import logging
import os
import uuid
from datetime import datetime, date, timedelta, timezone
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query, Request, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from setup_database import SessionLocal, StoolRecord, Child, DoctorReview, User
from app.api.deps import (
    CurrentUser,
    assert_can_view_record,
    chan_neu_qua_han_muc,
    get_current_user,
    require_service_token,
)
from app.services import bitss, dong_y_nghien_cuu, lam_sach_anh, records_query, uncertainty
from app.services.authz import da_xoa_mem
from app.services.records_query import LocKhongHopLe

router = APIRouter(prefix="/api/v1/records", tags=["Stool Records"])

logger = logging.getLogger("bitss.records")

# Cho phép override để test ghi vào thư mục tạm, không đụng data/uploads/ thật.
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/uploads/raw")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Định dạng hợp lệ được xác định bằng CHÍNH nội dung ảnh sau khi decode bằng Pillow.
# Không tin content-type hay filename do client gửi lên: cả hai đều giả mạo được.
ALLOWED_IMAGE_FORMATS = {
    "JPEG": "jpg",
    "PNG": "png",
    "WEBP": "webp",
}

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_TOTAL_PIXELS = 40_000_000            # chặn decompression bomb
MIN_DIMENSION_PX = 32                    # ảnh nhỏ hơn mức này không đủ thông tin để phân loại
FUTURE_TOLERANCE = timedelta(minutes=5)  # dung sai lệch đồng hồ giữa client và server

DAYS_PER_MONTH = 30.4375


def _calculate_age_months(date_of_birth: date, observed_at: datetime) -> float:
    delta_days = (observed_at.date() - date_of_birth).days
    return round(max(delta_days, 0) / DAYS_PER_MONTH, 2)


def _parse_observed_at(raw: Optional[str]) -> datetime:
    """Lấy thời điểm quan sát từ request thay vì mặc định dùng giờ server.

    Phụ huynh có thể chụp ảnh lúc 2h sáng rồi 8h mới upload; nếu lấy giờ server thì
    mốc thời gian lâm sàng sai 6 tiếng và chuỗi theo dõi theo ngày bị lệch.
    """
    if raw is None or not raw.strip():
        return datetime.now(timezone.utc)

    text = raw.strip()
    # datetime.fromisoformat của Python < 3.11 không nhận hậu tố 'Z'
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="'observed_at' phải theo định dạng ISO 8601, ví dụ: 2026-09-20T14:30:00Z"
        )

    # Naive datetime được hiểu là UTC (client di động thường gửi thiếu timezone)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    if parsed > datetime.now(timezone.utc) + FUTURE_TOLERANCE:
        raise HTTPException(status_code=400, detail="'observed_at' không thể nằm ở tương lai.")

    return parsed


def _validate_image_bytes(file_bytes: bytes) -> str:
    """Decode ảnh thật để xác thực. Trả về đuôi file suy ra từ định dạng THẬT của ảnh.

    Chạy hoàn toàn trong bộ nhớ và được gọi TRƯỚC khi ghi bất cứ thứ gì xuống đĩa,
    nên ảnh hỏng không bao giờ để lại file rác.
    """
    if not file_bytes:
        raise HTTPException(status_code=422, detail="File ảnh rỗng.")

    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Ảnh vượt quá dung lượng tối đa {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
        )

    # Bước 1: kiểm tra cấu trúc file (rẻ, không decode toàn bộ pixel)
    try:
        with Image.open(BytesIO(file_bytes)) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(
            status_code=422,
            detail="Nội dung file không phải ảnh hợp lệ hoặc ảnh đã bị hỏng."
        )

    # verify() làm đối tượng ảnh không dùng lại được -> bắt buộc mở lại
    try:
        with Image.open(BytesIO(file_bytes)) as img:
            image_format = (img.format or "").upper()
            width, height = img.size

            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise HTTPException(
                    status_code=422,
                    detail="Chỉ chấp nhận ảnh định dạng JPG, PNG hoặc WEBP."
                )
            if width < MIN_DIMENSION_PX or height < MIN_DIMENSION_PX:
                raise HTTPException(
                    status_code=422,
                    detail=f"Ảnh quá nhỏ (tối thiểu {MIN_DIMENSION_PX}x{MIN_DIMENSION_PX} pixel)."
                )
            # Chặn bomb TRƯỚC khi gọi load(), vì load() mới là bước cấp phát bộ nhớ thật
            if width * height > MAX_TOTAL_PIXELS:
                raise HTTPException(status_code=422, detail="Ảnh có độ phân giải quá lớn.")

            # Bước 2: decode toàn bộ pixel để bắt file bị cắt cụt giữa chừng
            img.load()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Không giải mã được ảnh, file có thể bị hỏng hoặc không đầy đủ."
        )

    return ALLOWED_IMAGE_FORMATS[image_format]


