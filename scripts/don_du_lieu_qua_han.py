"""Dọn dữ liệu quá hạn một lần rồi thoát. Dùng để chạy bằng cron / Task Scheduler.

Server đã tự dọn lúc khởi động và theo nhịp trên đường chat, nên script này KHÔNG bắt buộc.
Nó dành cho server chạy liên tục nhiều ngày mà ít người chat: khi đó nhịp dọn trên đường
chat hiếm khi tới, còn dữ liệu quá hạn vẫn cứ nằm đó.

    python scripts/don_du_lieu_qua_han.py

Mã thoát khác 0 khi dọn thất bại, để cron báo lỗi thay vì im lặng.
"""
import os
import sys

# Chạy được bằng `python scripts/don_du_lieu_qua_han.py`: đưa thư mục gốc dự án vào
# sys.path để import được setup_database và app.
GOC_DU_AN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GOC_DU_AN not in sys.path:
    sys.path.insert(0, GOC_DU_AN)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from setup_database import SessionLocal  # noqa: E402  (phải sau load_dotenv)
from app.services import chat_history  # noqa: E402


def main() -> int:
    chat_history.dat_lai_nhip_don()     # chạy tay/cron thì luôn dọn, không chờ nhịp
    with SessionLocal() as db:
        ket = chat_history.don_dinh_ky(db)
    print(
        f"Da don (han luu {chat_history.SO_NGAY_LUU_TRU} ngay): "
        f"{ket['tin_nhan_qua_han']} tin nhan qua han, "
        f"{ket['token_thu_hoi_het_han']} token thu hoi da het han."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
