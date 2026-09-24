"""Nguồn chân lý duy nhất về thang điểm BITSS trong hệ thống.

Vì sao module này tồn tại:
    Cột `ai_predicted_class` trong database chỉ lưu số nguyên 1-4. Nếu không có nơi
    nào định nghĩa 1 nghĩa là gì, mà system_instruction lại yêu cầu Agent "giải thích
    ý nghĩa của loại phân BITSS", thì Agent buộc phải lấp chỗ trống bằng kiến thức
    tham số của nó — tức là bịa ra một cách có hệ thống, im lặng và rất thuyết phục.
    Mọi nhãn, mô tả và cảnh báo mà Agent được phép nói ra phải xuất phát từ đây.

Cơ sở khoa học:
    Huysentruyt K, et al. The Brussels Infant and Toddler Stool Scale: A Study on
    Interobserver Reliability. J Pediatr Gastroenterol Nutr. 2019;68(2):207-213.
    Lewis SJ, Heaton KW. Stool form scale as a useful guide to intestinal transit
    time. Scand J Gastroenterol. 1997;32(9):920-924.

Cấu trúc thang điểm:
    BITSS gồm 7 bức ảnh tã, mỗi ảnh ứng với một type của thang Bristol (BSFS 1-7).
    Bảy ảnh này được gộp thành 4 nhóm hình thái dùng làm nhãn phân loại của hệ thống.

    LƯU Ý QUAN TRỌNG: BITSS xếp BSFS type 3 vào nhóm PHÂN CỨNG, khác với thang
    Bristol dành cho người lớn (vốn coi type 3 là phân bình thường). Nhầm điểm này
    khi gán nhãn sẽ làm hỏng toàn bộ tập huấn luyện mà không có cách nào phát hiện
    được về sau.

Tính chất thứ bậc (ordinal):
    4 lớp nằm trên một trục liên tục cứng -> khô -> lỏng -> nước. Nhầm lớp 1 thành
    lớp 2 nhẹ hơn nhiều so với nhầm lớp 1 thành lớp 4. Vì vậy khi đánh giá model
    phải dùng linearly weighted kappa (Cohen 1960) chứ không phải kappa thường,
    và cơ chế uncertainty gating nên tính đến khoảng cách thứ bậc.
"""

from typing import Optional

# Đối chiếu sang thang Bristol gốc, giữ lại để truy vết và đối chiếu tài liệu.
BSFS_REFERENCE = {
    1: "Các cục cứng rời rạc như hạt, khó đẩy ra",
    2: "Khối dài ghép từ nhiều cục cứng",
    3: "Khối dài, bề mặt có vết nứt",
    4: "Khối dài hoặc cuộn, bề mặt nhẵn và mềm",
    5: "Các mảnh mềm rời có bờ rõ",
    6: "Mảnh nhão xốp, bờ lởm chởm, không còn hình khối",
    7: "Hoàn toàn là dịch, không có phần rắn",
}