@router.post("/upload")
def upload_stool_observation(
        request: Request,
        file: UploadFile = File(...),
        child_id: int = Form(...),
        observed_at: Optional[str] = Form(None),
        principal: CurrentUser = Depends(get_current_user),
):
    """Tiếp nhận ảnh cho MỘT hồ sơ bé đã đăng ký. Bắt buộc đăng nhập.

    Vì sao bỏ luồng khách vãng lai (P4):
        Sản phẩm này là công cụ theo dõi dọc — giá trị nằm ở chuỗi quan sát theo thời
        gian, không ở một lần tra cứu lẻ. Một ca của khách là điểm dữ liệu mồ côi: không
        gắn với bé nào, không có ca trước để so, và bác sĩ mở ra chỉ thấy đúng một tấm
        ảnh. Nó tốn đủ chi phí lưu trữ và duyệt của một ca thật mà không phục vụ được
        mục đích nào của hệ thống.

        Đổi lại còn một cái được không hiển nhiên: dữ liệu lâm sàng nay đều được SUY RA.
        Đường guest cũ nhận 'guest_age_months' và 'guest_feeding_type' do client tự khai,
        không ai kiểm chứng — nghĩa là nhãn huấn luyện gắn với một tháng tuổi bịa cũng
        được nhận bình thường. Giờ tuổi tính từ children.date_of_birth và chế độ ăn lấy
        từ children.feeding_type, cả hai đều thuộc một hồ sơ có chủ và sửa được truy vết.

    Ô chat vẫn mở cho người chưa đăng nhập, nhưng chỉ ở phần kiến thức chung — xem
    app/services/agent_service.py.

    Vì sao là `def` chứ không phải `async def`:
        Thân hàm toàn việc đồng bộ — decode ảnh bằng Pillow, truy vấn database, ghi file.
        Đặt chúng trong `async def` là chạy thẳng trên event loop, nên trong lúc một ảnh
        đang được decode thì MỌI request khác của server phải đứng chờ. Đo thật: 5 upload
        PNG 36 triệu pixel cùng lúc đẩy độ trễ của GET / (endpoint rẻ nhất hệ thống) từ
        43 ms lên 186 ms. Khai báo `def` thì FastAPI tự đẩy hàm sang threadpool, event loop
        rảnh tay phục vụ phần còn lại.
    """
    # Hạn mức đặt TRƯỚC khi đọc file: endpoint này decode tới 40 triệu pixel (~120MB RAM
    # cho ảnh RGB) và ghi tới 10MB xuống đĩa mỗi lần gọi, mà không có hạn ngạch đĩa ở bất
    # kỳ đâu trong hệ thống. Đã xác thực nên truy được trách nhiệm và khoá tài khoản được
    # — nhưng ổ đĩa đã đầy trước khi ai kịp nhận ra, nên truy trách nhiệm là việc xảy ra
    # quá muộn.
    chan_neu_qua_han_muc(
        "upload", request, principal,
        "Bạn đang tải ảnh lên quá nhanh. Vui lòng chờ một chút rồi thử lại.",
    )

    # 1. child_id không hợp lệ phải báo lỗi tường minh, không âm thầm bỏ qua.
    if child_id <= 0:
        raise HTTPException(status_code=400, detail="'child_id' phải là số nguyên dương.")

    # 2. Thời điểm quan sát lấy từ request
    observed_at_dt = _parse_observed_at(observed_at)

    # 3. Xác thực ảnh bằng cách decode thật, TRƯỚC khi chạm vào đĩa hay database
    #
    # Đọc CÓ GIỚI HẠN: tối đa MAX + 1 byte. Bản trước gọi read() không tham số rồi mới so
    # dung lượng, tức là một file 2 GB được nạp trọn 2 GB vào RAM trước khi bị từ chối —
    # đủ để hệ điều hành giết cả tiến trình phục vụ. Đọc thừa đúng một byte là đủ để biết
    # file có vượt ngưỡng hay không, mà không cần biết nó vượt bao nhiêu.
    #
    # Giới hạn còn lại, ghi rõ để không ai tưởng đã chặn xong: Starlette đã nhận TRỌN phần
    # thân request và ghi tạm xuống đĩa trước khi hàm này chạy. Dòng này chặn được RAM, không
    # chặn được đĩa tạm. Chặn cả đĩa tạm phải làm ở tầng proxy (ví dụ client_max_body_size
    # của nginx), là nơi thấy request trước ứng dụng.
    try:
        file_bytes = file.file.read(MAX_FILE_SIZE_BYTES + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Không đọc được dữ liệu file tải lên.")

    file_ext = _validate_image_bytes(file_bytes)

    # Xoá siêu dữ liệu (EXIF có GPS nơi chụp, XMP, comment...) và áp hướng xoay TRƯỚC khi
    # ghi đĩa. Kể từ dòng này, byte gốc do phụ huynh tải lên không đi tiếp tới đâu nữa.
    # Chi tiết và cái giá của việc mã hoá lại: app/services/lam_sach_anh.py.
    try:
        file_bytes = lam_sach_anh.lam_sach(file_bytes)
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Không xử lý được ảnh này. Vui lòng chụp lại hoặc chọn ảnh khác.",
        )

    with SessionLocal() as db:
        # 4. Thông tin lâm sàng tại thời điểm quan sát, suy ra từ hồ sơ bé
        child = db.query(Child).filter(Child.id == child_id).first()
        if not child:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy bệnh nhi có ID: {child_id}")
        # Hồ sơ đã xoá mềm thì không nhận thêm dữ liệu mới. Thiếu dòng này thì một yêu
        # cầu xoá dữ liệu có thể bị vô hiệu hoá ngay sau khi vừa thực hiện xong: ảnh mới
        # vẫn ghi vào được, và bé "đã xoá" lại có hồ sơ đang lớn dần.
        #
        # Cố ý KHÔNG dùng can_view_child ở đây dù nó cũng chặn bé đã xoá: hàm đó cho bác
        # sĩ xem mọi hồ sơ, mà tải ảnh lên thì chỉ chủ hồ sơ mới được làm. Dùng nó ở đây
        # sẽ âm thầm nới quyền ghi rộng ra bằng quyền đọc.
        if da_xoa_mem(child):
            raise HTTPException(status_code=404, detail=f"Không tìm thấy bệnh nhi có ID: {child_id}")
        # Quyền sở hữu: cùng một thông báo 404 với trường hợp bé không tồn tại.
        # Trả 403 ở đây sẽ xác nhận child_id đó có thật, biến endpoint thành công cụ
        # đếm xem hệ thống đang quản lý những hồ sơ nào.
        if child.owner_user_id != principal.id:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy bệnh nhi có ID: {child_id}")
        if observed_at_dt.date() < child.date_of_birth:
            raise HTTPException(
                status_code=400,
                detail="'observed_at' không thể sớm hơn ngày sinh của bé."
            )
        age_months_at_observation = _calculate_age_months(child.date_of_birth, observed_at_dt)
        feeding_type_at_observation = child.feeding_type

        # 5. Lưu ảnh vật lý (đuôi file lấy từ định dạng thật đã decode được)
        unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
        saved_file_path = os.path.join(UPLOAD_DIR, unique_filename)

        try:
            with open(saved_file_path, "wb") as f:
                f.write(file_bytes)
        except Exception:
            raise HTTPException(status_code=500, detail="Không thể lưu file ảnh, vui lòng thử lại.")

        # 6. Ghi bản ghi ở trạng thái chờ suy luận AI.
        #    Chưa có mô hình thật nên tuyệt đối không sinh kết quả giả.
        try:
            new_record = StoolRecord(
                child_id=child_id,
                age_months_at_observation=age_months_at_observation,
                feeding_type_at_observation=feeding_type_at_observation,
                image_path=saved_file_path,
                heatmap_path=None,
                inference_status="queued",
                ai_predicted_class=None,
                ai_confidence=None,
                is_uncertain=False,
                observed_at=observed_at_dt,
            )
            # Đồng thuận nghiên cứu lấy từ lựa chọn MẶC ĐỊNH của tài khoản, chỉ khi lựa chọn
            # đó còn gắn với thông báo hiện hành. Phụ huynh vẫn rút lại được cho riêng ảnh này.
            chu = db.query(User).filter(User.id == principal.id).first()
            if dong_y_nghien_cuu.mac_dinh_con_hieu_luc(chu):
                dong_y_nghien_cuu.ghi_cho_ca(new_record)
            db.add(new_record)
            db.commit()
            db.refresh(new_record)
        except Exception:
            db.rollback()
            # Ghi DB hỏng thì ảnh vừa lưu trở thành rác mồ côi -> dọn ngay
            if os.path.exists(saved_file_path):
                try:
                    os.remove(saved_file_path)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail="Không thể lưu bản ghi phân tích, vui lòng thử lại.")

        return {
            "status": "success",
            "message": "Đã nhận ảnh, ca phân tích đang chờ xử lý (queued).",
            "data": {
                "record_id": new_record.id,
                "child_id": new_record.child_id,
                "ten_be": child.name,
                "age_months_at_observation": new_record.age_months_at_observation,
                "feeding_type_at_observation": new_record.feeding_type_at_observation,
                "inference_status": new_record.inference_status,
                "review_status": new_record.review_status,
                "is_uncertain": new_record.is_uncertain,
                "observed_at": new_record.observed_at.strftime("%Y-%m-%d %H:%M"),
                # Báo lại cho phụ huynh ảnh này có đang được phép dùng cho nghiên cứu không,
                # vì giá trị được điền sẵn từ lựa chọn mặc định — họ phải thấy được để còn đổi.
                "dong_y_nghien_cuu": dong_y_nghien_cuu.ca_con_hieu_luc(new_record),
            },
        }


