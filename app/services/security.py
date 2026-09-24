"""Nguyên thuỷ mật mã: băm mật khẩu và ký/giải mã token.

Vì sao tách riêng khỏi tầng API:
    Ở đây là những quyết định mật mã ít thay đổi nhưng sai một lần là hỏng toàn bộ
    (thuật toán băm, thời hạn token, cách so sánh bí mật). Gom về một chỗ thì khi rà
    soát bảo mật chỉ phải đọc một file, thay vì truy từng endpoint xem nó tự chế biến
    gì. Tầng API ở trên chỉ gọi hàm, không tự đụng vào jwt hay bcrypt.

Ranh giới trách nhiệm:
    File này KHÔNG biết gì về database và KHÔNG quyết định ai được làm gì. Nó chỉ trả
    lời "chuỗi này có chữ ký hợp lệ không" và "mật khẩu này có khớp hash không".
    Việc tra người dùng ra khỏi database và áp quy tắc phân quyền nằm ở app/api/deps.py.
"""
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from dotenv import load_dotenv

load_dotenv()

ALGORITHM = "HS256"

# Thời hạn ngắn cho người dùng: JWT KHÔNG thu hồi được sau khi đã phát. Không có
# blocklist thì một token bị lộ vẫn dùng được đến khi hết hạn, nên hạn càng ngắn thì
# cửa sổ thiệt hại càng hẹp. 12 giờ là mức đủ cho một ca trực mà không bắt bác sĩ
# đăng nhập lại giữa chừng.
ACCESS_TOKEN_EXPIRE_HOURS = 12

# Hệ thống hiện chỉ phát đúng MỘT loại token cho người: token của tài khoản đã đăng nhập.
# Loại token khách vãng lai đã bị gỡ ở P4 cùng toàn bộ luồng đó.
#
# Vì sao vẫn giữ trường "typ" dù chỉ còn một giá trị:
#     Token đang lưu hành ngoài kia không tự đổi định dạng được. Giữ nhãn loại nghĩa là
#     decode_token vẫn từ chối được bất kỳ token nào không mang đúng nhãn này — kể cả
#     guest token cũ còn hạn 30 ngày do bản trước phát ra. Bỏ trường đi thì những token
#     đó bỗng dưng hợp lệ vì chữ ký vẫn đúng, chỉ là không còn ai kiểm tra chúng là loại gì.
TOKEN_TYPE_USER = "user"


class TokenError(Exception):
    """Token không dùng được: sai chữ ký, hết hạn, sai loại, hoặc thiếu trường."""


def _get_secret() -> str:
    """Lấy khoá ký, từ chối chạy nếu thiếu hoặc còn để giá trị mẫu.

    Cố ý KHÔNG có giá trị mặc định. Một secret mặc định nằm sẵn trong mã nguồn là bí mật
    công khai: ai đọc được repo đều tự ký được token của bất kỳ ai, kể cả token bác sĩ.
    Thà sập lúc khởi động còn hơn chạy được nhưng mọi chữ ký đều giả mạo được.
    """
    secret = os.getenv("JWT_SECRET_KEY", "")
    if not secret or not secret.strip():
        raise RuntimeError(
            "Thiếu biến môi trường JWT_SECRET_KEY. Sinh một khoá ngẫu nhiên rồi thêm vào .env:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    if len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET_KEY quá ngắn (cần tối thiểu 32 ký tự). Khoá ngắn có thể bị dò "
            "ngoại tuyến từ một token bắt được."
        )
    return secret


# --- Mật khẩu ---------------------------------------------------------------

# bcrypt cắt cụt đầu vào ở 72 byte và LẶNG LẼ bỏ phần đuôi. Không chặn trước thì hai mật
# khẩu khác nhau từ ký tự thứ 73 trở đi sẽ đăng nhập lẫn nhau được.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


def hash_password(plain: str) -> str:
    if len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Mật khẩu vượt quá {MAX_PASSWORD_BYTES} byte.")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# Hash bcrypt THẬT, dùng cho nhánh "email không tồn tại" của endpoint đăng nhập để hai