BITSS_CLASSES = {
    1: {
        "ten": "Phân cứng",
        "ten_en": "Hard stools",
        "bsfs_tuong_duong": [1, 2, 3],
        "mo_ta_hinh_thai": (
            "Khối rắn, bề mặt khô, đường viền sắc nét. Có thể ở dạng các cục rời như "
            "hạt đậu, khối dài ghép từ nhiều cục, hoặc khối dài có vết nứt trên bề mặt. "
            "Giữ nguyên hình dạng, không loang thấm ra vải tã."
        ),
        "dau_hieu_nhan_biet": [
            "Bề mặt khô, không bóng",
            "Đường viền rõ và sắc",
            "Giữ nguyên khối khi nghiêng tã",
            "Không có quầng thấm quanh khối phân",
        ],
        "y_nghia_lam_sang": (
            "Nhóm hình thái liên quan tới táo bón chức năng ở trẻ nhỏ. Đây là nhóm có "
            "độ đồng thuận giữa những người đánh giá thấp nhất trong nghiên cứu BITSS, "
            "nên kết quả thuộc nhóm này cần được xem xét thận trọng hơn."
        ),
        "luu_y_theo_boi_canh": [
            "Trẻ bú mẹ hoàn toàn rất hiếm khi đi phân cứng; nếu xuất hiện thì nên được bác sĩ xem xét.",
            "Thường gặp hơn ở giai đoạn chuyển sang sữa công thức hoặc mới bắt đầu ăn dặm.",
        ],
    },
    2: {
        "ten": "Phân khuôn",
        "ten_en": "Formed stools",
        "bsfs_tuong_duong": [4],
        "mo_ta_hinh_thai": (
            "Thành khuôn mềm, liền khối, bề mặt nhẵn và ẩm. Giữ được dáng nhưng không "
            "cứng, có thể hơi dẹt xuống do trọng lượng của chính nó."
        ),
        "dau_hieu_nhan_biet": [
            "Bề mặt nhẵn và ẩm",
            "Còn giữ được dáng khối",
            "Quầng thấm quanh phân rất hẹp hoặc không có",
        ],
        "y_nghia_lam_sang": (
            "Hình thái phân thành khuôn điển hình. Đây là một trong hai nhóm có độ "
            "đồng thuận cao nhất giữa những người đánh giá trong nghiên cứu BITSS."
        ),
        "luu_y_theo_boi_canh": [
            "Phổ biến ở trẻ đã ăn dặm ổn định.",
            "Ở trẻ bú mẹ hoàn toàn dưới 6 tháng, phân khuôn ít gặp hơn phân lỏng.",
        ],
    },
    3: {
        "ten": "Phân lỏng",
        "ten_en": "Loose stools",
        "bsfs_tuong_duong": [5, 6],
        "mo_ta_hinh_thai": (
            "Mất khuôn một phần hoặc hoàn toàn. Dạng mảnh mềm rời có bờ nhoè, hoặc "
            "dạng nhão xốp không còn hình khối. Bắt đầu loang thấm ra vải tã."
        ),
        "dau_hieu_nhan_biet": [
            "Bờ không rõ, nhoè",
            "Không giữ được dáng khối",
            "Có quầng thấm quanh phân",
            "Bề mặt bóng ướt",
        ],
        "y_nghia_lam_sang": (
            "Bắt buộc phải diễn giải theo tuổi và chế độ ăn. Cùng một hình thái phân "
            "lỏng có thể hoàn toàn sinh lý ở trẻ này nhưng lại là bất thường ở trẻ khác."
        ),
        "luu_y_theo_boi_canh": [
            "Trẻ bú mẹ hoàn toàn: phân lỏng, màu vàng hoa cà hoa cải là bình thường, KHÔNG đồng nghĩa tiêu chảy.",
            "Trẻ đã ăn dặm: phân lỏng mới xuất hiện kèm tăng số lần đi ngoài cần chú ý khả năng tiêu chảy.",
            "Điều quan trọng nhất là SỰ THAY ĐỔI so với thói quen thường ngày của bé, không chỉ là hình thái tại một thời điểm.",
        ],
    },
    4: {
        "ten": "Phân toé nước",
        "ten_en": "Watery stools",
        "bsfs_tuong_duong": [7],
        "mo_ta_hinh_thai": (
            "Hoàn toàn ở dạng dịch, không còn phần rắn nào giữ được hình. Loang rộng "
            "và thấm gần như khắp mặt tã, thường để lại vòng thấm lớn."
        ),
        "dau_hieu_nhan_biet": [
            "Không còn khối rắn nào",
            "Loang rộng khắp tã",
            "Vòng thấm lớn trên vải tã",
            "Đôi khi chỉ thấy vệt màu thấm chứ không thấy khối phân",
        ],
        "y_nghia_lam_sang": (
            "Nhóm cần chú ý nguy cơ mất nước, đặc biệt khi lặp lại nhiều lần trong ngày. "
            "Đây là một trong hai nhóm có độ đồng thuận cao nhất giữa những người đánh giá."
        ),
        "luu_y_theo_boi_canh": [
            "Nguy cơ mất nước tăng nhanh khi trẻ càng nhỏ tuổi.",
            "Cần theo dõi số lần đi ngoài trong 24 giờ cùng các dấu hiệu mất nước.",
        ],
    },
}