@router.get("")
def list_records(
        child_id: Optional[int] = Query(None, description="Chỉ lấy ca của một bé."),
        inference_status: Optional[str] = Query(None),
        review_status: Optional[str] = Query(None),
        chi_ca_khong_chac: bool = Query(False, description="Chỉ lấy ca mô hình không chắc chắn."),
        limit: int = Query(records_query.GIOI_HAN_MAC_DINH),
        offset: int = Query(0),
        principal: CurrentUser = Depends(get_current_user),
):
    """Liệt kê ca phân tích trong phạm vi người đang đăng nhập được xem.

    Phụ huynh thấy ca của con mình, bác sĩ thấy mọi ca. Không có tham số nào cho phép
    client tự khai phạm vi — như GET /api/v1/children, phạm vi do token quyết định.
    Tham số 'child_id' chỉ THU HẸP phạm vi đó chứ không mở rộng: hỏi child_id của người
    khác thì nhận danh sách rỗng, không phải 403.

    Toàn bộ phần lọc và phân trang nằm ở app/services/records_query.py để công cụ xu
    hướng của agent (P6.3) dùng lại đúng câu truy vấn này thay vì viết một bản gần giống.
    """
    with SessionLocal() as db:
        try:
            ket_qua = records_query.liet_ke(
                db,
                principal,
                child_id=child_id,
                inference_status=inference_status,
                review_status=review_status,
                chi_ca_khong_chac=chi_ca_khong_chac,
                limit=limit,
                offset=offset,
            )
        except LocKhongHopLe as loi:
            # Tầng dưới cố ý không biết HTTP là gì, nên việc dịch sang mã lỗi nằm ở đây.
            raise HTTPException(status_code=422, detail=str(loi))

        return {"status": "success", "data": ket_qua}


