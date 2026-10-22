"""Kiểm thử đột biến cho bộ kiểm tra tests/verify_task.py.

Chạy:  uv run python danh_gia/kiem_thu_dot_bien.py          (mất vài phút, chạy song song 4 tiến trình)

Mỗi đột biến là một lỗi nhỏ cố ý chèn vào MỘT BẢN SAO của mã (thư mục tạm, xoá khi xong); repo không bị
sửa. Với mỗi đột biến, script chạy lại toàn bộ bộ kiểm tra trên bản sao. Đột biến "bị bắt" khi bộ kiểm tra
chạy đủ và có kiểm tra chức năng trượt. Crash, timeout hoặc mất kiểm tra là lỗi thí nghiệm, không tính là bắt.
Không gọi Gemini
(bộ kiểm tra tự đặt khoá API rỗng), không đụng data/ thật.

Danh sách gồm 59 đột biến: D01–D19 dựng ngày 2026-10-01, rải trên các thành phần chính, gồm bốn đột biến nhắm
thẳng vào bất biến B1–B4; D20–D25 dựng ngày 2026-10-05, mỗi đột biến đảo ngược một bản sửa lỗi sau đợt đo;
D26 dựng ngày 2026-10-08 cho bản sửa ghi chú JPEG còn sót sau bước làm sạch ảnh; D27–D30 dựng ngày 2026-10-10
cho phần nối mô hình ảnh (quy đổi nhãn 7 loại sang 4 nhóm, hàng chờ và ảnh cho worker); D31–D33 dựng ngày
2026-10-16 cho việc tách yếu tố lâm sàng thành bảng child_yeu_to. Nhánh security, cùng ngày: D34–D37 cho đợt
tăng cường bảo mật 1 (log nhánh lỗi của chat, tắt tài liệu API, header bảo mật, timeout Gemini); D38–D46, mỗi
đột biến gỡ một chốt phân quyền theo đối tượng mà ma trận route × vai (mục 44) phải bắt; D47–D53 cho nhật ký
kiểm toán (mục 45): bỏ ghi ở từng loại sự kiện, bỏ trigger chỉ-ghi-thêm, trợ lý vẫn trả dữ liệu khi không ghi
được nhật ký, không ghi IP; D54–D59 cho token dịch vụ riêng từng worker (mục 46). D07 và D15 giữ nguyên ý nghĩa
nhưng nay nhắm vào verify_service_token mới (so băm bí mật của token worker thay cho bí mật dùng chung).
D07 và D12 tương đương trong phạm vi kiểm tra chức năng trên dữ liệu hợp lệ; D07 vẫn đổi đặc tính thời
gian so sánh. Chúng được giữ lại với lý do riêng để thấy giới hạn của phương pháp.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHEP = ["app", "tests", "scripts", "danh_gia", "main.py", "setup_database.py", "pyproject.toml", "uv.lock"]

# (nhãn, tệp, đoạn gốc, đoạn thay)
DOT_BIEN = [
    ("D01 máy trạng thái: bỏ kiểm tra chuyển hợp lệ", "app/api/records.py",
     "    if target not in allowed:\n", "    if False and target not in allowed:\n"),
    ("D02 ai_confidence: cận trên 1,0 thành 2,0", "app/api/records.py",
     "if not (0.0 <= payload.ai_confidence <= 1.0):", "if not (0.0 <= payload.ai_confidence <= 2.0):"),
    ("D03 ngưỡng độ tự tin: < thành <=", "app/services/uncertainty.py",
     "    return confidence < CONFIDENCE_THRESHOLD", "    return confidence <= CONFIDENCE_THRESHOLD"),
    ("D04 cửa sổ trượt: t > mốc cũ thành >=", "app/services/rate_limit.py",
     "if t > moc_cu]", "if t >= moc_cu]"),
    ("D05 cửa sổ trượt: >= hạn mức thành >", "app/services/rate_limit.py",
     "if len(moc) >= han_muc:", "if len(moc) > han_muc:"),
    ("D06 cửa sổ trượt: ghi cả lượt bị từ chối", "app/services/rate_limit.py",
     "            cho = moc[0] + cua_so - bay_gio", "            moc.append(bay_gio)\n            cho = moc[0] + cua_so - bay_gio"),
    ("D07 so sánh token: compare_digest thành == (tương đương)", "app/services/security.py",
     "hmac.compare_digest(bam_bi_mat_worker(bi_mat), dong.token_hash)",
     "bam_bi_mat_worker(bi_mat) == dong.token_hash"),
    ("D08 thử lại mọi ClientError", "app/services/agent_service.py",
     "        return exc.code == 429", "        return True"),
    ("D09 giờ mở lại hạn mức ngày: +1 ngày thành +2", "app/services/agent_service.py",
     "(dia_phuong + timedelta(days=1))", "(dia_phuong + timedelta(days=2))"),
    ("D10 bỏ dấu: không đổi đ thành d", "app/services/agent_service.py",
     'unicodedata.category(c) != "Mn").replace("đ", "d")', 'unicodedata.category(c) != "Mn")'),
    ("D11 mã ẩn danh: 20 ký tự thành 10", "app/services/xuat_du_lieu.py",
     ".hexdigest()[:20]", ".hexdigest()[:10]"),
    ("D12 authz: bỏ nhánh child_id None (tương đương)", "app/services/authz.py",
     "        if record.child_id is None:\n            return False\n", "        if False:\n            return False\n"),
    ("D13 xu hướng: mức ý nghĩa 0,05 thành 0,5", "app/services/records_query.py",
     "MUC_Y_NGHIA = 0.05", "MUC_Y_NGHIA = 0.5"),
    ("D14 dọn sổ đếm: cửa sổ dài nhất thành ngắn nhất", "app/services/rate_limit.py",
     "cua_so_dai_nhat = max((c for _, _, c in HAN_MUC.values()), default=CUA_SO_MAC_DINH)",
     "cua_so_dai_nhat = min((c for _, _, c in HAN_MUC.values()), default=CUA_SO_MAC_DINH)"),
    ("D15 token dịch vụ: bỏ kiểm 'không có token'", "app/services/security.py",
     "    if not presented:\n        return None\n    phan = tach_token_worker(presented)",
     "    phan = tach_token_worker(presented)"),
    ("D16 B1: phụ huynh xem được hồ sơ mọi bé", "app/services/authz.py",
     "return child.owner_user_id == principal.id", "return True"),
    ("D17 B2: bộ lọc nhãn không căn cứ luôn rỗng", "app/services/agent_service.py",
     "    return nhan_bitss_trong(cau_tra_loi) - set(nhan_da_thay)", "    return set()"),
    ("D18 B3: bộ dò phán quyết phân luồng luôn rỗng", "app/services/agent_service.py",
     "    return _chua_cum_nao(cau_tra_loi, CUM_TU_PHAN_LUONG)", "    return set()"),
    ("D19 B4: lưới an toàn không thêm lời dặn đi khám", "app/services/agent_service.py",
     "    return cau_tra_loi + _dung_luoi_an_toan(khop)", "    return cau_tra_loi"),
    ("D20 khớp từ khoá: bỏ chuẩn hoá NFC", "app/services/agent_service.py",
     'thap = unicodedata.normalize("NFC", tin_nhan or "").lower()', 'thap = (tin_nhan or "").lower()'),
    ("D21 xu hướng: tối thiểu 7 ca thành 4", "app/services/records_query.py",
     "TOI_THIEU_CA_CO_NHAN = 7", "TOI_THIEU_CA_CO_NHAN = 4"),
    ("D22 ghi kết quả suy luận: bỏ điều kiện trạng thái trong UPDATE", "app/api/records.py",
     ".filter(StoolRecord.id == record.id, StoolRecord.inference_status == da_doc)",
     ".filter(StoolRecord.id == record.id)"),
    ("D23 chế độ ăn: bỏ kiểm từ vựng", "app/api/children.py",
     "    if che_do_an is None:\n", "    if False:\n"),
    ("D24 Gemini lỗi: không kèm cảnh báo dấu hiệu nguy hiểm", "app/api/chat.py",
     "return thong_bao + agent_service._dung_luoi_an_toan(khop) if khop else thong_bao", "return thong_bao"),
    ("D25 điều phối công cụ: bỏ kiểm chữ ký trước khi gọi", "app/services/agent_service.py",
     "        inspect.signature(ham).bind(**args)\n", "        pass\n"),
    ("D26 làm sạch ảnh: không bỏ info (ghi chú JPEG còn sót)", "app/services/lam_sach_anh.py",
     "    anh.info = {}\n", "    pass\n"),
    ("D27 quy đổi nhãn: lấy loại cao nhất thay vì cộng theo nhóm", "app/services/quy_doi_nhan.py",
     "        xac_suat_nhom[BSFS_SANG_NHOM[i + 1]] += gia_tri\n",
     "        xac_suat_nhom[BSFS_SANG_NHOM[i + 1]] = max(xac_suat_nhom[BSFS_SANG_NHOM[i + 1]], gia_tri)\n"),
    ("D28 quy đổi nhãn: bỏ kiểm tổng xác suất bằng 1", "app/services/quy_doi_nhan.py",
     "    if abs(tong - 1.0) > DUNG_SAI_TONG:\n", "    if False:\n"),
    ("D29 hàng chờ worker: không loại ca của bé đã xoá mềm", "app/api/records.py",
     '.filter(StoolRecord.inference_status == "queued", Child.deleted_at.is_(None))',
     '.filter(StoolRecord.inference_status == "queued")'),
    ("D30 ảnh cho worker: bỏ giới hạn trạng thái queued/processing", "app/api/records.py",
     "        if record.inference_status not in TRANG_THAI_WORKER_DOC_ANH:\n", "        if False:\n"),
    ("D31 child_yeu_to: bỏ CHECK giới hạn mã trong từ vựng", "setup_database.py",
     '            "ma IN (" + ", ".join(f"\'{m}\'" for m in sorted(_MA_YEU_TO_HOP_LE)) + ")",\n',
     '            "1 = 1",\n'),
    ("D32 kiểm schema: không báo cột cũ chưa chuyển", "setup_database.py",
     "    for bang, cot, cho_moi in COT_DA_BO:\n", "    for bang, cot, cho_moi in []:\n"),
    ("D33 chuyển dữ liệu: không lọc mã ngoài từ vựng", "setup_database.py",
     "                if ma not in _MA_YEU_TO_HOP_LE:\n", "                if False:\n"),
    ("D34 chat lỗi không lường trước: hạ log exception xuống debug", "app/api/chat.py",
     'logging.getLogger("bitss.chat").exception(\n            "[%s] loi khong luong',
     'logging.getLogger("bitss.chat").debug(\n            "[%s] loi khong luong'),
    ("D35 tài liệu API: thiếu cấu hình thì bật", "main.py",
     '_GIA_TRI_BAT = {"1", "true", "yes", "on"}', '_GIA_TRI_BAT = {"", "1", "true", "yes", "on"}'),
    ("D36 header bảo mật: bỏ Cache-Control no-store cho /api/", "main.py",
     '    "Cache-Control": "no-store",\n', ""),
    ("D37 Gemini: gọi không kèm timeout", "app/services/agent_service.py",
     "config=_gan_timeout(config, han_chot)", "config=config"),
    ("D38 đồng thuận nghiên cứu: bỏ kiểm chủ hồ sơ", "app/api/records.py",
     "if not child or da_xoa_mem(child) or child.owner_user_id != principal.id:",
     "if not child or da_xoa_mem(child):"),
    ("D39 xoá hồ sơ bé: bỏ kiểm chủ hồ sơ", "app/api/children.py",
     "if not child or child.owner_user_id != user.id or da_xoa_mem(child):", "if not child or da_xoa_mem(child):"),
    ("D40 tải ảnh: bỏ kiểm chủ hồ sơ", "app/api/records.py",
     "        if child.owner_user_id != principal.id:\n", "        if False:\n"),
    ("D41 duyệt ca: không đòi vai bác sĩ", "app/api/doctor.py",
     "Depends(require_doctor)", "Depends(get_current_user)"),
    ("D42 danh sách duyệt của ca: bỏ kiểm quyền xem", "app/api/doctor.py",
     '{record_id}")\n\n        assert_can_view_record(db, record, principal)\n', '{record_id}")\n\n'),
    ("D43 ảnh của ca: bỏ kiểm quyền xem", "app/api/records.py",
     '{record_id}")\n        assert_can_view_record(db, record, principal)\n', '{record_id}")\n'),
    ("D44 danh sách bé: bỏ lọc theo chủ hồ sơ", "app/api/children.py",
     ".filter(Child.owner_user_id == user.id, Child.deleted_at.is_(None))", ".filter(Child.deleted_at.is_(None))"),
    ("D45 phạm vi danh sách ca: phụ huynh thấy bé của mọi nhà", "app/services/authz.py",
     ".filter(Child.owner_user_id == principal.id, Child.deleted_at.is_(None))", ".filter(Child.deleted_at.is_(None))"),
    ("D46 không được xem ca: 404 thành 403 (lộ ca có tồn tại)", "app/api/deps.py",
     '            status_code=404,\n            detail=f"Không tìm thấy ca phân tích có ID: {record.id}",',
     '            status_code=403,\n            detail=f"Không tìm thấy ca phân tích có ID: {record.id}",'),
    ("D47 nhật ký: xem ca không ghi", "app/api/records.py",
     '        kiem_toan.ghi(db, principal, kiem_toan.XEM_CA, "record", record.id)\n', ""),
    ("D48 nhật ký: chốt nhãn không ghi", "app/api/doctor.py",
     '            kiem_toan.ghi(db, doctor, kiem_toan.DUYET_CA, "record", record.id, chi_tiet=payload.decision)\n', ""),
    ("D49 nhật ký: lượt bị từ chối không ghi", "app/api/deps.py",
     '        kiem_toan.ghi_rieng(principal, kiem_toan.TRUY_CAP_BI_TU_CHOI, "record", record.id,\n'
     '                            ket_qua=kiem_toan.BI_TU_CHOI)\n', ""),
    ("D50 nhật ký: không tạo trigger chỉ-ghi-thêm", "setup_database.py",
     '    if connection.dialect.name == "sqlite":\n        for cau_lenh in TRIGGER_AUDIT_CHI_GHI_THEM:',
     '    if False:\n        for cau_lenh in TRIGGER_AUDIT_CHI_GHI_THEM:'),
    ("D51 nhật ký: trợ lý ghi hỏng vẫn trả kết quả", "app/services/agent_service.py",
     '        logger.exception("[%s] khong ghi duoc nhat ky kiem toan cho %s - khong tra ket qua", nhan, ten_cong_cu)\n'
     '        return {"error": f"Không thực hiện được \'{ten_cong_cu}\' lúc này."}\n', "        pass\n"),
    ("D52 nhật ký: bước chuyển của worker không ghi", "app/api/records.py",
     '    kiem_toan.ghi(db, worker, _HANH_DONG_THEO_DICH[target], "record", record.id,\n'
     '                  chi_tiet=f"{da_doc}->{target}")\n', ""),
    ("D53 nhật ký: không ghi IP", "app/services/nhat_ky_kiem_toan.py",
     "ip=_ip_hien_tai.get(),", "ip=None,"),
    ("D54 token worker: bỏ kiểm hạn dùng", "app/services/security.py",
     "    if dong.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):\n        return None\n", ""),
    ("D55 token worker: bỏ kiểm thu hồi", "app/services/security.py",
     "    if dong.revoked_at is not None:\n        return None\n", ""),
    ("D56 token worker: đúng id là đủ, không so bí mật", "app/services/security.py",
     "    if not hmac.compare_digest(bam_bi_mat_worker(bi_mat), dong.token_hash):\n        return None\n", ""),
    ("D57 nhật ký: không ghi worker_id", "app/services/nhat_ky_kiem_toan.py",
     "return None, VAI_WORKER, principal.id", "return None, VAI_WORKER, None"),
    ("D58 token worker: bỏ trần hạn dùng 365 ngày", "app/services/token_worker.py",
     "if not 1 <= so_ngay <= SO_NGAY_TOI_DA:", "if not 1 <= so_ngay:"),
    ("D59 token worker: thu hồi không ghi nhật ký", "app/services/token_worker.py",
     '    kiem_toan.ghi(db, kiem_toan.VAN_HANH, kiem_toan.THU_HOI_TOKEN_WORKER, "worker_token", dong.id,\n'
     "                  chi_tiet=dong.ten)\n", ""),
]


TUONG_DUONG = {
    "D07": "Tương đương với kiểm tra chức năng; KHÔNG tương đương về thời gian so sánh token.",
    "D12": "Tương đương trên bản ghi hợp lệ: stool_records.child_id có ràng buộc NOT NULL.",
}


def ban_sao(dich):
    os.makedirs(dich)
    for x in CHEP:
        nguon = os.path.join(GOC, x)
        if os.path.isdir(nguon):
            shutil.copytree(nguon, os.path.join(dich, x), ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(nguon, dich)
    os.makedirs(os.path.join(dich, "data"))


def doc_ket_qua(stdout, returncode):
    """Chỉ nhận tổng kết đầy đủ, số dòng và mã thoát khớp với nhau."""
    tong = re.findall(r"^(\d+)/(\d+) PASS[ \t]*\r?$", stdout, re.M)
    dong = re.findall(r"^(PASS|FAIL): (.+)$", stdout, re.M)
    ket = {"returncode": returncode, "passed": None, "total": None,
           "checks": [ten.rstrip("\r") for _, ten in dong],
           "failures": [ten.rstrip("\r") for status, ten in dong if status == "FAIL"],
           "error": None}
    if len(tong) != 1:
        ket["error"] = "Thiếu hoặc có nhiều tổng kết đầy đủ (có thể dừng giữa chừng)."
        return ket
    dat, so = map(int, tong[0])
    ket.update(passed=dat, total=so)
    if (so <= 0 or dat > so or len(dong) != so
            or sum(status == "PASS" for status, _ in dong) != dat):
        ket["error"] = "Số kiểm tra/tổng kết không khớp."
    elif returncode != (0 if dat == so else 1):
        ket["error"] = "Mã thoát không khớp tổng kết kiểm tra."
    return ket


def _van_ban(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


def chay_kiem_tra(thu_muc, thu_muc_log, ma, timeout):
    stdout, stderr, code, loi = "", "", None, None
    try:
        p = subprocess.run(
            [sys.executable, "-B", os.path.join(thu_muc, "tests", "verify_task.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=thu_muc,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "GEMINI_API_KEY": ""},
        )
        stdout, stderr, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _van_ban(exc.stdout), _van_ban(exc.stderr)
        loi = f"Timeout sau {timeout} giây."
    except OSError as exc:
        loi = f"Không khởi chạy được kiểm tra: {type(exc).__name__}: {exc}"
    thu_muc_log = Path(thu_muc_log)
    out = thu_muc_log / f"{ma}.stdout.log"
    err = thu_muc_log / f"{ma}.stderr.log"
    out.write_text(stdout, encoding="utf-8")
    err.write_text(stderr, encoding="utf-8")
    ket = doc_ket_qua(stdout, code)
    if loi:
        ket["error"] = loi
    ket.update(stdout_log=out.name, stderr_log=err.name)
    return ket


def phan_loai(ma, lan_chay, goc):
    if lan_chay["error"]:
        return "error", lan_chay["error"]
    if Counter(lan_chay["checks"]) != Counter(goc["checks"]):
        return "error", "Danh sách kiểm tra thay đổi so với bản gốc."
    if lan_chay["failures"]:
        if ma in TUONG_DUONG:
            return "error", "Đột biến được miễn là tương đương đã đổi hành vi; phải duyệt lại lý do."
        return "killed", "Có kiểm tra chức năng trượt; bộ kiểm tra chạy đủ."
    if ma in TUONG_DUONG:
        return "equivalent", TUONG_DUONG[ma]
    return "survived", "Không có kiểm tra nào phát hiện đột biến."


def mot_dot_bien(args):
    (nhan, tep, goc, moi), snapshot, thu_muc_tam, logs, timeout, baseline = args
    ma = nhan.split()[0]
    ket = {"id": ma, "label": nhan, "file": tep, "status": "error", "reason": "", "run": None}
    try:
        d = Path(thu_muc_tam) / ma
        shutil.copytree(snapshot, d, ignore=shutil.ignore_patterns("__pycache__"))
        duong = d / tep
        nguon = duong.read_text(encoding="utf-8")
        if nguon.count(goc) != 1:
            ket["reason"] = f"Đoạn gốc xuất hiện {nguon.count(goc)} lần; cần cập nhật đột biến."
            return ket
        duong.write_text(nguon.replace(goc, moi), encoding="utf-8", newline="")
        ket["run"] = chay_kiem_tra(d, logs, ma, timeout)
        ket["status"], ket["reason"] = phan_loai(ma, ket["run"], baseline)
    except Exception as exc:
        # Lỗi dựng/chạy mutant phải hiện trong JSON và làm cổng thất bại.
        ket["reason"] = f"{type(exc).__name__}: {exc}"
    return ket


def ma_thoat(baseline, results):
    if baseline["error"] or baseline["failures"] or any(r["status"] == "error" for r in results):
        return 2
    return 1 if any(r["status"] == "survived" for r in results) else 0


def dau_van_tay(snapshot):
    files = {p.relative_to(snapshot).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(Path(snapshot).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


def _so_duong(value):
    so = int(value)
    if so <= 0:
        raise argparse.ArgumentTypeError("Phải lớn hơn 0.")
    return so


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chi", nargs="+", metavar="Dxx", help="Chỉ chạy các mã này (mặc định: tất cả).")
    parser.add_argument("--workers", type=_so_duong, default=4)
    parser.add_argument("--timeout", type=_so_duong, default=600, help="Giây cho mỗi lần chạy bộ kiểm tra.")
    parser.add_argument("--output-dir", help="Thư mục MỚI lưu JSON và log; không ghi đè đợt cũ.")
    args = parser.parse_args(argv)
    cac_ma = {d[0].split()[0] for d in DOT_BIEN}
    if args.chi and (set(args.chi) - cac_ma):
        parser.error("Mã đột biến không có: " + ", ".join(sorted(set(args.chi) - cac_ma)))
    chon = [d for d in DOT_BIEN if not args.chi or d[0].split()[0] in args.chi]
    bat_dau = datetime.now(timezone.utc)
    logs = Path(args.output_dir or os.path.join(
        GOC, "outputs", "logs", "dot_bien_" + bat_dau.strftime("%Y%m%dT%H%M%S%fZ"))).resolve()
    try:
        logs.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"Thư mục đã tồn tại, không ghi đè: {logs}")
    report = {"schema_version": 1, "started_at_utc": bat_dau.isoformat(),
              "selected": [d[0].split()[0] for d in chon], "baseline": None,
              "results": [], "source_sha256": None}
    with tempfile.TemporaryDirectory(prefix="kiem_thu_dot_bien_") as tam:
        snapshot = Path(tam) / "goc"
        ban_sao(snapshot)
        report["source_sha256"] = dau_van_tay(snapshot)
        baseline = chay_kiem_tra(snapshot, logs, "baseline", args.timeout)
        report["baseline"] = baseline
        print(f"Bản gốc: {baseline['passed']}/{baseline['total']} PASS; lỗi={baseline['error']}", flush=True)
        if not baseline["error"] and not baseline["failures"]:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
                pending = [ex.submit(mot_dot_bien, (d, snapshot, tam, logs, args.timeout, baseline))
                           for d in chon]
                for f in concurrent.futures.as_completed(pending):
                    r = f.result()
                    report["results"].append(r)
                    print(f"{r['id']} {r['status']}: {r['reason']}", flush=True)
        report["results"].sort(key=lambda r: r["id"])
        code = ma_thoat(baseline, report["results"])
    report.update(finished_at_utc=datetime.now(timezone.utc).isoformat(), exit_code=code,
                  counts=dict(Counter(r["status"] for r in report["results"])))
    (logs / "ket_qua.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"Tổng: {report['counts']}; exit={code}; báo cáo: {logs / 'ket_qua.json'}", flush=True)
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
