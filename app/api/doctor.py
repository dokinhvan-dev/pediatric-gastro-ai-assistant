"""Endpoint để bác sĩ chốt kết luận lâm sàng cho một ca đã có kết quả AI.

Vì sao tách khỏi records.py:
    records.py lo việc tiếp nhận ảnh và vòng đời suy luận của máy. Ở đây là phán quyết
    của con người — nguồn sự thật cuối cùng của hệ thống, và cũng là nguồn nhãn để
    huấn luyện lại mô hình về sau. Hai mối quan tâm này có lý do thay đổi khác nhau
    nên không nên nằm chung một file.

Vì sao endpoint này tồn tại:
    Từ khi chốt chính sách "mọi ca đều phải qua bác sĩ", hàng chờ duyệt chỉ có đường
    vào mà không có đường ra: mọi ca đổ về review_status='pending' và mắc kẹt ở đó
    vĩnh viễn vì không có cách nào ghi vào bảng doctor_reviews.

Danh tính người chốt lấy từ đâu (P3):
    'clinician_name' CỐ Ý không còn nằm trong request body. Trước đây client tự khai tên,
    nghĩa là ngay cả sau khi có đăng nhập, một bác sĩ vẫn ký được kết luận dưới tên đồng
    nghiệp — và chữ ký trong hồ sơ bệnh án mà giả mạo được thì toàn bộ vết kiểm toán chỉ
    là chuyện kể. Giờ tên và id đều lấy từ token đã xác thực.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from setup_database import SessionLocal, StoolRecord, DoctorReview
from app.api.deps import CurrentUser, assert_can_view_record, get_current_user, require_doctor
from app.services import bitss

router = APIRouter(prefix="/api/v1/records", tags=["Doctor Review"])

# Khớp chính xác với CHECK constraint chk_review_decision_valid
VALID_DECISIONS = {"approved", "rejected", "needs_more_info"}

# 'needs_more_info' CỐ Ý không phải quyết định cuối: nó có nghĩa là "tôi chưa kết luận
# được, cần thêm thông tin", nên ca phải còn duyệt lại được. Nếu đánh dấu nó là final,
# partial unique index sẽ khoá ca lại vĩnh viễn — đúng cái ngược với ý nghĩa của nó.
FINAL_DECISIONS = {"approved", "rejected"}

MAX_NOTES_LENGTH = 5000


class DoctorReviewIn(BaseModel):
    # Không có 'clinician_name': danh tính người chốt lấy từ token, không phải từ client.
    decision: str
    confirmed_bitss: Optional[int] = None
    clinical_notes: Optional[str] = None
    # Đánh giá CHẤT LƯỢNG của bác sĩ, không phải quyền được dùng. Để trống thì ca approved
    # mặc định được coi là đủ chất lượng; bác sĩ tắt khi nhãn đúng nhưng ảnh không phù hợp
    # làm dữ liệu học (ảnh mờ, bố cục lạ, ca hiếm dễ làm lệch tập dữ liệu).
    #
    # Mặc định bật KHÔNG có nghĩa ca đó sẽ vào tập huấn luyện: điều kiện xuất còn đòi đồng
    # thuận nghiên cứu của gia đình cho đúng ảnh đó (app/services/xuat_du_lieu.py). Trước đây
    # cờ này là điều kiện duy nhất, tức là bác sĩ duyệt xong là ảnh của một đứa trẻ thành dữ
    # liệu huấn luyện mà gia đình chưa từng được hỏi.
    eligible_for_training: Optional[bool] = None


def _validate_payload(payload: DoctorReviewIn) -> None:
    """Toàn bộ kiểm tra dữ liệu vào, chạy TRƯỚC khi mở session database.

    Các quy tắc ở đây lặp lại đúng những gì CHECK constraint trong database đã ràng
    buộc. Chủ ý là vậy: database là chốt chặn cuối không bao giờ được phép thủng, còn
    ở đây trả lỗi tiếng Việt rõ ràng cho người dùng thay vì để họ nhận một lỗi 500 từ
    IntegrityError.
    """
    if payload.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"'decision' phải là một trong: {sorted(VALID_DECISIONS)}."
        )

    if payload.decision == "approved":
        if not bitss.is_valid_class(payload.confirmed_bitss):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Khi duyệt (approved) bắt buộc phải chốt 'confirmed_bitss' "
                    f"thuộc {sorted(bitss.VALID_CLASSES)} theo thang BITSS."
                )
            )
    else:
        # Từ chối hoặc cần thêm thông tin thì KHÔNG được kèm nhãn: một nhãn nằm trên ca
        # chưa được duyệt sẽ bị các bước sau hiểu nhầm là nhãn đã xác nhận.
        if payload.confirmed_bitss is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Quyết định '{payload.decision}' không được kèm 'confirmed_bitss'. "
                    "Chỉ ca được duyệt (approved) mới có nhãn chuẩn."
                )
            )

    if payload.clinical_notes and len(payload.clinical_notes) > MAX_NOTES_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"'clinical_notes' không được dài quá {MAX_NOTES_LENGTH} ký tự."
        )

    # Không im lặng hạ cờ xuống False: nếu bác sĩ chủ động bật cờ huấn luyện trên một ca
    # không được duyệt, đó là hiểu nhầm cần báo cho họ biết, không phải thứ để lặng lẽ sửa.
    if payload.eligible_for_training and payload.decision != "approved":
        raise HTTPException(
            status_code=422,
            detail=(
                "Chỉ ca được duyệt (approved) mới có thể dùng cho huấn luyện. "
                f"Ca '{payload.decision}' không có nhãn chuẩn nên không đủ điều kiện."
            )
        )


def _ket_luan_cuoi(db, record_id: int):
    return (
        db.query(DoctorReview)
        .filter(DoctorReview.record_id == record_id, DoctorReview.is_final)
        .first()
    )


def _da_chot(review) -> HTTPException:
    """Một câu trả lời duy nhất cho "ca này đã có kết luận cuối", dù phát hiện ở bước kiểm
    tra trước hay ở ràng buộc database sau — bác sĩ không cần biết mình thua ở vòng nào."""
    return HTTPException(
        status_code=409,
        detail=(
            f"Ca này đã được {review.clinician_name} chốt "
            f"('{review.decision}') và không thể duyệt lại."
        ),
    )


@router.post("/{record_id}/review")
def submit_doctor_review(
        record_id: int,
        payload: DoctorReviewIn,
        doctor: CurrentUser = Depends(require_doctor),
):
    """Bác sĩ chốt kết luận cho một ca đã có kết quả AI.

    approved / rejected  -> là quyết định CUỐI CÙNG, khoá ca lại, không duyệt thêm.
    needs_more_info      -> chưa chốt, ca vẫn duyệt lại được sau khi có thêm thông tin.

    Chỉ tài khoản vai trò 'doctor' gọi được. Admin cố ý KHÔNG nằm trong nhóm này: quyền
    quản trị hệ thống không đồng nghĩa với năng lực đọc ảnh phân nhi khoa, mà nhãn do
    admin chốt lại chui vào tập huấn luyện y hệt nhãn của bác sĩ thật.

    Ca thuộc hồ sơ bé đã xoá mềm thì không duyệt được nữa, và trả 404 y như đường đọc.
    """
    _validate_payload(payload)

    is_final = payload.decision in FINAL_DECISIONS

    with SessionLocal() as db:
        record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")

        # Không xem được thì không duyệt được. Cố ý gọi lại ĐÚNG hàm mà GET .../reviews
        # dùng: hai endpoint trên cùng một ca phải kể cùng một câu chuyện về việc ca đó có
        # tồn tại hay không. Thiếu dòng này thì hồ sơ bé đã xoá mềm biến mất khỏi mọi đường
        # đọc nhưng vẫn ký được kết luận nếu biết record_id — và nhãn ấy còn mặc định
        # eligible_for_training, tức dữ liệu người nhà xin xoá đi thẳng vào tập huấn luyện.
        #
        # Đặt TRƯỚC kiểm tra inference_status: phân quyền phải trả lời trước mọi bình luận
        # về trạng thái của ca, nếu không thì một ca 'queued' của bé đã xoá sẽ nhận 409
        # "chưa có kết quả AI để duyệt" — câu đó tự nó xác nhận ca ấy có thật.
        #
        # Các điểm nối của worker suy luận CỐ Ý không có chốt này: chúng hoàn tất công việc
        # đã xếp hàng từ trước lúc xoá, chặn giữa đường chỉ để lại ca mắc kẹt ở 'processing'
        # chứ không che thêm được gì — ca đó đã không còn đọc được từ bất cứ đâu.
        assert_can_view_record(db, record, doctor)

        # Chỉ duyệt ca đã có kết quả AI. Đây là quyết định phạm vi có chủ ý: việc bác sĩ
        # đọc trực tiếp một ca mà mô hình chạy hỏng là một quy trình khác (primary read),
        # không phải "duyệt lại kết quả của máy", nên không gộp vào đây.
        if record.inference_status != "completed":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ca này đang ở trạng thái suy luận '{record.inference_status}', chưa có kết quả AI để duyệt. "
                    "Chỉ duyệt được ca đã 'completed'."
                )
            )

        existing_final = _ket_luan_cuoi(db, record.id)
        if existing_final:
            raise _da_chot(existing_final)

        eligible = (
            payload.eligible_for_training
            if payload.eligible_for_training is not None
            else (payload.decision == "approved")
        )

        review = DoctorReview(
            record_id=record.id,
            # Cả hai trường đều lấy từ token đã xác thực. clinician_name là ảnh chụp tên
            # tại thời điểm ký, cố ý lặp dữ liệu với users.full_name: bác sĩ đổi tên sau
            # này thì hồ sơ cũ vẫn phải hiện đúng cái tên đã ký lúc đó.
            clinician_user_id=doctor.id,
            clinician_name=doctor.full_name,
            confirmed_bitss=payload.confirmed_bitss,
            decision=payload.decision,
            clinical_notes=payload.clinical_notes,
            is_final=is_final,
            eligible_for_training=eligible,
        )

        try:
            db.add(review)
            # Cập nhật record trong CÙNG một transaction với việc tạo review.
            # Nếu tách hai lần commit, một lần hỏng sẽ để lại review đã ghi nhưng
            # review_status của ca vẫn là 'pending' — hai nguồn dữ liệu nói hai điều
            # khác nhau mà không có cách nào phát hiện về sau.
            record.review_status = payload.decision
            db.commit()
            db.refresh(review)
            db.refresh(record)
        except IntegrityError:
            db.rollback()
            # Hai bác sĩ chốt cùng một ca gần như đồng thời: cả hai cùng qua bước kiểm tra
            # "đã có kết luận cuối chưa" ở trên, rồi partial unique index
            # uq_one_final_review_per_record chặn người commit sau. Dữ liệu vẫn đúng — index
            # đã làm việc của nó. Thứ sai là câu trả lời: bản trước để lỗi này rơi vào nhánh
            # chung và trả 500 "vui lòng thử lại", trong khi thử lại thì nhận 409. Bác sĩ cần
            # biết ngay là đồng nghiệp đã chốt, và chốt thế nào.
            da_co = _ket_luan_cuoi(db, record.id)
            if da_co:
                raise _da_chot(da_co)
            # IntegrityError mà không tìm thấy kết luận cuối nào thì là một ràng buộc khác bị
            # vi phạm — không đoán lý do, báo đúng là lỗi hệ thống.
            raise HTTPException(status_code=500, detail="Không thể lưu kết quả duyệt, vui lòng thử lại.")
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="Không thể lưu kết quả duyệt, vui lòng thử lại.")

        # Khoảng cách thứ bậc giữa máy và người: nhầm 1 bậc (cứng <-> khuôn) nhẹ hơn
        # nhiều so với nhầm 3 bậc (cứng <-> toé nước, tức mô hình đang thật sự hỏng).
        # Đây là số liệu thô cho weighted kappa khi đánh giá mô hình về sau.
        khoang_cach = bitss.ordinal_distance(record.ai_predicted_class, review.confirmed_bitss)

        return {
            "status": "success",
            "message": "Đã ghi nhận kết luận của bác sĩ.",
            "data": {
                "review_id": review.id,
                "record_id": record.id,
                "clinician_name": review.clinician_name,
                "decision": review.decision,
                "confirmed_bitss": review.confirmed_bitss,
                "confirmed_bitss_ten": bitss.label(review.confirmed_bitss),
                "is_final": review.is_final,
                "eligible_for_training": review.eligible_for_training,
                "review_status_cua_ca": record.review_status,
                "doi_chieu_voi_ai": {
                    "ai_predicted_class": record.ai_predicted_class,
                    "ai_bitss_nhan": bitss.label(record.ai_predicted_class),
                    "khoang_cach_thu_bac": khoang_cach,
                    "ai_dung_khong": (khoang_cach == 0) if khoang_cach is not None else None,
                },
            },
        }


@router.get("/{record_id}/reviews")
def list_doctor_reviews(record_id: int, principal: CurrentUser = Depends(get_current_user)):
    """Xem toàn bộ lịch sử duyệt của một ca, mới nhất trước.

    Một ca có thể có nhiều lượt 'needs_more_info' trước khi được chốt, nên lịch sử
    duyệt là chuỗi chứ không phải một bản ghi duy nhất.

    Mở cho cả bác sĩ lẫn phụ huynh sở hữu ca — kết luận của bác sĩ là thứ người nhà
    CẦN đọc được, khoá lại chỉ cho bác sĩ thì sản phẩm mất luôn ý nghĩa. Nhưng mỗi
    người chỉ đọc được ca của mình.

    Đổi từ get_optional_principal sang get_current_user ở P4, và lý do không phải là
    "giờ ca nào cũng có chủ" mà là một rò rỉ thật:
        Thân hàm tra record TRƯỚC rồi mới gọi assert_can_view_record. Với người gọi
        không token, thứ tự đó trả 404 cho record_id không tồn tại nhưng 401 cho
        record_id có thật — tức là đếm được số ca trong hệ thống mà không cần đăng
        nhập. Dependency chạy xong trước khi thân hàm bắt đầu, nên chuyển lên đây thì
        mọi người lạ đều nhận đúng một câu trả lời, bất kể record_id có thật hay không.
    """
    with SessionLocal() as db:
        record = db.query(StoolRecord).filter(StoolRecord.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy ca phân tích có ID: {record_id}")

        assert_can_view_record(db, record, principal)

        reviews = (
            db.query(DoctorReview)
            .filter(DoctorReview.record_id == record_id)
            .order_by(DoctorReview.reviewed_at.desc(), DoctorReview.id.desc())
            .all()
        )

        return {
            "record_id": record_id,
            "review_status": record.review_status,
            "total_reviews": len(reviews),
            "da_chot": any(r.is_final for r in reviews),
            "reviews": [
                {
                    "review_id": r.id,
                    "clinician_name": r.clinician_name,
                    "decision": r.decision,
                    "confirmed_bitss": r.confirmed_bitss,
                    "confirmed_bitss_ten": bitss.label(r.confirmed_bitss),
                    "clinical_notes": r.clinical_notes,
                    "is_final": r.is_final,
                    "eligible_for_training": r.eligible_for_training,
                    "reviewed_at": r.reviewed_at.strftime("%Y-%m-%d %H:%M"),
                }
                for r in reviews
            ],
        }