@router.get("/thong-bao-nghien-cuu")
def thong_bao_nghien_cuu():
    """Nội dung thông báo về việc dùng ảnh cho nghiên cứu, để client hiển thị trước khi hỏi.

    Mở cho người chưa đăng nhập: phải đọc được TRƯỚC khi quyết định. Khai báo trước route
    '/{record_id}' là bắt buộc — đảo lại thì 'thong-bao-nghien-cuu' bị hiểu là một record_id.
    """
    return dong_y_nghien_cuu.thong_bao()


def _ca_cua_chinh_chu(db, record_id: int, principal) -> StoolRecord:
    """Trả ca nếu người gọi là CHỦ hồ sơ bé và bé chưa bị xoá mềm; ngược lại 404.

    Cố ý KHÔNG dùng assert_can_view_record: hàm đó cho bác sĩ xem mọi ca. Đồng thuận nghiên
    cứu là quyền của gia đình, và mượn quyền đọc của bác sĩ ở đây nghĩa là một bác sĩ quyết
    định thay gia đình việc ảnh của con họ có vào tập huấn luyện hay không. Cùng cái bẫy mà
    endpoint upload và endpoint xoá hồ sơ bé đã tránh bằng cùng một cách.

    Một thông báo 404 duy nhất cho: không tồn tại, không phải của mình, bé đã xoá.
    """
    khong_thay = HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")
    record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
    if not record:
        raise khong_thay
    child = db.query(Child).filter(Child.id == record.child_id).first()
    if not child or da_xoa_mem(child) or child.owner_user_id != principal.id:
        raise khong_thay
    return record


class DongYNghienCuuIn(BaseModel):
    # Phiên bản thông báo mà client VỪA hiển thị cho người dùng.
    phien_ban: str


