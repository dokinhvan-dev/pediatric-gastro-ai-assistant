"""Đăng ký, đăng nhập và tra cứu danh tính của chính mình.

ĐIỂM QUAN TRỌNG NHẤT CỦA FILE NÀY:
    Endpoint đăng ký KHÔNG nhận trường 'role'. Nếu client chọn được vai trò của mình thì
    bất kỳ ai cũng tự phong mình làm bác sĩ, và toàn bộ lớp phân quyền phía sau chỉ còn
    là trang trí. Tài khoản bác sĩ chỉ được cấp qua scripts/create_clinician.py — một script chạy
    trực tiếp trên máy chủ, tức là đã cần quyền truy cập database từ trước.

    Đây không phải cẩn thận thừa: "mass assignment" kiểu này là một trong những lỗ hổng
    phổ biến nhất ở các API tự viết, vì dòng code gây lỗi trông hoàn toàn vô hại
    (User(**payload.dict())).
"""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from setup_database import SessionLocal, User
from app.api.deps import (
    ROLE_PARENT,
    CurrentUser,
    chan_neu_qua_han_muc,
    get_current_user,
    get_token_payload,
)
from app.services import chat_history, dong_y, dong_y_nghien_cuu, security, token_store

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])

# Cố ý dễ dãi. Cách duy nhất biết chắc email có thật là gửi thư xác nhận; một regex
# chặt chẽ chỉ loại oan các địa chỉ hợp lệ nhưng hiếm gặp mà không chặn được ai.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAX_EMAIL_LENGTH = 255
MAX_NAME_LENGTH = 100


class RegisterIn(BaseModel):
    email: str
    password: str
    full_name: str
    # KHÔNG có 'role' ở đây, và đó là chủ ý. Pydantic mặc định bỏ qua trường lạ, nên
    # client có gửi kèm "role": "doctor" thì nó cũng rơi thẳng vào thùng rác.


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = Field(default="bearer")
    expires_in_hours: int
    user_id: int
    full_name: str
    role: str


def _normalize_email(raw: str) -> str:
    """Chuẩn hoá email về chữ thường.

    Không chuẩn hoá thì 'An@x.com' và 'an@x.com' tạo ra hai tài khoản khác nhau trong
    khi thư vẫn về chung một hộp — người dùng sẽ đăng ký lại rồi tưởng mất hết dữ liệu.
    """
    return (raw or "").strip().lower()


