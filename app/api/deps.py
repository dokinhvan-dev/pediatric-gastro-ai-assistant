"""Dependency phân quyền: biến một request thô thành "ai đang gọi và được làm gì".

Vì sao tất cả nằm chung một file:
    Phân quyền rải rác là cách chắc chắn nhất để một endpoint bị bỏ sót. Gom về đây thì
    câu hỏi "endpoint nào đang không được bảo vệ" trả lời được bằng cách đọc danh sách
    router xem chỗ nào thiếu Depends, thay vì phải đọc hết thân hàm của từng endpoint.

Ranh giới với app/services/authz.py:
    File kia QUYẾT ĐỊNH (trả True/False), file này DỊCH quyết định đó sang mã lỗi HTTP.
    Tách ra vì tầng agent cũng cần đúng quyết định ấy nhưng phải diễn đạt bằng dict báo
    lỗi chứ không phải exception. Một quy tắc, hai cách nói.

Quy ước mã lỗi, giữ nhất quán toàn hệ thống:
    401 = "tôi không biết anh là ai"      (thiếu token, token hỏng, token hết hạn)
    403 = "tôi biết anh là ai, và không"  (đã xác thực nhưng sai vai trò / không sở hữu)
    404 = "không có thứ đó"
Phân biệt 401 với 403 không phải chuyện hình thức: client cần biết nên đưa người dùng
đi đăng nhập lại (401) hay báo thẳng là không có quyền (403).
"""
import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from setup_database import SessionLocal, StoolRecord, User
from app.services import rate_limit, security, token_store
# Nhập lại và tái xuất để các module đang dùng "from app.api.deps import CurrentUser"
# không phải đổi: với tầng API thì deps.py vẫn là cửa vào duy nhất của khái niệm danh tính.
from app.services.authz import (  # noqa: F401
    ROLE_ADMIN,
    ROLE_DOCTOR,
    ROLE_PARENT,
    CurrentUser,
    WorkerDanhTinh,
    can_view_record,
)

logger = logging.getLogger("bitss.api")

# auto_error=False để TỰ sinh lỗi. Mặc định của FastAPI trả 403 khi thiếu header
# Authorization, trong khi thiếu token đúng nghĩa là 401 — client nhìn 403 sẽ tưởng
# mình sai quyền và không bao giờ thử đăng nhập lại.
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    # WWW-Authenticate là phần bắt buộc của 401 theo RFC 9110; thiếu nó thì client
    # chuẩn mực không biết phải trình loại thông tin xác thực nào.
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """Bắt buộc có token người dùng hợp lệ, và tài khoản phải còn hoạt động."""
    payload = get_token_payload(credentials)

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized("Token không hợp lệ.")

    # Tra lại database ở MỖI request thay vì tin phần payload của token.
    # Token là ảnh chụp lúc đăng nhập; từ lúc đó tới giờ tài khoản có thể đã bị khoá
    # hoặc bị hạ vai trò. Tin token nghĩa là một bác sĩ vừa bị thu hồi quyền vẫn chốt
    # được kết luận thêm 12 tiếng nữa.
    with SessionLocal() as db:
        # Kiểm tra thu hồi đặt ở ĐÂY, và đây là chỗ duy nhất, vì get_optional_principal
        # cũng đi qua hàm này. Nhân bản phép kiểm sang chỗ thứ hai nghĩa là tạo ra một
        # bản có thể bị quên cập nhật — và bản bị quên bao giờ cũng là bản lỏng hơn.
        #
        # Đặt trước cả truy vấn users: token đã thu hồi thì không cần biết chủ nó là ai.
        if token_store.da_thu_hoi(db, payload.get("jti")):
            raise _unauthorized("Phiên đăng nhập đã kết thúc, vui lòng đăng nhập lại.")

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise _unauthorized("Tài khoản không còn tồn tại.")
        if not user.is_active:
            # 403 chứ không 401: token vẫn hợp lệ, danh tính vẫn rõ — vấn đề nằm ở
            # quyền. Trả 401 sẽ khiến client cho người dùng đăng nhập lại vô ích.
            raise HTTPException(status_code=403, detail="Tài khoản đã bị vô hiệu hoá.")

        return CurrentUser(id=user.id, email=user.email, full_name=user.full_name, role=user.role)


