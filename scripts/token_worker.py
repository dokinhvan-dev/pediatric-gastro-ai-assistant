"""Cấp, thu hồi, liệt kê token dịch vụ riêng của từng worker suy luận.

    uv run python scripts/token_worker.py cap --ten gpu-01 [--so-ngay 90]
    uv run python scripts/token_worker.py thu-hoi --id 3
    uv run python scripts/token_worker.py liet-ke

Token in ra ĐÚNG MỘT LẦN khi cấp; database chỉ giữ SHA-256 của phần bí mật. Đặt token đó vào
INFERENCE_SERVICE_TOKEN trong môi trường của worker ấy (không phải của máy chủ). Mất token thì
thu hồi và cấp token mới, không có cách lấy lại.

Vì sao là script chứ không phải endpoint: cùng lý do với scripts/create_clinician.py — quyền
ghi kết quả suy luận không được cấp qua Internet. Muốn chạy phải đứng trên máy chủ và có quyền
ghi database. Mọi lần cấp và thu hồi đều vào nhật ký kiểm toán (vai 'van_hanh').
"""
import argparse
import os
import sys

# Chạy được từ bất kỳ thư mục nào: đưa thư mục gốc dự án vào sys.path để import được
# setup_database và app; database luôn tính từ gốc dự án (setup_database.duong_dan_du_an).
GOC_DU_AN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GOC_DU_AN not in sys.path:
    sys.path.insert(0, GOC_DU_AN)

from setup_database import SessionLocal, init_db  # noqa: E402
from app.services import token_worker  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Quan ly token dich vu cua worker suy luan.")
    lenh = parser.add_subparsers(dest="lenh", required=True)
    p_cap = lenh.add_parser("cap", help="Cap token moi cho mot worker.")
    p_cap.add_argument("--ten", required=True, help="Ten worker, vd. gpu-01.")
    p_cap.add_argument("--so-ngay", type=int, default=token_worker.SO_NGAY_MAC_DINH,
                       help=f"Han dung (1-{token_worker.SO_NGAY_TOI_DA} ngay).")
    p_thu = lenh.add_parser("thu-hoi", help="Thu hoi mot token theo id.")
    p_thu.add_argument("--id", type=int, required=True)
    lenh.add_parser("liet-ke", help="Liet ke token (khong bao gio in bi mat).")
    args = parser.parse_args(argv)

    # init_db chứ không phải create_all: database cũ cần được bổ sung bảng worker_tokens.
    init_db()
    with SessionLocal() as db:
        if args.lenh == "cap":
            try:
                token, dong = token_worker.cap(db, args.ten, args.so_ngay)
            except ValueError as loi:
                print(str(loi), file=sys.stderr)
                return 1
            db.commit()
            print(f"Da cap token id={dong.id} cho worker '{dong.ten}', het han {dong.expires_at:%Y-%m-%d %H:%M} UTC.")
            print("Token (CHI HIEN MOT LAN, dat vao INFERENCE_SERVICE_TOKEN cua worker nay):")
            print(token)
            return 0
        if args.lenh == "thu-hoi":
            if not token_worker.thu_hoi(db, args.id):
                print(f"Khong co token id={args.id} con hieu luc de thu hoi.", file=sys.stderr)
                return 1
            db.commit()
            print(f"Da thu hoi token id={args.id}. Worker dung token nay bi tu choi tu request ke tiep.")
            return 0
        for d in token_worker.liet_ke(db):
            print(f"{d['id']:>4}  {d['ten']:<20} het han {d['het_han']}  {d['trang_thai']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