def _validate_credentials(email: str, password: str) -> None:
    if not email:
        raise HTTPException(status_code=422, detail="'email' không được để trống.")
    if len(email) > MAX_EMAIL_LENGTH:
        raise HTTPException(status_code=422, detail=f"'email' không được dài quá {MAX_EMAIL_LENGTH} ký tự.")
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=422, detail="'email' không đúng định dạng.")

    if len(password) < security.MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Mật khẩu phải có tối thiểu {security.MIN_PASSWORD_LENGTH} ký tự."
        )
    # Chặn ở tầng này thay vì để bcrypt lặng lẽ cắt cụt ở byte thứ 72.
    if len(password.encode("utf-8")) > security.MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"Mật khẩu quá dài (tối đa {security.MAX_PASSWORD_BYTES} byte)."
        )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn, request: Request):
    """Tạo tài khoản phụ huynh. LUÔN LUÔN là vai trò 'parent', không có ngoại lệ."""
    # Hạn mức đặt TRƯỚC mọi thứ khác, vì thứ đắt nhất ở đây là hash_password: đo thật
    # 208ms bcrypt cho mỗi lần gọi, mà endpoint thì không cần đăng nhập. Vấn đề không
    # nằm ở số request/giây mà ở chiếm dụng luồng — 40 kết nối lặp là threadpool của
    # FastAPI đầy vĩnh viễn, và bác sĩ đang duyệt ca xếp hàng phía sau.
    chan_neu_qua_han_muc(
        "dang_ky", request, None,
        "Quá nhiều lượt đăng ký từ thiết bị này. Vui lòng thử lại sau ít phút.",
    )

    email = _normalize_email(payload.email)
    full_name = (payload.full_name or "").strip()
    _validate_credentials(email, payload.password)

    if not full_name:
        raise HTTPException(status_code=422, detail="'full_name' không được để trống.")
    if len(full_name) > MAX_NAME_LENGTH:
        raise HTTPException(status_code=422, detail=f"'full_name' không được dài quá {MAX_NAME_LENGTH} ký tự.")

    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash=security.hash_password(payload.password),
            full_name=full_name,
            # Gán cứng, không lấy từ payload. Xem docstring đầu file.
            role=ROLE_PARENT,
            is_active=True,
        )
        try:
            db.add(user)
            db.commit()
            db.refresh(user)
        except IntegrityError:
            db.rollback()
            # Ràng buộc UNIQUE trên cột email là chốt chặn thật. Kiểm tra trước bằng một
            # câu SELECT rồi mới INSERT vẫn có khe hở: hai request đồng thời đều thấy
            # email còn trống rồi cùng ghi. Ở đây bắt lỗi từ chính database nên không
            # tồn tại khe hở đó.
            raise HTTPException(status_code=409, detail="Email này đã được đăng ký.")
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể tạo tài khoản, vui lòng thử lại.")

        return TokenOut(
            access_token=security.create_user_token(user.id, user.role),
            expires_in_hours=security.ACCESS_TOKEN_EXPIRE_HOURS,
            user_id=user.id,
            full_name=user.full_name,
            role=user.role,
        )


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request):
    """Đổi email + mật khẩu lấy token truy cập."""
    # Chốt này gánh HAI việc cùng lúc:
    #   1. Dò mật khẩu. Trước đây không có gì giới hạn số lần đoán: không khoá tài khoản,
    #      không đếm lần sai, bcrypt là thứ duy nhất làm chậm kẻ tấn công.
    #   2. Khuếch đại CPU. Sau khi vá lỗi hash giả, nhánh "email không tồn tại" cũng tốn
    #      200ms thật — đúng như thiết kế — nên nó gia nhập nhóm chiếm dụng luồng cùng
    #      endpoint đăng ký. Vá lỗi timing khiến chốt này thành bắt buộc chứ không còn
    #      là tuỳ chọn.
    #
    # Đếm theo IP, CỐ Ý không đếm theo email đang thử. Đếm theo email sẽ chặn được dò
    # phân tán vào một tài khoản, nhưng mở ra một đường hại người dùng thật: kẻ tấn công
    # chỉ cần bắn hết hạn mức của một email là khoá luôn chủ tài khoản ra ngoài. Đổi một
    # lỗ hổng lấy một lỗ hổng khác thì không phải là vá.
    chan_neu_qua_han_muc(
        "dang_nhap", request, None,
        "Quá nhiều lần đăng nhập từ thiết bị này. Vui lòng thử lại sau ít phút.",
    )

    email = _normalize_email(payload.email)

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()

        # Một thông báo lỗi DUY NHẤT cho cả "email không tồn tại" và "sai mật khẩu".
        # Tách hai trường hợp ra sẽ biến endpoint đăng nhập thành công cụ dò xem địa chỉ
        # nào đã đăng ký — thông tin đủ để nhắm mục tiêu lừa đảo, và với hệ thống y tế
        # thì bản thân việc một người có tài khoản ở đây đã là dữ liệu nhạy cảm.
        sai_thong_tin = HTTPException(
            status_code=401,
            detail="Email hoặc mật khẩu không đúng.",
            headers={"WWW-Authenticate": "Bearer"},
        )

        if not user:
            # Vẫn băm một lần dù biết chắc sẽ hỏng, để giữ thời gian phản hồi ngang với
            # nhánh có tài khoản thật. Thoát sớm ở đây làm request trả về nhanh hơn hẳn,
            # và chênh lệch thời gian đó tự nó tiết lộ email nào tồn tại.
            #
            # Hash giả phải là hash bcrypt THẬT. Bản trước tự ghép "$2b$12$" + "x"*53 —
            # đúng độ dài, nhìn hợp lý, nhưng bcrypt từ chối ngay vì salt không hợp lệ,
            # nên nhánh này trả về trong 0ms trong khi nhánh kia tốn 200ms. Phép phòng vệ
            # không những không chạy mà còn tự tạo ra tín hiệu thời gian lớn hơn hẳn.
            # Xem DUMMY_PASSWORD_HASH trong app/services/security.py.
            security.verify_password(payload.password, security.DUMMY_PASSWORD_HASH)
            raise sai_thong_tin

        if not security.verify_password(payload.password, user.password_hash):
            raise sai_thong_tin

        if not user.is_active:
            raise HTTPException(status_code=403, detail="Tài khoản đã bị vô hiệu hoá.")

        return TokenOut(
            access_token=security.create_user_token(user.id, user.role),
            expires_in_hours=security.ACCESS_TOKEN_EXPIRE_HOURS,
            user_id=user.id,
            full_name=user.full_name,
            role=user.role,
        )