# Khớp chính xác với CHECK constraint: ai_predicted_class IN (1, 2, 3, 4)
VALID_CLASSES = frozenset(BITSS_CLASSES.keys())

# Dấu hiệu cảnh báo đỏ. Đây là nội dung TĨNH, đã được rà soát — Agent không được
# tự nghĩ ra danh sách này mà phải đọc từ đây.
RED_FLAGS = [
    "Phân có máu tươi, hoặc có nhầy lẫn máu",
    "Phân đen như hắc ín",
    "Phân bạc màu, trắng như đất sét (nhất là khi kèm vàng da hoặc nước tiểu sẫm màu)",
    "Nôn ra dịch xanh hoặc vàng (dịch mật), hoặc nôn liên tục không dứt",
    "Bụng chướng căng, đau bụng dữ dội, quấy khóc từng cơn bất thường",
    "Dấu hiệu mất nước: môi khô, khóc không ra nước mắt, mắt trũng, thóp trũng, tiểu ít hoặc không tiểu trên 6 giờ",
    "Li bì, khó đánh thức, hoặc co giật",
    "Sốt cao ở trẻ dưới 3 tháng tuổi",
    "Sụt cân, hoặc chậm tăng cân kéo dài",
    "Tiêu chảy kéo dài trên 14 ngày",
]


def is_valid_class(class_id) -> bool:
    return class_id in VALID_CLASSES


def label(class_id: Optional[int]) -> Optional[str]:
    """Trả tên nhóm BITSS. None vào thì None ra — không bịa nhãn cho ca chưa có kết quả."""
    if class_id is None or class_id not in BITSS_CLASSES:
        return None
    return BITSS_CLASSES[class_id]["ten"]


def describe(class_id: Optional[int]) -> Optional[dict]:
    """Trả toàn bộ mô tả của một nhóm, kèm số hiệu lớp."""
    if class_id is None or class_id not in BITSS_CLASSES:
        return None
    return {"bitss_class": class_id, **BITSS_CLASSES[class_id]}


def ordinal_distance(class_a: Optional[int], class_b: Optional[int]) -> Optional[int]:
    """Khoảng cách trên trục thứ bậc cứng -> nước.

    Dùng cho weighted kappa và cho uncertainty gating ở P2.2: model phân vân giữa
    lớp 1 và 2 (khoảng cách 1) ít đáng lo hơn nhiều so với phân vân giữa lớp 1 và 4
    (khoảng cách 3, nghĩa là model đang thật sự hỏng).
    """
    if class_a is None or class_b is None:
        return None
    if class_a not in VALID_CLASSES or class_b not in VALID_CLASSES:
        return None
    return abs(class_a - class_b)


def full_scale() -> dict:
    """Toàn bộ thang điểm, dùng cho tool tra cứu của Agent."""
    return {
        "nguon": (
            "Brussels Infant and Toddler Stool Scale (Huysentruyt et al., "
            "J Pediatr Gastroenterol Nutr 2019;68(2):207-213)"
        ),
        "cau_truc": (
            "BITSS gồm 7 ảnh tã tương ứng 7 type của thang Bristol, được gộp thành "
            "4 nhóm hình thái dùng làm nhãn phân loại."
        ),
        "tinh_chat": (
            "4 nhóm nằm trên một trục thứ bậc liên tục từ cứng đến toé nước, "
            "không phải 4 nhãn rời rạc độc lập."
        ),
        "luu_y_quan_trong": (
            "BITSS xếp BSFS type 3 vào nhóm phân cứng, khác với thang Bristol người lớn "
            "vốn coi type 3 là phân bình thường."
        ),
        "cac_nhom": [describe(c) for c in sorted(BITSS_CLASSES)],
        "doi_chieu_bristol": BSFS_REFERENCE,
    }
