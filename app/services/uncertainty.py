"""Cơ chế uncertainty gating: đánh dấu ca mô hình không chắc chắn để bác sĩ ưu tiên xem.

Lưu ý về phạm vi:
    Hệ thống này bắt MỌI ca đều phải qua bác sĩ duyệt, nên gating KHÔNG dùng để bỏ
    qua bước duyệt. Nó dùng để xếp ưu tiên trong hàng chờ và để theo dõi sức khoẻ
    của mô hình theo thời gian.

Vì sao module này tách riêng khỏi endpoint:
    Ngưỡng tin cậy là một quyết định LÂM SÀNG, không phải chi tiết kỹ thuật của một
    hàm API. Nó cần được nhìn thấy, tranh luận và chỉnh sửa bởi người phụ trách
    chuyên môn. Một con số 0.7 nằm lẫn giữa thân hàm xử lý HTTP là con số không ai
    rà lại, và không ai biết nó dựa trên cơ sở nào.

Vì sao chỉ dùng MỘT ngưỡng đơn, không dùng entropy hay margin:
    Mô hình trong dự án này chỉ trả về một nhãn kèm một giá trị confidence, KHÔNG
    trả về vector xác suất đầy đủ trên cả 4 lớp. Entropy và margin (khoảng cách
    giữa top-1 và top-2) đều cần phân phối đầy đủ mới tính được, nên hiện không
    khả dụng. Nếu sau này mô hình trả ra vector xác suất, đây là chỗ DUY NHẤT cần
    sửa — endpoint và database không phải đụng tới.

Cách hiệu chỉnh ngưỡng khi đã có validation set thật (đừng chọn bằng cảm tính):
    Với mỗi ngưỡng ứng viên, tính đồng thời hai đại lượng đối nghịch nhau:
      - Tỉ lệ ca bị gắn cờ  -> khối lượng việc đổ lên bác sĩ.
      - Tỉ lệ ca SAI nhưng confidence vẫn cao nên lọt qua  -> rủi ro lâm sàng thật.
    Ngưỡng hợp lý là điểm mà tỉ lệ lọt-sai đủ thấp để chấp nhận được, với khối lượng
    duyệt mà đội ngũ thực sự kham nổi. Hai điều này luôn đánh đổi lẫn nhau, không có
    ngưỡng nào tối ưu cả hai.

Lưu ý riêng cho thang BITSS (xem app/services/bitss.py):
    Nghiên cứu độ đồng thuận BITSS cho thấy ranh giới phân cứng / phân khuôn là chỗ
    ngay cả người thật cũng hay bất đồng nhất. Nếu về sau có số liệu confidence theo
    từng lớp, rất có thể nhóm 1-2 cần ngưỡng cao hơn nhóm 3-4, thay vì dùng chung
    một ngưỡng phẳng như hiện tại.
"""

from typing import Optional

# Ngưỡng tạm thời cho giai đoạn prototype. CHƯA được hiệu chỉnh trên dữ liệu thật,
# phải chỉnh lại khi có validation set của mô hình.
CONFIDENCE_THRESHOLD = 0.70

# Chính sách phân luồng sau khi gắn cờ.
#
# MỌI ca đều phải qua mắt bác sĩ ('pending'), kể cả ca mô hình rất tự tin. Đây là
# quyết định chuyên môn, không phải giới hạn kỹ thuật: mô hình tự tin không đồng
# nghĩa với mô hình đúng, và một ca nhi khoa bị phân loại sai mà không ai kiểm tra
# là cái giá không chấp nhận được.
#
# Vậy uncertainty gating còn tác dụng gì khi cả hai nhánh đều ra 'pending'?
# Nó không còn dùng để BỎ QUA bước duyệt, mà để XẾP ƯU TIÊN: cờ is_uncertain đánh
# dấu những ca bác sĩ nên xem trước, và là tín hiệu để theo dõi chất lượng mô hình
# theo thời gian (tỉ lệ ca bị gắn cờ tăng lên là dấu hiệu mô hình đang xuống cấp
# trên dữ liệu thực tế).
REVIEW_STATUS_WHEN_UNCERTAIN = "pending"
REVIEW_STATUS_WHEN_CONFIDENT = "pending"


def should_flag_uncertain(confidence: Optional[float]) -> bool:
    """Ca này có phải đưa vào danh sách chờ bác sĩ duyệt không?

    confidence là None cũng bị gắn cờ: không có số liệu để đánh giá thì mặc định coi
    là không chắc chắn. Im lặng cho qua một ca không rõ độ tin cậy là kiểu hỏng nguy
    hiểm nhất, vì nó trông y hệt một ca bình thường.
    """
    if confidence is None:
        return True
    return confidence < CONFIDENCE_THRESHOLD


def review_status_for(is_uncertain: bool) -> str:
    """Trạng thái duyệt tương ứng với kết quả gating."""
    return REVIEW_STATUS_WHEN_UNCERTAIN if is_uncertain else REVIEW_STATUS_WHEN_CONFIDENT


def evaluate(confidence: Optional[float]) -> dict:
    """Chạy toàn bộ gating cho một ca, trả về quyết định kèm lý do đọc được.

    Trả kèm 'ly_do' để mọi quyết định gating đều tự giải thích được khi đọc log hoặc
    khi phụ huynh/bác sĩ hỏi vì sao ca này bị đánh dấu.
    """
    flagged = should_flag_uncertain(confidence)

    if confidence is None:
        ly_do = "Không có giá trị độ tin cậy từ mô hình nên mặc định coi là không chắc chắn."
    elif flagged:
        ly_do = (
            f"Độ tin cậy {round(confidence * 100, 1)}% thấp hơn ngưỡng "
            f"{round(CONFIDENCE_THRESHOLD * 100, 1)}% nên ca này cần bác sĩ duyệt."
        )
    else:
        ly_do = (
            f"Độ tin cậy {round(confidence * 100, 1)}% đạt ngưỡng "
            f"{round(CONFIDENCE_THRESHOLD * 100, 1)}%."
        )

    return {
        "is_uncertain": flagged,
        "review_status": review_status_for(flagged),
        "nguong_ap_dung": CONFIDENCE_THRESHOLD,
        "ly_do": ly_do,
    }