@router.post("/logout")
def logout(
        payload: dict = Depends(get_token_payload),
        user: CurrentUser = Depends(get_current_user),
):
    """Thu hồi token đang dùng, để nó hết hiệu lực ngay thay vì chờ tới exp.

    Vì sao cần endpoint này dù client chỉ việc xoá token đi:
        Xoá ở client là xoá bản sao của client. Bản nào đã kịp nằm ở chỗ khác — log của
        proxy, lịch sử của một máy dùng chung, một ảnh chụp màn hình — vẫn dùng được tới
        hết 12 tiếng. Phụ huynh vừa đăng xuất ở máy lạ cần điều đó dừng lại BÂY GIỜ.

    Vì sao vẫn yêu cầu get_current_user chứ không chỉ cần chữ ký hợp lệ:
        Để đăng xuất một token ĐÃ thu hồi trả về 401 chứ không phải 200. Báo thành công
        cho một thao tác không xảy ra là nói dối client, và ở đây lời nói dối đó có hình
        dạng đặc biệt xấu: nó khẳng định một phiên đã đóng trong khi ta chưa hề đóng gì.

    Đây KHÔNG phải "đăng xuất khỏi mọi thiết bị": mỗi lần đăng nhập là một jti riêng.
    Muốn chặn sạch một tài khoản thì công cụ đúng là cờ is_active.
    """
    with SessionLocal() as db:
        if not token_store.thu_hoi(db, payload, ly_do="logout"):
            # Token thiếu jti (do bản cũ phát ra) thì không có gì để ghi vào sổ. Nói
            # thẳng là không thu hồi được, vì client cần biết để bắt người dùng đăng
            # nhập lại lấy token mới thay vì yên tâm rằng phiên cũ đã đóng.
            raise HTTPException(
                status_code=409,
                detail="Token này không thu hồi được. Hãy đăng nhập lại để lấy token mới.",
            )

        # Dọn cơ hội: chỗ này vốn đã ghi vào bảng và không nằm trên đường đi của request
        # thông thường, nên gánh thêm việc dọn ở đây không làm chậm ai cả.
        token_store.don_token_het_han(db)

    return {"status": "success", "message": "Đã đăng xuất."}


@router.get("/me")
def read_me(user: CurrentUser = Depends(get_current_user)):
    """Cho client kiểm tra token còn hiệu lực và mình đang mang vai trò gì.

    Vai trò trả về ở đây lấy từ database, không phải từ token, nên client luôn thấy
    trạng thái hiện tại chứ không phải trạng thái lúc đăng nhập.
    """
    with SessionLocal() as db:
        u = db.query(User).filter(User.id == user.id).first()
        return {
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "dong_y_chia_se_ai": {
                "con_hieu_luc": dong_y.con_hieu_luc(u),
                # Trả cả phiên bản đã đồng ý lẫn phiên bản hiện hành, để client phân biệt
                # được "chưa từng đồng ý" với "đã đồng ý nhưng thông báo đã đổi, cần xem lại".
                "phien_ban_da_dong_y": getattr(u, "dong_y_ai_phien_ban", None),
                "phien_ban_hien_hanh": dong_y.PHIEN_BAN_THONG_BAO,
            },
            # Tách riêng khỏi dong_y_chia_se_ai: hai sự đồng ý độc lập, về hai việc khác nhau.
            "nghien_cuu_mac_dinh": {
                "con_hieu_luc": dong_y_nghien_cuu.mac_dinh_con_hieu_luc(u),
                "phien_ban_hien_hanh": dong_y_nghien_cuu.PHIEN_BAN_THONG_BAO,
            },
        }


class NghienCuuMacDinhIn(BaseModel):
    bat: bool
    # Bắt buộc khi bật: phiên bản thông báo nghiên cứu mà client vừa hiển thị.
    phien_ban: Optional[str] = None


