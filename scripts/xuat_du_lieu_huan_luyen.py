"""Xuất tập dữ liệu huấn luyện cho phần mô hình. Chạy trên máy chủ, không phải endpoint.

    KHOA_MA_AN_DANH_DU_LIEU=<khoa co dinh> python scripts/xuat_du_lieu_huan_luyen.py --ra data/exports/v1

Vì sao là script CLI chứ không phải endpoint HTTP:
    Đây là thao tác đưa ảnh của nhiều đứa trẻ ra khỏi hệ thống cùng một lúc. Đặt nó sau một
    endpoint là đặt toàn bộ tập dữ liệu sau một lớp phân quyền tự viết. Là script thì muốn
    chạy phải đứng trên máy chủ và có quyền đọc database — giống create_clinician.py.

Kết quả:
    <thu_muc_ra>/metadata.csv   cột xem xuat_du_lieu.COT_XUAT
    <thu_muc_ra>/images/        ảnh, tên file là mã ẩn danh của ca
    data/nhat_ky_xuat/xuat_<thoi_diem>.csv   NHẬT KÝ NỘI BỘ: record_id nào đã đi ra trong lần này.
        Không nằm trong thư mục xuất, không bao giờ gửi đi. Cần để trả lời được câu "ảnh này
        có nằm trong lần xuất nào không" khi một gia đình rút đồng thuận sau đó.

Điều kiện chọn ca nằm hết ở app/services/xuat_du_lieu.py. Script này chỉ lo ghi file.
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from io import BytesIO

# Chạy được từ bất kỳ thư mục nào: đưa thư mục gốc dự án vào sys.path để import được
# setup_database và app (import bên trong xuat()); database, thư mục ảnh và nhật ký xuất luôn
# tính từ gốc dự án. Riêng --ra là tham số dòng lệnh nên theo thư mục đang đứng, như mọi CLI.
GOC_DU_AN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GOC_DU_AN not in sys.path:
    sys.path.insert(0, GOC_DU_AN)

from dotenv import load_dotenv  # noqa: E402

# Tự nạp .env: hằng số này được đọc lúc import, còn setup_database (nơi đầu tiên nạp .env)
# chỉ được import bên trong xuat(). Không tự nạp thì THU_MUC_NHAT_KY_XUAT trong .env bị bỏ
# qua âm thầm và nhật ký nằm ở thư mục mặc định — đúng thứ cần tìm lại khi gia đình rút đồng thuận.
load_dotenv()
# Đường dẫn tương đối tính từ gốc dự án (giống setup_database.duong_dan_du_an), không từ thư mục
# đang đứng: nhật ký phải luôn nằm một chỗ để còn tìm lại được.
THU_MUC_NHAT_KY = os.path.normpath(os.path.join(GOC_DU_AN, os.getenv("THU_MUC_NHAT_KY_XUAT", "data/nhat_ky_xuat")))


class ThuMucKhongTrong(RuntimeError):
    """Không ghi đè hay trộn vào một lần xuất cũ: hai phiên bản dữ liệu lẫn vào nhau thì
    không còn biết mô hình đã học trên tập nào."""


def _nam_trong(thu_muc: str, duong_dan: str) -> bool:
    goc = os.path.realpath(thu_muc)
    that = os.path.realpath(duong_dan or "")
    try:
        return os.path.commonpath([goc, that]) == goc
    except ValueError:
        return False


def xuat(thu_muc_ra: str, db=None, thu_muc_nhat_ky: str = None) -> dict:
    """Xuất dữ liệu. Trả về bản tóm tắt. Không có khoá ẩn danh thì dừng trước khi ghi gì."""
    from PIL import Image

    from setup_database import SessionLocal
    from app.api.records import UPLOAD_DIR, duong_dan_anh_that
    from app.services import lam_sach_anh, xuat_du_lieu

    khoa = xuat_du_lieu.lay_khoa()      # thiếu khoá thì nổ ở đây, chưa chạm vào file nào

    if os.path.isdir(thu_muc_ra) and os.listdir(thu_muc_ra):
        raise ThuMucKhongTrong(f"Thu muc '{thu_muc_ra}' da co du lieu. Chon mot thu muc moi.")

    tu_mo = db is None
    if tu_mo:
        db = SessionLocal()
    try:
        cac_ca = xuat_du_lieu.truy_van_ca_duoc_phep(db)
    finally:
        if tu_mo:
            db.close()

    thu_muc_anh = os.path.join(thu_muc_ra, "images")
    os.makedirs(thu_muc_anh, exist_ok=True)

    dong_csv, nhat_ky = [], []
    tom_tat = {"da_xuat": 0, "bo_qua_thieu_file": 0, "bo_qua_ngoai_thu_muc": 0}

    for ca in cac_ca:
        # Cùng cách tính đường dẫn ảnh với endpoint trả ảnh: kết quả xuất không phụ thuộc thư
        # mục đang đứng, kể cả với ca cũ lưu image_path tương đối.
        duong_anh = duong_dan_anh_that(ca["image_path"])
        # Cùng chốt với endpoint trả ảnh: image_path nằm trong database, và một dòng bị sửa
        # không được phép lôi một file bất kỳ trên máy chủ vào tập dữ liệu gửi đi.
        if not _nam_trong(UPLOAD_DIR, duong_anh):
            tom_tat["bo_qua_ngoai_thu_muc"] += 1
            continue
        if not os.path.isfile(duong_anh):
            tom_tat["bo_qua_thieu_file"] += 1
            continue

        with open(duong_anh, "rb") as f:
            du_lieu = f.read()
        # Ảnh upload đã được làm sạch, nhưng ảnh cũ hay ảnh khôi phục từ sao lưu thì chưa
        # chắc. Tập dữ liệu rời khỏi hệ thống nên phải kiểm lại, không tin giả định.
        with Image.open(BytesIO(du_lieu)) as anh:
            if lam_sach_anh.con_sieu_du_lieu(anh):
                du_lieu = lam_sach_anh.lam_sach(du_lieu)

        ma_anh = xuat_du_lieu.ma_an_danh("anh", ca["record_id"], khoa)
        duoi = os.path.splitext(duong_anh)[1].lower()
        ten_file = f"{ma_anh}{duoi}"
        with open(os.path.join(thu_muc_anh, ten_file), "wb") as f:
            f.write(du_lieu)

        dong_csv.append({
            "image_path": f"images/{ten_file}",
            "patient_id": xuat_du_lieu.ma_an_danh("be", ca["child_id"], khoa),
            "nhan_bitss": ca["nhan_bitss"],
            "thang_tuoi": ca["thang_tuoi"],
            "che_do_an": ca["che_do_an"],
        })
        nhat_ky.append({"record_id": ca["record_id"], "ma_anh": ma_anh})
        tom_tat["da_xuat"] += 1

    with open(os.path.join(thu_muc_ra, "metadata.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(xuat_du_lieu.COT_XUAT))
        w.writeheader()
        w.writerows(dong_csv)

    thu_muc_nhat_ky = thu_muc_nhat_ky or THU_MUC_NHAT_KY
    os.makedirs(thu_muc_nhat_ky, exist_ok=True)
    moc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    duong_nhat_ky = os.path.join(thu_muc_nhat_ky, f"xuat_{moc}.csv")
    with open(duong_nhat_ky, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["record_id", "ma_anh"])
        w.writeheader()
        w.writerows(nhat_ky)

    tom_tat["nhat_ky"] = duong_nhat_ky
    return tom_tat


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="Xuat tap du lieu huan luyen (chi ca du dieu kien).")
    ap.add_argument("--ra", required=True, help="Thu muc dich, phai chua ton tai hoac dang rong")
    args = ap.parse_args()

    from setup_database import kiem_tra_schema
    lech = kiem_tra_schema()
    if lech:
        print("Database chua khop schema, chay 'python setup_database.py' truoc:", "; ".join(lech))
        return 1

    tom_tat = xuat(args.ra)
    print(f"Da xuat {tom_tat['da_xuat']} ca vao {args.ra}")
    if tom_tat["bo_qua_thieu_file"] or tom_tat["bo_qua_ngoai_thu_muc"]:
        print(f"Bo qua: {tom_tat['bo_qua_thieu_file']} ca thieu file anh, "
              f"{tom_tat['bo_qua_ngoai_thu_muc']} ca co duong dan nam ngoai thu muc upload")
    print(f"Nhat ky noi bo (KHONG gui di): {tom_tat['nhat_ky']}")
    print("LUU Y: cot nhan la 'nhan_bitss' (BITSS 1-4), khong phai 'label' (Bristol 0-6) ma "
          "loader cua phan mo hinh dang doc. Hai ben can chot so lop truoc khi huan luyen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