@router.post("/{record_id}/dong-y-nghien-cuu")
def dong_y_nghien_cuu_cho_ca(
        record_id: int,
        payload: DongYNghienCuuIn,
        principal: CurrentUser = Depends(get_current_user),
):
    """Chủ hồ sơ đồng ý cho dùng ẢNH NÀY vào nghiên cứu. Phải kèm đúng phiên bản thông báo."""
    if payload.phien_ban != dong_y_nghien_cuu.PHIEN_BAN_THONG_BAO:
        raise HTTPException(
            status_code=409,
            detail="Nội dung thông báo nghiên cứu đã được cập nhật. Vui lòng xem lại trước khi đồng ý.",
        )
    with SessionLocal() as db:
        record = _ca_cua_chinh_chu(db, record_id, principal)
        dong_y_nghien_cuu.ghi_cho_ca(record)
        db.commit()
        return {"status": "success", "record_id": record_id, "dong_y_nghien_cuu": True}


@router.delete("/{record_id}/dong-y-nghien-cuu")
def rut_dong_y_nghien_cuu_cho_ca(record_id: int, principal: CurrentUser = Depends(get_current_user)):
    """Chủ hồ sơ rút đồng ý cho ảnh này. Có hiệu lực với mọi lần xuất dữ liệu sau đó.

    Trả 200 kể cả khi ảnh vốn chưa được đồng ý: trạng thái người dùng muốn ("không dùng ảnh
    này") đã đúng sẵn, và trạng thái cuối cùng mới là thứ được khẳng định. Trường 'da_rut'
    cho biết lần gọi này có thật sự thay đổi gì không.
    """
    with SessionLocal() as db:
        record = _ca_cua_chinh_chu(db, record_id, principal)
        da_rut = dong_y_nghien_cuu.rut_cho_ca(record)
        db.commit()
        return {
            "status": "success",
            "record_id": record_id,
            "dong_y_nghien_cuu": False,
            "da_rut": da_rut,
            "luu_y": (
                "Ảnh sẽ không được đưa vào bất kỳ lần xuất dữ liệu nào từ bây giờ. Nếu ảnh đã "
                "được dùng để huấn luyện một mô hình trước đó, mô hình đó không thể xoá những "
                "gì đã học từ ảnh."
            ),
        }


@router.get("/{record_id}")
def get_record(record_id: int, principal: CurrentUser = Depends(get_current_user)):
    """Xem chi tiết MỘT ca phân tích.

    Vì sao cần dù đã có GET /api/v1/records:
        Danh sách trả về dòng tóm tắt. Client muốn mở một ca cụ thể trước đây chỉ có hai
        lối: lọc lại cả danh sách rồi tự tìm, hoặc đi qua ô chat. Lối thứ hai đặc biệt
        không ổn — nó biến một thao tác đọc dữ liệu thuần tuý thành một lượt gọi model
        tốn tiền, chậm, và không tất định.

    Phần chung với danh sách dùng lại đúng records_query.tom_tat_record, không tự dựng
    lại bộ trường. Hai endpoint mô tả cùng một ca mà dùng hai bộ tên khác nhau là chỗ
    client buộc phải viết hai bộ mã đọc, rồi một bộ sẽ lạc hậu.

    Phần thêm so với dòng tóm tắt là những thứ chỉ có nghĩa khi xem một ca: mô tả đầy đủ
    của nhóm BITSS, và kết luận cuối cùng của bác sĩ.

    CỐ Ý không trả về inference_error: nội dung đó do worker ghi, có thể chứa traceback
    kèm đường dẫn trên máy chủ. Phụ huynh cần biết ca đang hỏng, không cần biết nó hỏng ở
    dòng nào trong mã nguồn.
    """
    with SessionLocal() as db:
        record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")

        # Cùng một hàm mà GET .../reviews và đường duyệt của bác sĩ dùng. Đây là đường đọc
        # thứ tám trong hệ thống, và toàn bộ lý do quy tắc xoá mềm nằm trong authz chứ
        # không rải ở từng truy vấn là để một endpoint mới như thế này không thể quên nó.
        assert_can_view_record(db, record, principal)

        child = db.query(Child).filter(Child.id == record.child_id).first()
        data = records_query.tom_tat_record(record, ten_be=child.name if child else None)
        data["bitss_chi_tiet"] = bitss.describe(record.ai_predicted_class)

        if record.inference_status != "completed":
            data["ghi_chu_suy_luan"] = (
                f"Ca này đang ở trạng thái suy luận '{record.inference_status}', "
                "chưa có kết quả phân loại BITSS từ mô hình AI."
            )

        # Chỉ kết luận ĐÃ chốt. Lượt 'needs_more_info' chưa phải kết luận, và hiện nó ra ở
        # đây như một phán quyết sẽ khiến phụ huynh đọc một ý kiến còn dang dở thành quyết
        # định cuối. Muốn xem toàn bộ diễn biến duyệt thì đã có GET .../reviews.
        review = (
            db.query(DoctorReview)
            .filter(DoctorReview.record_id == record.id, DoctorReview.is_final.is_(True))
            .first()
        )
        # Chỉ trả qua HTTP, cố ý KHÔNG thêm vào records_query.tom_tat_record: hàm đó cũng
        # dựng dữ liệu cho công cụ của trợ lý, tức là mọi trường thêm vào đó đều đi sang nhà
        # cung cấp AI — và bản kiểm kê dữ liệu gửi đi (mục 24) sẽ phải đổi theo.
        data["dong_y_nghien_cuu"] = dong_y_nghien_cuu.ca_con_hieu_luc(record)

        data["bac_si_da_duyet"] = None
        if review:
            data["bac_si_da_duyet"] = {
                "ten_bac_si": review.clinician_name,
                "nhan_chuan_bitss": review.confirmed_bitss,
                "nhan_chuan_bitss_ten": bitss.label(review.confirmed_bitss),
                "quyet_dinh": review.decision,
                "loi_dan": review.clinical_notes,
                "thoi_gian_duyet": review.reviewed_at.strftime("%Y-%m-%d %H:%M"),
            }

        return {"status": "success", "data": data}