@router.put("/nghien-cuu-mac-dinh")
def dat_nghien_cuu_mac_dinh(payload: NghienCuuMacDinhIn, user: CurrentUser = Depends(get_current_user)):
    """Đặt lựa chọn MẶC ĐỊNH cho đồng thuận nghiên cứu của các ảnh upload sau này.

    Chỉ ảnh hưởng tới ảnh upload SAU lời gọi này — không đụng tới đồng thuận đã ghi trên các
    ảnh cũ. Bật mặc định rồi âm thầm áp ngược cho mọi ảnh đã có là biến một lựa chọn về
    tương lai thành một quyết định về quá khứ mà người dùng không hề thấy.
    """
    if payload.bat and payload.phien_ban != dong_y_nghien_cuu.PHIEN_BAN_THONG_BAO:
        raise HTTPException(
            status_code=409,
            detail="Nội dung thông báo nghiên cứu đã được cập nhật. Vui lòng xem lại trước khi đồng ý.",
        )
    with SessionLocal() as db:
        u = db.query(User).filter(User.id == user.id).first()
        u.nghien_cuu_mac_dinh_phien_ban = dong_y_nghien_cuu.PHIEN_BAN_THONG_BAO if payload.bat else None
        db.commit()
    return {"status": "success", "nghien_cuu_mac_dinh": payload.bat}


class DongYIn(BaseModel):
    # Client phải gửi lại đúng phiên bản thông báo mà nó VỪA hiển thị cho người dùng.
    phien_ban: str


@router.post("/dong-y-ai")
def dong_y_chia_se_ai(payload: DongYIn, user: CurrentUser = Depends(get_current_user)):
    """Ghi nhận người dùng đồng ý cho hệ thống gửi hồ sơ bé kèm theo khi chat với trợ lý.

    Vì sao bắt client gửi lại phiên bản thông báo:
        Đồng ý chỉ có nghĩa khi người dùng đã thấy đúng nội dung mình đồng ý. Nếu thông báo
        vừa đổi (gửi thêm một loại dữ liệu mới) trong lúc màn hình của họ còn mở bản cũ,
        một cú bấm "đồng ý" không được phép tính là đồng ý với bản mới mà họ chưa hề đọc.
        Lệch phiên bản thì trả 409 để client tải lại thông báo.
    """
    if payload.phien_ban != dong_y.PHIEN_BAN_THONG_BAO:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nội dung thông báo đã được cập nhật. Vui lòng xem lại thông báo mới trước "
                "khi đồng ý."
            ),
        )

    with SessionLocal() as db:
        u = db.query(User).filter(User.id == user.id).first()
        dong_y.ghi_dong_y(u)
        db.commit()
        return {
            "status": "success",
            "message": "Đã ghi nhận đồng ý. Trợ lý có thể đọc hồ sơ của bé từ lượt chat kế tiếp.",
            "phien_ban": dong_y.PHIEN_BAN_THONG_BAO,
        }


@router.delete("/dong-y-ai")
def rut_dong_y_chia_se_ai(user: CurrentUser = Depends(get_current_user)):
    """Rút lại đồng ý. Có hiệu lực ngay, kể cả với lượt chat đang chạy dở.

    Mỗi lời gọi công cụ của trợ lý tra lại đồng thuận trong database, nên lời gọi kế tiếp
    sau thời điểm rút đã bị chặn.

    Lịch sử hội thoại bị XOÁ HẲN, trong CÙNG transaction với việc xoá đồng thuận. Trước đây
    lịch sử chỉ thôi được nạp lại và vẫn nằm trong database — tức là những câu trả lời dựng
    từ hồ sơ bé tiếp tục được lưu sau khi phụ huynh đã nói "thôi, đừng dùng hồ sơ của con
    tôi nữa", và chờ họ tự tìm ra endpoint xoá lịch sử. Không lọc được văn bản tự do theo
    "phần nào có dùng hồ sơ bé", nên xoá toàn bộ; thông báo đồng thuận nói trước điều này.
    Hai lần commit riêng sẽ mở ra cửa sổ đồng thuận đã rút mà lịch sử vẫn còn — cùng lý do
    endpoint xoá hồ sơ bé gộp chung một transaction.

    Rút khi chưa từng đồng ý vẫn trả 200: trạng thái người dùng muốn ("không chia sẻ") đã
    đúng sẵn. Khác với đăng xuất một token đã thu hồi, ở đây không có thao tác nào bị nói
    dối là đã xảy ra — trạng thái cuối cùng là thứ được khẳng định, và nó đúng.
    """
    with SessionLocal() as db:
        u = db.query(User).filter(User.id == user.id).first()
        dong_y.rut_dong_y(u)
        so_tin_da_xoa = chat_history.xoa_lich_su_chua_commit(db, user)
        db.commit()
    return {
        "status": "success",
        "message": "Đã rút lại đồng ý chia sẻ hồ sơ với trợ lý AI và xoá lịch sử trò chuyện.",
        "so_tin_chat_da_xoa": so_tin_da_xoa,
    }