def get_token_payload(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """Kiểm tra chữ ký và loại token. Đăng xuất dùng jti để thu hồi phiên."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Cần đăng nhập để thực hiện thao tác này.")
    try:
        return security.decode_token(credentials.credentials, expected_type=security.TOKEN_TYPE_USER)
    except security.TokenError as exc:
        raise _unauthorized(str(exc))


def require_role(*allowed_roles: str):
    """Sinh ra dependency chặn theo vai trò.

    Danh sách vai trò được phép là danh sách TRẮNG. Viết theo kiểu chặn ("ai không phải
    parent thì cho qua") sẽ tự động mở cửa cho mọi vai trò thêm vào sau này.
    """
    allowed = set(allowed_roles)

    def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail="Bạn không có quyền thực hiện thao tác này.",
                # Cố ý không nói ra cần vai trò gì. Thông báo "cần quyền bác sĩ" vẽ sẵn
                # bản đồ cho người đang dò xem nên chiếm tài khoản loại nào.
            )
        return user

    return _checker


# Bác sĩ chốt kết luận lâm sàng. Admin KHÔNG nằm trong nhóm này: quyền quản trị hệ thống
# không đồng nghĩa với năng lực đọc ảnh phân nhi khoa, và nhãn do admin chốt sẽ chui vào
# tập huấn luyện y như nhãn bác sĩ thật.
require_doctor = require_role(ROLE_DOCTOR)


def require_service_token(x_service_token: Optional[str] = Header(default=None)) -> WorkerDanhTinh:
    """Chặn các điểm nối dành cho worker suy luận.

    Đây là ranh giới máy-với-máy, không phải người dùng, nên không dùng Bearer token của
    người. Tách hẳn header ra cũng khiến việc một token người dùng bị đem đi ghi kết quả
    AI trở thành bất khả, chứ không phụ thuộc vào việc nhớ kiểm tra vai trò.

    Mỗi worker một token riêng (setup_database.WorkerToken): trả về danh tính của đúng worker
    đó để nhật ký kiểm toán ghi được máy nào đã làm gì. Hết hạn hay đã thu hồi thì 401 như
    token sai — cùng một thông báo, không cho người dò biết token từng có hiệu lực.
    """
    with SessionLocal() as db:
        dong = security.verify_service_token(x_service_token, db)
        if dong is None:
            raise HTTPException(
                status_code=401,
                detail="Thiếu hoặc sai 'X-Service-Token'. Điểm nối này chỉ dành cho worker suy luận.",
            )
        return WorkerDanhTinh(id=dong.id, ten=dong.ten)


def get_optional_principal(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[CurrentUser]:
    """Cho phép chat ẩn danh khi thiếu token. Nếu có token, kiểm tra như yêu cầu đăng nhập."""
    if credentials is None or not credentials.credentials:
        return None

    return get_current_user(credentials)


_PROXY_DA_CANH_BAO: set = set()
_SO_PROXY_CANH_BAO_TOI_DA = 20


def canh_bao_neu_proxy_chua_duoc_tin(request) -> bool:
    """Ghi cảnh báo khi có dấu hiệu ứng dụng đang chạy sau một proxy mà uvicorn không tin.

    Vì sao cần: hạn mức đếm theo request.client.host. Uvicorn chỉ thay giá trị đó bằng IP
    thật của khách (lấy từ X-Forwarded-For) khi request đến từ một IP nằm trong
    FORWARDED_ALLOW_IPS — mặc định chỉ 127.0.0.1. Chạy sau Cloudflare, load balancer hay
    mạng Docker mà quên cấu hình thì client.host là IP CỦA PROXY cho mọi người dùng, và cả
    trang chỉ còn chung một hạn mức: 10 lần đăng nhập mỗi 10 phút cho toàn bộ phụ huynh.
    Hỏng kiểu đó hoàn toàn im lặng — chỉ biểu hiện thành "thỉnh thoảng không ai đăng nhập được".

    Dấu hiệu nhận biết: request mang X-Forwarded-For nhưng client.host KHÔNG nằm trong chuỗi
    đó. Khi uvicorn đã tin proxy, client.host được lấy từ chính chuỗi này; khi không tin, nó
    là IP của proxy, thứ không bao giờ tự ghi mình vào header của mình.

    Chỉ CẢNH BÁO, không tự tin header: header do client gửi nên giả mạo được, và kẻ gọi thẳng
    vào ứng dụng cũng làm hiện được dòng cảnh báo này. Cảnh báo một lần cho mỗi IP, có chặn
    trên, để kẻ gửi header giả không biến nó thành công cụ làm ngập log.
    """
    xff = request.headers.get("x-forwarded-for") if hasattr(request, "headers") else None
    host = getattr(getattr(request, "client", None), "host", None)
    if not xff or not host:
        return False
    chuoi = [ip.strip() for ip in xff.split(",") if ip.strip()]
    if host in chuoi:
        return False
    if host in _PROXY_DA_CANH_BAO or len(_PROXY_DA_CANH_BAO) >= _SO_PROXY_CANH_BAO_TOI_DA:
        return False
    _PROXY_DA_CANH_BAO.add(host)
    logger.warning(
        "Nhan X-Forwarded-For tu %s nhung khong tin: han muc dang dem theo IP nay. Neu %s la "
        "proxy cua ban, moi nguoi dung dang chung MOT han muc. Dat bien moi truong THAT "
        "FORWARDED_ALLOW_IPS=%s (khong dat trong .env - xem .env.example).",
        host, host, host,
    )
    return True


def chan_neu_qua_han_muc(nhom: str, request, principal, thong_bao: str) -> None:
    """Dịch quyết định của app/services/rate_limit.py sang mã lỗi HTTP 429.

    Cùng ranh giới đã áp cho authz.py: tầng service quyết định, tầng này chỉ chọn con số
    và câu chữ trả về. Nhờ vậy hạn mức kiểm thử được bằng hàm thuần, không cần dựng HTTP.

    Retry-After mang số giây THẬT tính từ sổ đếm, cùng quy ước với nhánh 503 khi Gemini
    quá tải: nói cho client con số đúng thay vì để họ đoán rồi quay lại quá sớm và ăn
    tiếp một lần từ chối.

    Địa chỉ IP lấy từ request.client, CỐ Ý không đọc X-Forwarded-For — header đó do client
    gửi nên giả mạo được, và tin nó nghĩa là ai cũng vượt hạn mức bằng cách bịa một IP mới
    mỗi request. Chi tiết ở docstring của app/services/rate_limit.py.
    """
    canh_bao_neu_proxy_chua_duoc_tin(request)
    khoa = rate_limit.khoa_cua(principal, getattr(request.client, "host", None))
    cho_phep, cho_giay = rate_limit.kiem_tra(nhom, khoa)
    if not cho_phep:
        raise HTTPException(
            status_code=429,
            detail=thong_bao,
            headers={"Retry-After": str(cho_giay)},
        )


def get_record_or_404(db, record_id: int) -> StoolRecord:
    record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")
    return record


def assert_can_view_record(db, record, principal) -> None:
    """Dịch quyết định của authz.can_view_record sang mã lỗi HTTP.

    Quy tắc nằm ở app/services/authz.py; ở đây chỉ còn việc chọn con số trả về.

    Chưa xuất trình gì cả thì là 401 — có thể người dùng chỉ quên đăng nhập, và bảo họ
    đăng nhập là lời khuyên đúng. Đã xuất trình mà không được phép thì trả 404 chứ không
    403: 403 xác nhận rằng record_id đó có tồn tại, và chỉ riêng việc đếm xem hệ thống
    có bao nhiêu ca đã là thông tin không nên cho.
    """
    if principal is None:
        raise _unauthorized("Cần đăng nhập để xem ca phân tích.")

    if not can_view_record(db, record, principal):
        # Phiên riêng: phiên của request sẽ không commit vì exception ngay dưới đây.
        from app.services import nhat_ky_kiem_toan as kiem_toan
        kiem_toan.ghi_rieng(principal, kiem_toan.TRUY_CAP_BI_TU_CHOI, "record", record.id,
                            ket_qua=kiem_toan.BI_TU_CHOI)
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy ca phân tích có ID: {record.id}",
        )
