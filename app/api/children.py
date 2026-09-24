"""Hồ sơ bệnh nhi: tạo và liệt kê, luôn gắn với người sở hữu.

Vì sao file này xuất hiện ở đợt xác thực chứ không sớm hơn:
    Trước P3 không tồn tại khái niệm "chủ hồ sơ", nên một endpoint tạo hồ sơ bé sẽ sinh
    ra dữ liệu không ai sở hữu — đúng thứ vừa phải bịt lại bằng children.owner_user_id.
    Có quyền sở hữu rồi thì endpoint này mới viết được một cách đúng đắn, và nó là mảnh
    còn thiếu để một tài khoản dùng được trọn vẹn (đăng ký -> tạo hồ sơ bé -> upload).
"""
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from setup_database import SessionLocal, Child, StoolRecord
from app.api.deps import CurrentUser, get_current_user
from app.services import chat_history, yeu_to_lam_sang
from app.services.authz import da_xoa_mem

router = APIRouter(prefix="/api/v1/children", tags=["Children"])

MAX_NAME_LENGTH = 100
MAX_NOTES_LENGTH = 5000
MAX_AGE_YEARS = 18          # ngoài phạm vi nhi khoa thì gần như chắc chắn là gõ nhầm năm sinh
MAX_WEIGHT_KG = 150.0


class ChildIn(BaseModel):
    name: str
    date_of_birth: date
    feeding_type: str
    weight_kg: Optional[float] = None
    # Ghi chú tự do, dành cho BÁC SĨ đọc. Không còn được gửi cho model — xem
    # app/services/yeu_to_lam_sang.py để biết vì sao.
    medical_notes: Optional[str] = None
    # Yếu tố lâm sàng nền, chọn từ từ vựng có kiểm soát. ĐÂY là phần model đọc được.
    yeu_to_lam_sang: Optional[List[str]] = None
    # Không có 'owner_user_id': chủ hồ sơ lấy từ token. Cho client tự khai chủ sở hữu
    # thì bất kỳ ai cũng gán hồ sơ bé vào tài khoản người khác, hoặc tệ hơn, tự nhận
    # quyền sở hữu hồ sơ của trẻ khác.