_KIEU_ANH = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

_KHONG_CO_ANH = "Không tìm thấy ảnh của ca này."


@router.get("/{record_id}/anh")
def get_record_image(record_id: int, principal: CurrentUser = Depends(get_current_user)):
    """Trả ảnh gốc của một ca, để bác sĩ duyệt và phụ huynh xem lại.

    Vì sao endpoint này tồn tại:
        Toàn bộ quy trình duyệt ca — bác sĩ chốt nhãn BITSS, và nhãn đó thành dữ liệu huấn
        luyện — dựa vào việc bác sĩ NHÌN ảnh. Trước endpoint này, ảnh chỉ được ghi xuống đĩa
        mà không có đường nào đọc ra: bác sĩ chốt nhãn cho một tấm ảnh họ không thể xem.

    Phân quyền dùng đúng assert_can_view_record như mọi đường đọc một ca khác: chủ hồ sơ và
    bác sĩ xem được, người khác nhận 404, bé đã xoá mềm thì không ai xem được. Đây là đường
    đọc thứ chín của hệ thống, và cũng là đường nhạy cảm nhất.

    KHÔNG xét đồng thuận chia sẻ với AI: đồng thuận đó là về việc gửi dữ liệu ra nhà cung
    cấp bên ngoài, còn ở đây ảnh đi từ máy chủ tới đúng người được phép xem.
    """
    with SessionLocal() as db:
        record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")
        assert_can_view_record(db, record, principal)
        duong_dan = record.image_path

    # Chỉ phục vụ file nằm TRONG thư mục upload. image_path do chính hệ thống sinh từ uuid,
    # nhưng nó nằm trong database — một dòng bị sửa tay, hay một bản khôi phục lỗi, không
    # được phép biến endpoint này thành công cụ đọc file bất kỳ trên máy chủ.
    thu_muc = os.path.realpath(UPLOAD_DIR)
    that = os.path.realpath(duong_dan or "")
    try:
        nam_trong = os.path.commonpath([thu_muc, that]) == thu_muc
    except ValueError:      # khác ổ đĩa trên Windows
        nam_trong = False
    if not nam_trong:
        logger.warning("ca #%s co image_path nam ngoai thu muc upload, tu choi phuc vu", record_id)
        raise HTTPException(status_code=404, detail=_KHONG_CO_ANH)
    if not os.path.isfile(that):
        raise HTTPException(status_code=404, detail=_KHONG_CO_ANH)

    duoi = os.path.splitext(that)[1].lstrip(".").lower()
    kieu = _KIEU_ANH.get(duoi)
    if kieu is None:
        raise HTTPException(status_code=404, detail=_KHONG_CO_ANH)

    with open(that, "rb") as f:
        du_lieu = f.read()

    # Chốt cuối: không bao giờ trả ra một ảnh còn siêu dữ liệu nhận dạng, bất kể nó được
    # lưu vào lúc nào. Ảnh upload từ nay đã được làm sạch, nhưng ảnh lưu trước đó, ảnh chép
    # tay vào, hay ảnh khôi phục từ bản sao lưu thì chưa chắc. Kiểm tra chỉ đọc phần đầu
    # file nên rẻ; chỉ khi thấy còn siêu dữ liệu mới phải mã hoá lại.
    try:
        with Image.open(BytesIO(du_lieu)) as anh:
            con_lai = lam_sach_anh.con_sieu_du_lieu(anh)
        if con_lai:
            logger.warning("ca #%s: anh luu tren dia con sieu du lieu %s, lam sach truoc khi tra",
                           record_id, con_lai)
            du_lieu = lam_sach_anh.lam_sach(du_lieu)
    except Exception:
        # Không mở được thì không kiểm được, và không kiểm được thì không trả.
        logger.exception("ca #%s: khong doc duoc anh tren dia", record_id)
        raise HTTPException(status_code=404, detail=_KHONG_CO_ANH)

    return Response(
        content=du_lieu,
        media_type=kieu,
        headers={
            # Ảnh bệnh án: không cho proxy hay bộ nhớ đệm dùng chung nào giữ lại bản sao.
            "Cache-Control": "private, no-store",
            # Trình duyệt phải tin đúng Content-Type, không tự đoán nội dung.
            "X-Content-Type-Options": "nosniff",
            # Tên file không chứa tên bé hay bất cứ thứ gì nhận dạng được.
            "Content-Disposition": f'inline; filename="ca_{record_id}.{duoi}"',
        },
    )


