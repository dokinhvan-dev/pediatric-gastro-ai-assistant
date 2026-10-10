"""Worker suy luận: nối mô hình phân loại ảnh với backend qua các endpoint dành cho worker.

Backend không tự chạy mô hình (records.submit_inference_result). Worker này là tiến trình
riêng, xác thực bằng X-Service-Token, và làm đúng một vòng cho mỗi ca:

    GET  /records/worker/hang-cho        lấy các ca đang queued, cũ nhất trước
    POST /records/{id}/inference-start   queued -> processing (409: worker khác đã nhận, bỏ qua)
    GET  /records/{id}/worker/anh        tải ảnh (đã làm sạch siêu dữ liệu)
    (chạy mô hình: 7 xác suất Type_1..Type_7 -> quy_doi_nhan.gop_xac_suat -> nhóm BITSS 1-4)
    POST /records/{id}/inference-result  processing -> completed (backend tự gắn cờ độ tự tin)
    POST /records/{id}/inference-failed  khi ảnh hỏng, mô hình lỗi hay đầu ra không hợp lệ

Hai loại mô hình:
    --mo-hinh tien   nạp checkpoint của phần mô hình ảnh (repo bitss_stool_classification,
                     chỉ ĐỌC repo đó). Cần cài PyTorch, torchvision, PyYAML, pandas
                     (không nằm trong requirements.txt của backend).
    --mo-hinh gia    trả một phân phối cố định, để thử luồng khi chưa có checkpoint.

Chạy (máy chủ phải đang chạy, INFERENCE_SERVICE_TOKEN trong .env giống của máy chủ):
    uv run python scripts/worker_suy_luan.py --mo-hinh gia --mot-lan
    uv run python scripts/worker_suy_luan.py --mo-hinh tien \\
        --repo-mo-hinh D:/bitss_stool_classification-main \\
        --checkpoint D:/bitss_stool_classification-main/outputs/checkpoints/best.pt
"""
import argparse
import logging
import os
import sys
import time
from io import BytesIO
from typing import Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from app.services import quy_doi_nhan  # noqa: E402

logger = logging.getLogger("worker_suy_luan")

TIEN_TO_API = "/api/v1/records"
# Lý do thất bại ghi vào inference_error: đủ để người vận hành biết chuyện gì, nhưng cắt
# ngắn để một traceback dài không tràn vào cơ sở dữ liệu.
DO_DAI_LY_DO_TOI_DA = 300
CLASS_NAMES_CAN_CO = [f"Type_{i}" for i in range(1, quy_doi_nhan.SO_LOAI_BSFS + 1)]


class BoPhanLoaiGia:
    """Mô hình giả: luôn trả cùng một phân phối 7 loại. Chỉ để thử luồng."""

    MAC_DINH = (0.02, 0.03, 0.05, 0.80, 0.05, 0.03, 0.02)   # chủ yếu Type_4 -> nhóm 2

    def __init__(self, xac_suat: Optional[Sequence[float]] = None):
        self.xac_suat = list(xac_suat if xac_suat is not None else self.MAC_DINH)

    def du_doan(self, anh: Image.Image) -> list:
        return list(self.xac_suat)


class BoPhanLoaiTien:
    """Nạp checkpoint huấn luyện bằng src/train.py của repo mô hình ảnh.

    Dùng lại đúng build_model và get_transforms(split="test") của repo đó, để ảnh lúc suy
    luận được tiền xử lý giống hệt lúc đánh giá (resize 224, chuẩn hoá ImageNet). Cấu hình
    lấy từ chính checkpoint (khoá "config"), nên không lệch với lúc huấn luyện.
    """

    def __init__(self, repo_mo_hinh: str, checkpoint: str):
        try:
            import torch
        except ImportError as e:
            raise SystemExit(
                "Chưa cài PyTorch. Cài torch, torchvision, pyyaml, pandas (theo requirements.txt "
                "của repo mô hình ảnh) vào môi trường chạy worker."
            ) from e
        thu_muc_src = os.path.join(repo_mo_hinh, "src")
        if not os.path.isfile(os.path.join(thu_muc_src, "models.py")):
            raise SystemExit(f"Không thấy {thu_muc_src}/models.py: kiểm tra lại --repo-mo-hinh")
        sys.path.insert(0, thu_muc_src)
        from dataset import get_transforms  # repo mô hình ảnh
        from models import build_model

        trang_thai = torch.load(checkpoint, map_location="cpu")
        cfg = trang_thai["config"]
        ten_lop = list(cfg["data"]["class_names"])
        # Thứ tự lớp là hợp đồng giữa hai phần: nhãn i của mô hình phải là Type_{i+1}
        # (docs/data_contract.md của repo mô hình). Sai thứ tự thì quy đổi sai nhóm mà không
        # có lỗi nào hiện ra, nên dừng ngay.
        if ten_lop != CLASS_NAMES_CAN_CO:
            raise SystemExit(f"class_names của checkpoint là {ten_lop}, cần đúng {CLASS_NAMES_CAN_CO}")
        cfg_mo_hinh = dict(cfg["model"], pretrained=False)
        self.mo_hinh = build_model(cfg_mo_hinh, cfg["data"]["num_classes"])
        self.mo_hinh.load_state_dict(trang_thai["model_state_dict"])
        self.mo_hinh.eval()
        self.bien_doi = get_transforms(cfg["data"]["image_size"], "test", cfg["augmentation"])
        self._torch = torch
        logger.info("Đã nạp checkpoint %s (epoch %s)", checkpoint, trang_thai.get("epoch"))

    def du_doan(self, anh: Image.Image) -> list:
        x = self.bien_doi(anh.convert("RGB")).unsqueeze(0)
        with self._torch.no_grad():
            xac_suat = self._torch.softmax(self.mo_hinh(x), dim=1)[0]
        return [float(v) for v in xac_suat]