def _validate(payload: ChildIn) -> tuple:
    """Kiểm tra toàn bộ dữ liệu vào. Trả về (tên đã chuẩn hoá, chuỗi yếu tố lâm sàng)."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="'name' không được để trống.")
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(status_code=422, detail=f"'name' không được dài quá {MAX_NAME_LENGTH} ký tự.")

    if not (payload.feeding_type or "").strip():
        raise HTTPException(status_code=422, detail="'feeding_type' không được để trống.")

    today = datetime.now(timezone.utc).date()
    if payload.date_of_birth > today:
        raise HTTPException(status_code=422, detail="'date_of_birth' không thể nằm ở tương lai.")
    if (today - payload.date_of_birth).days > MAX_AGE_YEARS * 366:
        raise HTTPException(
            status_code=422,
            detail=f"'date_of_birth' quá xa trong quá khứ (hệ thống phục vụ trẻ dưới {MAX_AGE_YEARS} tuổi)."
        )

    # Cân nặng đi thẳng vào phần tính toán lâm sàng, nên số vô lý phải chặn tại cửa chứ
    # không để nó lặng lẽ làm lệch mọi đánh giá về sau.
    if payload.weight_kg is not None and not (0 < payload.weight_kg <= MAX_WEIGHT_KG):
        raise HTTPException(status_code=422, detail=f"'weight_kg' phải lớn hơn 0 và không quá {MAX_WEIGHT_KG}.")

    if payload.medical_notes and len(payload.medical_notes) > MAX_NOTES_LENGTH:
        raise HTTPException(status_code=422, detail=f"'medical_notes' không được dài quá {MAX_NOTES_LENGTH} ký tự.")

    # Mã lạ bị từ chối thẳng chứ không lặng lẽ bỏ qua: bỏ qua nghĩa là phụ huynh tưởng đã
    # khai dị ứng đạm sữa bò cho con trong khi hệ thống không ghi nhận gì — một loại mất
    # dữ liệu lâm sàng không có dấu hiệu nào để phát hiện.
    try:
        yeu_to = yeu_to_lam_sang.chuan_hoa(payload.yeu_to_lam_sang)
    except yeu_to_lam_sang.MaKhongHopLe as loi:
        raise HTTPException(status_code=422, detail=str(loi))

    return name, yeu_to


@router.get("/yeu-to-lam-sang")
def danh_muc_yeu_to_lam_sang():
    """Từ vựng yếu tố lâm sàng, để client dựng form chọn.

    Mở cho cả người chưa đăng nhập: đây là danh mục thuật ngữ do hệ thống định nghĩa, không
    chứa dữ liệu của ai. Đóng lại chỉ khiến màn hình đăng ký hồ sơ phải chờ có token mới
    vẽ được form.

    Có endpoint này thì client không phải chép cứng danh sách mã — và một bản chép cứng
    chính là chỗ sẽ lạc hậu vào lần đầu tiên danh mục được bác sĩ rà soát lại.

    Đặt TRƯỚC route '/{child_id}' là bắt buộc: FastAPI khớp route theo thứ tự khai báo,
    nên nếu đảo lại thì 'yeu-to-lam-sang' sẽ bị nuốt thành một child_id và trả 422.
    """
    return {"total": len(yeu_to_lam_sang.MA_HOP_LE), "yeu_to": yeu_to_lam_sang.danh_muc()}


@router.post("", status_code=201)
def create_child(payload: ChildIn, user: CurrentUser = Depends(get_current_user)):
    """Tạo hồ sơ bé. Người tạo mặc nhiên là chủ sở hữu, không thể chỉ định người khác."""
    name, yeu_to = _validate(payload)

    with SessionLocal() as db:
        child = Child(
            owner_user_id=user.id,
            name=name,
            date_of_birth=payload.date_of_birth,
            feeding_type=payload.feeding_type.strip(),
            weight_kg=payload.weight_kg,
            medical_notes=payload.medical_notes,
            yeu_to_lam_sang=yeu_to,
        )
        try:
            db.add(child)
            db.commit()
            db.refresh(child)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể tạo hồ sơ bé, vui lòng thử lại.")

        return {
            "status": "success",
            "message": "Đã tạo hồ sơ bé.",
            "data": {
                "child_id": child.id,
                "name": child.name,
                "date_of_birth": child.date_of_birth.isoformat(),
                "feeding_type": child.feeding_type,
                "weight_kg": child.weight_kg,
                "yeu_to_lam_sang": [y["ma"] for y in yeu_to_lam_sang.doc(child.yeu_to_lam_sang)],
            },
        }


@router.get("")
def list_my_children(user: CurrentUser = Depends(get_current_user)):
    """Liệt kê hồ sơ bé của CHÍNH người đang đăng nhập.

    Không có tham số lọc theo chủ sở hữu, và đó là chủ ý: một tham số như
    '?owner_user_id=' sẽ mời gọi đúng kiểu tấn công mà lớp phân quyền sinh ra để chặn.
    Phạm vi dữ liệu do token quyết định, không do client quyết định.
    """
    with SessionLocal() as db:
        children = (
            db.query(Child)
            .filter(Child.owner_user_id == user.id, Child.deleted_at.is_(None))
            .order_by(Child.id.asc())
            .all()
        )
        return {
            "total": len(children),
            "children": [
                {
                    "child_id": c.id,
                    "name": c.name,
                    "date_of_birth": c.date_of_birth.isoformat(),
                    "feeding_type": c.feeding_type,
                    "weight_kg": c.weight_kg,
                    "yeu_to_lam_sang": [y["ma"] for y in yeu_to_lam_sang.doc(c.yeu_to_lam_sang)],
                }
                for c in children
            ],
        }


@router.delete("/{child_id}")
def soft_delete_child(child_id: int, user: CurrentUser = Depends(get_current_user)):
    """Xoá hồ sơ bé theo yêu cầu của gia đình. Xoá MỀM: đặt cờ deleted_at.

    Vì sao endpoint này cần tồn tại:
        Cột deleted_at có từ đầu và được tôn trọng chặt chẽ ở mọi đường đọc — nhưng
        không có đường nào ĐẶT nó. Nghĩa là quyền xoá dữ liệu của gia đình mới chỉ
        được thi hành ở nửa sau: hệ thống biết phải giấu gì, nhưng người nhà không có
        cách nào yêu cầu giấu. Cơ chế xoá mà chỉ quản trị viên chạm tay vào database
        mới kích hoạt được thì trên thực tế là không có.

    Vì sao chỉ CHỦ hồ sơ được gọi, không dùng can_view_child:
        Hàm đó cho bác sĩ và admin xem MỌI hồ sơ. Mượn nó ở đây sẽ âm thầm nới quyền
        xoá rộng bằng quyền đọc, tức là một bác sĩ xoá được dữ liệu của gia đình người
        khác. Đây đúng là cái bẫy mà endpoint upload đã tránh bằng cùng một cách.

    Vì sao xoá lần hai trả 404 chứ không phải 200:
        Cùng lý do đã ghi ở endpoint đăng xuất: báo thành công cho một thao tác không
        xảy ra là nói dối client. Hồ sơ đã xoá thì vô hình với mọi đường đọc, nên "không
        tìm thấy" vừa đúng sự thật vừa nhất quán với phần còn lại của hệ thống.

    Xoá hồ sơ bé cũng xoá TOÀN BỘ lịch sử trò chuyện của tài khoản:
        Nghe như quá tay, nhưng đây là lựa chọn duy nhất trung thực. Lịch sử chat buộc
        vào user_id và chứa văn bản tự do — phụ huynh gõ "bé Khôi dạo này thế nào", trợ
        lý trả lời kèm tên bé, và cả hai câu đó nằm lại trong chat_messages. Bảng ấy
        không có child_id nên không lọc theo bé được, và dò theo tên trong văn bản tự do
        thì vừa sót ("con tôi", "cháu") vừa tạo cảm giác an toàn sai.

        Không xoá thì yêu cầu xoá dữ liệu bị vô hiệu hoá một cách âm thầm: hồ sơ biến mất
        khỏi mọi danh sách, nhưng tên bé vẫn nằm trong lịch sử VÀ vẫn được nạp ngược vào
        ngữ cảnh model ở lượt chat kế tiếp. Đã dựng lại được đúng tình huống đó trước khi
        có dòng này.

        Với gia đình một con — trường hợp phổ biến — đây chính xác là điều họ yêu cầu.
        Với nhiều con thì rộng quá tay, nhưng sai về phía tôn trọng yêu cầu xoá là hướng
        sai đúng. Số tin đã xoá được trả về để người dùng thấy chuyện đó đã xảy ra chứ
        không phát hiện ra sau.

    Xoá MỀM chứ không xoá cứng, và đây là giới hạn cần nói rõ:
        Dòng dữ liệu vẫn nằm trong database, ảnh vẫn nằm trên đĩa. Chúng chỉ không còn
        đọc được qua bất kỳ đường nào của ứng dụng. Xoá cứng sẽ đụng vào khoá ngoại
        RESTRICT của stool_records và doctor_reviews — tức là xoá luôn cả kết luận đã
        ký của bác sĩ, thứ mà hồ sơ bệnh án không cho phép bốc khỏi lịch sử. Xoá thật
        sự khỏi đĩa là một quy trình vận hành riêng, có kiểm soát, không phải một lời
        gọi HTTP (cột pseudonymized_at đã có sẵn cho bước đó).
    """
    with SessionLocal() as db:
        child = db.query(Child).filter(Child.id == child_id).first()

        # MỘT thông báo duy nhất cho cả ba trường hợp: không tồn tại, không phải của
        # mình, và đã xoá rồi. Tách ra sẽ biến endpoint thành công cụ dò xem hệ thống
        # đang quản lý những hồ sơ nào — đúng thứ mà việc trả 404 thay cho 403 ở khắp
        # nơi trong dự án này sinh ra để chặn.
        if not child or child.owner_user_id != user.id or da_xoa_mem(child):
            raise HTTPException(status_code=404, detail=f"Không tìm thấy bệnh nhi có ID: {child_id}")

        # Đếm TRƯỚC khi xoá, để nói cho người nhà biết thao tác này vừa giấu đi bao
        # nhiêu ca. Xoá một hồ sơ mà không cho biết quy mô hệ quả là bắt họ quyết định
        # trong bóng tối.
        so_ca = db.query(StoolRecord).filter(StoolRecord.child_id == child.id).count()

        try:
            # Cả hai việc trong CÙNG một transaction. Tách làm hai lần commit sẽ tạo ra
            # một cửa sổ mà hồ sơ đã xoá nhưng lịch sử vẫn còn tên bé — đúng lỗ hổng mà
            # dòng này sinh ra để bịt, chỉ khác là nó xuất hiện ngẫu nhiên thay vì luôn
            # luôn, và loại lỗi đó khó truy hơn hẳn.
            so_tin_chat = chat_history.xoa_lich_su_chua_commit(db, user)
            child.deleted_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể xoá hồ sơ bé, vui lòng thử lại.")

        return {
            "status": "success",
            "message": (
                "Đã xoá hồ sơ bé. Dữ liệu không còn hiển thị ở bất kỳ đâu trong hệ thống, "
                "và lịch sử trò chuyện với trợ lý cũng đã được xoá."
            ),
            "data": {
                "child_id": child_id,
                "so_ca_an_di": so_ca,
                "so_tin_chat_da_xoa": so_tin_chat,
            },
        }