# Toàn bộ state machine của inference_status nằm ở ĐÚNG MỘT CHỖ này.
# Rải các phép kiểm tra trạng thái vào từng endpoint là cách chắc chắn nhất để hai
# endpoint dần dần cho phép hai tập chuyển trạng thái khác nhau mà không ai nhận ra.
#
#   queued ──► processing ──► completed   (đường chạy bình thường)
#      │            │
#      └────────────┴──────► failed ──► queued   (thất bại, và cho phép chạy lại)
#
# 'completed' là trạng thái cuối: đã có kết quả và có thể đã được bác sĩ duyệt,
# không cho phép quay ra bất kỳ đâu.
ALLOWED_TRANSITIONS = {
    "queued": {"processing", "completed", "failed"},
    "processing": {"completed", "failed"},
    "failed": {"queued"},
    "completed": set(),
}

MAX_ERROR_LENGTH = 2000


def _assert_transition(current: str, target: str) -> None:
    """Chặn mọi chuyển trạng thái không nằm trong bảng trên."""
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        huong_di = ", ".join(sorted(allowed)) if allowed else "không trạng thái nào (đây là trạng thái cuối)"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Không thể chuyển ca từ '{current}' sang '{target}'. "
                f"Từ '{current}' chỉ có thể chuyển sang: {huong_di}."
            )
        )


def _get_record_or_404(db, record_id: int) -> StoolRecord:
    record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")
    return record


class InferenceResultIn(BaseModel):
    ai_predicted_class: int
    ai_confidence: float


class InferenceFailureIn(BaseModel):
    error: str


@router.post("/{record_id}/inference-result")
def submit_inference_result(
        record_id: int,
        payload: InferenceResultIn,
        _worker: str = Depends(require_service_token),
):
    """Điểm nối để worker/script suy luận ghi kết quả của mô hình vào hệ thống.

    Đây là ranh giới giữa phần backend và phần mô hình: backend KHÔNG tự chạy mô hình,
    nó chỉ nhận (nhãn, độ tin cậy) rồi áp ràng buộc, gating và state machine. Nhờ vậy
    mô hình có thể được thay thế, huấn luyện lại hay chạy ở tiến trình khác mà không
    ảnh hưởng gì tới phần này.

    Bảo vệ bằng 'X-Service-Token' chứ không phải token người dùng: đây là ranh giới
    máy-với-máy. Tách hẳn loại thông tin xác thực khiến việc một token phụ huynh bị đem
    đi giả mạo kết quả AI trở thành bất khả về mặt cấu trúc, thay vì phụ thuộc vào việc
    có ai đó nhớ kiểm tra vai trò hay không.
    """
    # Nhãn hợp lệ lấy từ glossary BITSS, không hardcode lại dải 1-4 ở đây:
    # nếu thang điểm đổi, chỉ có một chỗ duy nhất phải sửa.
    if not bitss.is_valid_class(payload.ai_predicted_class):
        raise HTTPException(
            status_code=422,
            detail=f"'ai_predicted_class' phải thuộc {sorted(bitss.VALID_CLASSES)} theo thang BITSS."
        )

    if not (0.0 <= payload.ai_confidence <= 1.0):
        raise HTTPException(
            status_code=422,
            detail="'ai_confidence' phải nằm trong khoảng 0.0 đến 1.0."
        )

    with SessionLocal() as db:
        record = _get_record_or_404(db, record_id)
        _assert_transition(record.inference_status, "completed")

        # Uncertainty gating: đánh dấu ca mô hình không chắc chắn để bác sĩ ưu tiên xem.
        gating = uncertainty.evaluate(payload.ai_confidence)

        try:
            record.ai_predicted_class = payload.ai_predicted_class
            record.ai_confidence = payload.ai_confidence
            record.is_uncertain = gating["is_uncertain"]
            record.review_status = gating["review_status"]
            record.inference_status = "completed"
            db.commit()
            db.refresh(record)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể lưu kết quả suy luận, vui lòng thử lại.")

        return {
            "status": "success",
            "message": "Đã ghi nhận kết quả suy luận.",
            "data": {
                "record_id": record.id,
                "inference_status": record.inference_status,
                "ai_predicted_class": record.ai_predicted_class,
                # Nhãn chữ lấy từ glossary để client không phải tự dịch số sang tên
                "ai_bitss_nhan": bitss.label(record.ai_predicted_class),
                "ai_confidence": record.ai_confidence,
                "is_uncertain": record.is_uncertain,
                "review_status": record.review_status,
                "gating": {
                    "nguong_ap_dung": gating["nguong_ap_dung"],
                    "ly_do": gating["ly_do"],
                },
            }
        }


