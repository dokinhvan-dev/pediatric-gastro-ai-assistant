"""Nơi DUY NHẤT quyết định ca nào được rời hệ thống để làm dữ liệu huấn luyện.

Vì sao phải có đúng một chỗ:
    Trước module này không có đường xuất dữ liệu nào, và thứ duy nhất trông giống "được phép
    huấn luyện" là cờ doctor_reviews.eligible_for_training — cờ tự bật True khi bác sĩ duyệt
    approved, trong khi gia đình chưa từng được hỏi. Ngày phần mô hình bắt đầu lấy dữ liệu,
    ai viết câu truy vấn xuất cũng sẽ tin vào cờ đó. Đặt điều kiện đầy đủ ở một chỗ, có test
    canh, thì không còn câu truy vấn nào khác để tin nhầm.

Điều kiện, là phép AND — không trường đơn lẻ nào đủ:
    1. Bác sĩ đã chốt kết luận CUỐI, quyết định 'approved', nhãn BITSS hợp lệ.
    2. Bác sĩ đánh giá ảnh đủ chất lượng làm dữ liệu (eligible_for_training).
    3. Gia đình còn đồng thuận nghiên cứu cho ĐÚNG ẢNH NÀY, với thông báo hiện hành.
    4. Hồ sơ bé chưa bị xoá mềm — gia đình đã yêu cầu xoá thì dữ liệu không đi đâu nữa.
    Không thể ép bằng CHECK constraint vì dữ liệu nằm ở ba bảng; nên ép ở đây, và kiểm lại
    điều kiện 3 bằng chính hàm của dong_y_nghien_cuu để truy vấn SQL không thể lệch nghĩa.

Mã bệnh nhân ẩn danh:
    Hợp đồng dữ liệu của phần mô hình đòi "mã ẩn danh duy nhất cho mỗi trẻ, không dùng số hồ
    sơ thật" — và đó là trường quan trọng nhất của họ, vì tách train/test theo bệnh nhân dựa
    vào nó. Dùng HMAC-SHA256 của child_id với một khoá riêng:
      - Không suy ngược được ra child_id nếu không có khoá.
      - ỔN ĐỊNH qua các lần xuất: cùng một bé luôn cùng một mã, nên phép chia theo bệnh nhân
        của bên mô hình không rò giữa các phiên bản dữ liệu.
    Khoá RIÊNG, không dùng lại JWT_SECRET_KEY: xoay khoá JWT (việc nên làm định kỳ) sẽ đổi
    toàn bộ mã bệnh nhân và phá tính ổn định ở trên.

Cột nhãn CỐ Ý tên là 'nhan_bitss', không phải 'label':
    Loader bên mô hình (src/dataset.py) đọc cột 'label' theo quy ước 0-6 = Bristol Type_1..7
    bằng int(row["label"]), không kiểm miền giá trị. Nhãn ở đây là BITSS 1-4. Đặt tên
    'label' thì BITSS 2 bị đọc thành Type_3 và mô hình học sai MÀ KHÔNG AI BIẾT. Tên khác
    khiến loader bên đó báo lỗi "thiếu cột bắt buộc" ngay — hỏng ầm ĩ thay vì hỏng im lặng.
    Việc thống nhất 4 lớp BITSS hay 7 lớp Bristol là quyết định của hai bên, chưa được chốt.
"""
import hashlib
import hmac
import logging
import os

from app.services import bitss, dong_y_nghien_cuu

logger = logging.getLogger("bitss.xuat_du_lieu")

TEN_BIEN_KHOA = "KHOA_MA_AN_DANH_DU_LIEU"
DO_DAI_KHOA_TOI_THIEU = 32

# Cột của metadata.csv. Mục 32 trong verify_task.py đối chiếu danh sách này với nội dung
# thông báo nghiên cứu: xuất thêm một cột mà thông báo chưa từng nói tới thì bài test báo
# trượt. Thông báo đồng thuận lệch với dữ liệu thật đi ra thì không còn là thông báo.
COT_XUAT = ("image_path", "patient_id", "nhan_bitss", "thang_tuoi", "che_do_an")


class ThieuKhoaAnDanh(RuntimeError):
    """Không có khoá tạo mã ẩn danh thì không xuất — không bao giờ lùi về child_id thật."""


def lay_khoa() -> bytes:
    khoa = os.getenv(TEN_BIEN_KHOA, "")
    if len(khoa.strip()) < DO_DAI_KHOA_TOI_THIEU:
        raise ThieuKhoaAnDanh(
            f"Thieu {TEN_BIEN_KHOA} (toi thieu {DO_DAI_KHOA_TOI_THIEU} ky tu). Sinh khoa:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "GIU KHOA NAY CO DINH: doi khoa la doi toan bo ma benh nhan cua cac lan xuat."
        )
    return khoa.strip().encode("utf-8")


def ma_an_danh(loai: str, gia_tri: int, khoa: bytes) -> str:
    """Mã ẩn danh ổn định cho một đối tượng. 'loai' tách không gian mã của bé và của ảnh."""
    return hmac.new(khoa, f"{loai}:{gia_tri}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def truy_van_ca_duoc_phep(db) -> list:
    """Danh sách ca đạt ĐỦ bốn điều kiện ở đầu file, kèm thông tin cần để xuất.

    Trả về bản ghi nội bộ (có record_id, child_id, image_path thật) — việc ẩn danh hoá là của
    bước xuất, để hàm này còn dùng được cho nhật ký kiểm toán.
    """
    from setup_database import Child, DoctorReview, StoolRecord

    q = (
        db.query(StoolRecord, DoctorReview)
        .join(DoctorReview, DoctorReview.record_id == StoolRecord.id)
        .join(Child, Child.id == StoolRecord.child_id)
        .filter(
            DoctorReview.is_final.is_(True),
            DoctorReview.decision == "approved",
            DoctorReview.confirmed_bitss.in_(sorted(bitss.VALID_CLASSES)),
            DoctorReview.eligible_for_training.is_(True),
            StoolRecord.nghien_cuu_dong_y_luc.isnot(None),
            StoolRecord.nghien_cuu_rut_luc.is_(None),
            StoolRecord.nghien_cuu_phien_ban == dong_y_nghien_cuu.PHIEN_BAN_THONG_BAO,
            Child.deleted_at.is_(None),
        )
        .order_by(StoolRecord.id.asc())
    )

    ket = []
    for record, review in q.all():
        # Kiểm lại bằng chính hàm quyết định của module đồng thuận. Câu lọc SQL ở trên chỉ
        # để khỏi nạp cả bảng; nếu một ngày ai đó sửa định nghĩa "còn hiệu lực" ở bên kia mà
        # quên sửa ở đây, dòng này chặn lại và ghi log thay vì để dữ liệu đi ra.
        if not dong_y_nghien_cuu.ca_con_hieu_luc(record):
            logger.error("ca #%s lot qua cau loc SQL nhung khong con dong thuan - bo qua", record.id)
            continue
        ket.append({
            "record_id": record.id,
            "child_id": record.child_id,
            "image_path": record.image_path,
            "nhan_bitss": review.confirmed_bitss,
            "thang_tuoi": record.age_months_at_observation,
            "che_do_an": record.feeding_type_at_observation,
        })
    return ket