# nhánh tốn thời gian ngang nhau.
#
# Vì sao không viết tay một chuỗi trông giống hash:
#     Bản trước dùng "$2b$12$" + "x"*53. Chuỗi đó đúng độ dài 60 và nhìn hoàn toàn hợp lý,
#     nhưng KHÔNG phải hash hợp lệ: ký tự cuối của phần salt chỉ mang 2 bit có nghĩa nên
#     bắt buộc thuộc '.Oeu', mà 'x' thì không. bcrypt.checkpw ném ValueError("Invalid salt")
#     ngay lập tức, verify_password bắt lỗi rồi trả False — trong 0 mili giây.
#
#     Đo thật: nhánh email không tồn tại 0.0 ms, nhánh email có thật 203.8 ms. Tức là phép
#     phòng vệ chống dò email chẳng những không hoạt động, nó còn tạo ra một tín hiệu thời
#     gian LỚN HƠN nhiều so với sai lệch tinh vi mà nó sinh ra để che. Lỗi kiểu này không
#     bao giờ tự lộ: code vẫn chạy, test vẫn xanh, chỉ có tính chất bảo mật là không có.
#
# Sinh bằng gensalt() chứ không ghim một hằng số vào mã nguồn: cost factor của nó tự khớp
# với cost của hash thật do hash_password tạo ra. Ghim cứng thì ngày bcrypt đổi cost mặc
# định, hai nhánh lệch nhau trở lại mà không ai nhận ra.
#
# Tốn một lần băm (~200ms) lúc nạp module. Đổi lại là đúng thứ đang cần: một lần băm thật.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"khong-phai-mat-khau-cua-ai-ca", bcrypt.gensalt()
).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """So khớp mật khẩu. Mọi lỗi đều trả False chứ không ném ra ngoài.

    Hash hỏng/rỗng trong database phải dẫn tới "đăng nhập thất bại", không phải lỗi 500.
    Lỗi 500 ở đây vừa là sự cố vận hành, vừa nói cho người tấn công biết tài khoản đó
    có thật và dữ liệu của nó đang bất thường.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- Token ------------------------------------------------------------------

def _encode(payload: dict, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    to_encode = {
        **payload,
        "iat": now,
        "exp": now + expires_delta,
        # Mã định danh riêng cho từng token, để sau này muốn làm blocklist thu hồi token
        # thì đã có sẵn thứ để chặn mà không phải đổi định dạng token đang lưu hành.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(to_encode, _get_secret(), algorithm=ALGORITHM)


def create_user_token(user_id: int, role: str) -> str:
    """Token cho người thật đã đăng nhập.

    Cố ý chỉ nhét id và vai trò. Không nhét tên, email hay bất cứ thông tin định danh
    nào: phần payload của JWT chỉ là base64, ai cầm token cũng đọc được. Và vai trò
    trong token vẫn được đối chiếu lại với database ở mỗi request (xem deps.py), nên
    hạ quyền một tài khoản có hiệu lực ngay mà không cần chờ token cũ hết hạn.
    """
    return _encode(
        {"sub": str(user_id), "role": role, "typ": TOKEN_TYPE_USER},
        timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    )


def decode_token(token: str, expected_type: Optional[str] = None) -> dict:
    """Giải mã và kiểm tra token. Ném TokenError cho MỌI trường hợp không dùng được.

    Gộp chung mọi lý do thất bại vào một loại lỗi là có chủ ý: phân biệt "chữ ký sai"
    với "đã hết hạn" trong thông báo trả về client chỉ giúp người dò token biết mình
    đang tiến gần tới đâu.
    """
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise TokenError("Token không hợp lệ hoặc đã hết hạn.")

    if not payload.get("sub"):
        raise TokenError("Token thiếu thông tin định danh.")

    # Chặn dùng token loại này ở chỗ đang chờ token loại kia.
    if expected_type is not None and payload.get("typ") != expected_type:
        raise TokenError("Token không đúng loại cho thao tác này.")

    return payload


# --- Service token cho worker suy luận --------------------------------------

def verify_service_token(presented: Optional[str]) -> bool:
    """Xác thực worker suy luận bằng bí mật dùng chung.

    Vì sao không dùng JWT như người dùng: worker không đăng nhập, không có phiên và
    không cần hết hạn. Nó cần đúng một thứ — chứng minh mình là worker. Một bí mật dùng
    chung do người vận hành cấu hình là mô hình đúng với thực tế đó; dựng cả cơ chế
    phiên cho một tiến trình nền chỉ thêm bộ phận để hỏng.

    Dùng compare_digest chứ không dùng '==': so sánh chuỗi thông thường dừng ngay tại
    byte đầu tiên khác nhau, và thời gian trả lời rò rỉ ra độ dài tiền tố đúng, đủ để
    dò từng ký tự một.
    """
    expected = os.getenv("INFERENCE_SERVICE_TOKEN", "")
    if not expected or not expected.strip():
        # Chưa cấu hình thì KHÔNG mở cổng. Coi thiếu cấu hình là "cấm tất cả" chứ không
        # phải "cho tất cả": một biến môi trường quên set khi triển khai sẽ âm thầm gỡ
        # bỏ toàn bộ lớp bảo vệ mà không có dấu hiệu nào.
        return False
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)