class LoiBackend(RuntimeError):
    """Backend trả mã không mong đợi: dừng vòng hiện tại, không đánh dấu ca thất bại."""


def _ly_do(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"[:DO_DAI_LY_DO_TOI_DA]


def xu_ly_mot_ca(client, record_id: int, bo_phan_loai) -> str:
    """Chạy trọn một ca. Trả 'xong', 'bo_qua' (worker khác đã nhận) hoặc 'that_bai'.

    `client` là bất kỳ đối tượng nào có .get/.post kiểu httpx (httpx.Client có base_url và
    header token, hoặc TestClient trong bộ kiểm tra).
    """
    r = client.post(f"{TIEN_TO_API}/{record_id}/inference-start")
    if r.status_code == 409:
        return "bo_qua"
    if r.status_code != 200:
        raise LoiBackend(f"inference-start ca {record_id}: {r.status_code} {r.text[:200]}")

    # Từ đây ca đã ở processing do chính worker này nhận: mọi lỗi phía ảnh hay mô hình phải
    # được báo về bằng inference-failed, nếu không ca sẽ kẹt ở processing mãi.
    try:
        r = client.get(f"{TIEN_TO_API}/{record_id}/worker/anh")
        if r.status_code != 200:
            raise ValueError(f"không tải được ảnh: {r.status_code}")
        with Image.open(BytesIO(r.content)) as anh:
            anh.load()
            xac_suat = bo_phan_loai.du_doan(anh)
        kq = quy_doi_nhan.gop_xac_suat(xac_suat)
    except Exception as e:
        logger.warning("ca %s: suy luận thất bại: %s", record_id, _ly_do(e))
        r = client.post(f"{TIEN_TO_API}/{record_id}/inference-failed", json={"error": _ly_do(e)})
        if r.status_code != 200:
            raise LoiBackend(f"inference-failed ca {record_id}: {r.status_code} {r.text[:200]}")
        return "that_bai"

    r = client.post(
        f"{TIEN_TO_API}/{record_id}/inference-result",
        json={"ai_predicted_class": kq["nhom"], "ai_confidence": kq["do_tin_cay"]},
    )
    if r.status_code != 200:
        raise LoiBackend(f"inference-result ca {record_id}: {r.status_code} {r.text[:200]}")
    logger.info("ca %s: nhóm %s, độ tin cậy %.3f", record_id, kq["nhom"], kq["do_tin_cay"])
    return "xong"


def chay_mot_vong(client, bo_phan_loai, so_ca: int = 10) -> dict:
    """Lấy tối đa `so_ca` ca đang chờ và xử lý lần lượt. Trả số ca theo kết quả."""
    r = client.get(f"{TIEN_TO_API}/worker/hang-cho", params={"limit": so_ca})
    if r.status_code != 200:
        raise LoiBackend(f"hang-cho: {r.status_code} {r.text[:200]}")
    dem = {"xong": 0, "bo_qua": 0, "that_bai": 0}
    for ca in r.json()["data"]:
        dem[xu_ly_mot_ca(client, ca["record_id"], bo_phan_loai)] += 1
    return dem


def main():
    # Console Windows mặc định cp1252 không in được tiếng Việt (trợ giúp, log, thông báo lỗi).
    for luong in (sys.stdout, sys.stderr):
        try:
            luong.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="Worker suy luận cho backend BITSS")
    parser.add_argument("--mo-hinh", choices=["tien", "gia"], required=True)
    parser.add_argument("--repo-mo-hinh", default=None, help="Thư mục repo mô hình ảnh (--mo-hinh tien)")
    parser.add_argument("--checkpoint", default=None, help="Tệp checkpoint .pt (--mo-hinh tien)")
    parser.add_argument("--url", default=os.getenv("BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--so-ca", type=int, default=10, help="Số ca tối đa mỗi vòng")
    parser.add_argument("--chu-ky", type=float, default=30.0, help="Số giây nghỉ giữa hai vòng")
    parser.add_argument("--mot-lan", action="store_true", help="Chạy một vòng rồi thoát")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    token = os.getenv("INFERENCE_SERVICE_TOKEN", "")
    if not token:
        raise SystemExit("Chưa đặt INFERENCE_SERVICE_TOKEN (trong .env hoặc biến môi trường).")

    if args.mo_hinh == "tien":
        if not args.repo_mo_hinh or not args.checkpoint:
            raise SystemExit("--mo-hinh tien cần --repo-mo-hinh và --checkpoint")
        bo_phan_loai = BoPhanLoaiTien(args.repo_mo_hinh, args.checkpoint)
    else:
        bo_phan_loai = BoPhanLoaiGia()

    import httpx
    with httpx.Client(base_url=args.url, headers={"X-Service-Token": token}, timeout=60.0) as client:
        while True:
            try:
                dem = chay_mot_vong(client, bo_phan_loai, args.so_ca)
                logger.info("vòng xong: %s", dem)
            except (LoiBackend, httpx.HTTPError) as e:
                logger.error("lỗi khi gọi backend: %s", e)
                if args.mot_lan:
                    raise SystemExit(1)
            if args.mot_lan:
                break
            time.sleep(args.chu_ky)


if __name__ == "__main__":
    main()
