"""Cấp tài khoản bác sĩ. Đây là đường DUY NHẤT tạo được vai trò 'doctor'.

    uv run python scripts/create_clinician.py --email bs.an@benhvien.vn --name "BS. Nguyễn Văn An"

Vì sao là script CLI chứ không phải một endpoint:
    Vai trò bác sĩ là quyền chốt nhãn lâm sàng, và nhãn đó vừa hiển thị cho phụ huynh
    vừa chui vào tập huấn luyện mô hình. Nếu cấp được qua HTTP thì bề mặt tấn công của
    đặc quyền cao nhất hệ thống chính là bề mặt Internet. Là script thì muốn chạy phải
    đã đứng trên máy chủ và đã có quyền ghi database — tức là đã vượt qua những lớp bảo
    vệ mạnh hơn bất cứ thứ gì ta tự viết được ở tầng ứng dụng.

    Hệ quả kèm theo: không tồn tại endpoint nào để leo thang đặc quyền, vì không tồn tại
    endpoint nào ghi vào cột role.

Mật khẩu:
    Nhập qua getpass (không hiện lên màn hình, không vào lịch sử shell). Cố ý KHÔNG có
    tuỳ chọn --password: mật khẩu truyền qua tham số dòng lệnh sẽ nằm lại trong
    ~/.bash_history và hiện ra với mọi tiến trình khác qua danh sách tiến trình.
"""
import argparse
import getpass
import os
import sys

# Chạy được từ bất kỳ thư mục nào: đưa thư mục gốc dự án vào sys.path để import được
# setup_database và app; database luôn tính từ gốc dự án (setup_database.duong_dan_du_an).
GOC_DU_AN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GOC_DU_AN not in sys.path:
    sys.path.insert(0, GOC_DU_AN)

from sqlalchemy.exc import IntegrityError  # noqa: E402

from setup_database import SessionLocal, User, init_db  # noqa: E402
from app.services import security  # noqa: E402

ROLE_DOCTOR = "doctor"


def main() -> int:
    parser = argparse.ArgumentParser(description="Cap tai khoan bac si (role=doctor).")
    parser.add_argument("--email", required=True, help="Email dang nhap cua bac si")
    parser.add_argument("--name", required=True, help="Ho ten hien thi, se ky vao ho so benh an")
    args = parser.parse_args()

    email = args.email.strip().lower()
    full_name = args.name.strip()

    if not email or "@" not in email:
        print("Email khong hop le.", file=sys.stderr)
        return 1
    if not full_name:
        print("Ho ten khong duoc de trong.", file=sys.stderr)
        return 1

    password = getpass.getpass("Mat khau moi: ")
    if len(password) < security.MIN_PASSWORD_LENGTH:
        print(f"Mat khau phai co toi thieu {security.MIN_PASSWORD_LENGTH} ky tu.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Nhap lai mat khau: "):
        print("Hai lan nhap khong khop.", file=sys.stderr)
        return 1

    # init_db chứ không phải create_all: create_all bỏ qua bảng đã tồn tại, nên trên một
    # database cũ, lệnh INSERT bên dưới sẽ nổ "no such column" với các cột mới của users.
    init_db()

    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash=security.hash_password(password),
            full_name=full_name,
            role=ROLE_DOCTOR,
            is_active=True,
        )
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
        except IntegrityError:
            db.rollback()
            print(f"Email '{email}' da duoc dang ky.", file=sys.stderr)
            return 1

        print(f"Da tao tai khoan bac si: id={user.id} email={user.email} name={user.full_name}")
        # Ho ten in ra o day chinh la chuoi se duoc ghi vao truong clinician_name cua moi
        # ket luan nguoi nay chot. Sai chinh ta o buoc nay se nam lai vinh vien trong ho so.
        print("Luu y: ho ten tren se duoc ky vao moi ket luan lam sang cua tai khoan nay.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