@router.post("/{record_id}/inference-start")
def mark_inference_started(record_id: int, _worker: str = Depends(require_service_token)):
    """Worker báo đã nhận ca và bắt đầu chạy mô hình: queued -> processing.

    Có bước này thì mới phân biệt được 'ca chưa ai đụng tới' và 'ca đang chạy dở'.
    Nếu thiếu, một ca kẹt mãi ở 'queued' không nói lên được là hàng chờ đang tắc hay
    worker đã chết giữa chừng.
    """
    with SessionLocal() as db:
        record = _get_record_or_404(db, record_id)
        _assert_transition(record.inference_status, "processing")

        try:
            record.inference_status = "processing"
            db.commit()
            db.refresh(record)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể cập nhật trạng thái, vui lòng thử lại.")

        return {
            "status": "success",
            "message": "Ca đã chuyển sang trạng thái đang xử lý.",
            "data": {"record_id": record.id, "inference_status": record.inference_status},
        }


@router.post("/{record_id}/inference-failed")
def mark_inference_failed(
        record_id: int,
        payload: InferenceFailureIn,
        _worker: str = Depends(require_service_token),
):
    """Worker báo suy luận thất bại: queued/processing -> failed, kèm lý do.

    Bắt buộc có lý do. Một ca 'failed' rỗng nghĩa là lúc vận hành chỉ biết ca hỏng
    chứ không biết hỏng vì đâu, và không có cách nào phân biệt lỗi tạm thời (đáng chạy
    lại) với lỗi vĩnh viễn (ảnh hỏng, chạy lại bao nhiêu lần cũng thế).
    """
    reason = (payload.error or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="'error' không được để trống: phải ghi rõ lý do thất bại.")

    # Cắt bớt còn hơn từ chối: mất phần đuôi traceback vẫn tốt hơn mất toàn bộ báo cáo lỗi.
    if len(reason) > MAX_ERROR_LENGTH:
        reason = reason[:MAX_ERROR_LENGTH] + " ...(đã cắt bớt)"

    with SessionLocal() as db:
        record = _get_record_or_404(db, record_id)
        _assert_transition(record.inference_status, "failed")

        try:
            record.inference_status = "failed"
            record.inference_error = reason
            # Ca thất bại tuyệt đối không được giữ lại kết quả nửa vời:
            # CHECK constraint chk_inference_status_and_result cũng bắt buộc điều này.
            record.ai_predicted_class = None
            record.ai_confidence = None
            record.is_uncertain = False
            db.commit()
            db.refresh(record)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể ghi nhận thất bại, vui lòng thử lại.")

        return {
            "status": "success",
            "message": "Đã ghi nhận ca suy luận thất bại.",
            "data": {
                "record_id": record.id,
                "inference_status": record.inference_status,
                "inference_error": record.inference_error,
            },
        }


@router.post("/{record_id}/inference-retry")
def retry_inference(record_id: int, _worker: str = Depends(require_service_token)):
    """Đưa một ca đã thất bại trở lại hàng chờ: failed -> queued.

    Xoá luôn thông báo lỗi cũ. Nếu giữ lại, một ca đang chờ chạy lại sẽ mang theo lỗi
    của lần trước và người đọc tưởng nó vẫn đang hỏng (CHECK chk_error_only_when_failed
    cũng chặn tình huống này ở tầng database).
    """
    with SessionLocal() as db:
        record = _get_record_or_404(db, record_id)
        _assert_transition(record.inference_status, "queued")

        try:
            record.inference_status = "queued"
            record.inference_error = None
            db.commit()
            db.refresh(record)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể đưa ca trở lại hàng chờ, vui lòng thử lại.")

        return {
            "status": "success",
            "message": "Ca đã được đưa trở lại hàng chờ xử lý.",
            "data": {"record_id": record.id, "inference_status": record.inference_status},
        }
