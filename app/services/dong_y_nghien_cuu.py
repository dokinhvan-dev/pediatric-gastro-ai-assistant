"""Đồng thuận cho phép dùng ảnh của bé vào nghiên cứu và huấn luyện mô hình.

Vì sao tách khỏi app/services/dong_y.py:
    dong_y.py trả lời "có được gửi hồ sơ bé sang nhà cung cấp AI trong lúc chat không".
    Module này trả lời một câu khác hẳn: "có được đưa ảnh này vào tập dữ liệu huấn luyện
    không". Hai việc có phạm vi, thời hạn và hệ quả khác nhau — một phụ huynh hoàn toàn có
    thể muốn trợ lý đọc hồ sơ con mình mà không muốn ảnh của con nằm trong một tập dữ liệu
    nghiên cứu, hoặc ngược lại. Gộp hai thứ vào một cờ là quyết định thay họ.

    Trước module này, bác sĩ duyệt 'approved' là eligible_for_training tự bật thành True,
    trong khi gia đình chưa bao giờ được hỏi. Hợp đồng dữ liệu của phần mô hình
    (docs/data_contract.md bên repo đồng đội) yêu cầu đồng thuận "cho từng ảnh".

Vì sao lưu trên TỪNG CA chứ không phải một cờ chung ở tài khoản:
    Đúng yêu cầu "cho từng ảnh" ở trên, và để phụ huynh rút lại được cho riêng một tấm ảnh
    (ví dụ ảnh chụp lộ một phần cơ thể bé) mà không phải rút tất cả.

    Tài khoản chỉ lưu một LỰA CHỌN MẶC ĐỊNH để điền sẵn cho các ảnh upload sau — hỏi lại ở
    mỗi lần upload thì phụ huynh sẽ bấm cho qua, và một sự đồng ý bấm cho qua không còn là
    đồng ý. Bản thân lựa chọn mặc định cũng gắn với phiên bản thông báo: thông báo đổi thì
    ảnh upload sau đó KHÔNG được tự tính là đã đồng ý, vì phụ huynh chưa đọc bản mới.

Rút lại có tác dụng tới đâu — phải nói thật trong thông báo:
    Rút lại loại ca đó khỏi MỌI lần xuất dữ liệu kể từ lúc rút. Nhưng một mô hình đã huấn
    luyện xong thì không "quên" được một tấm ảnh nó từng học. Để phụ huynh tưởng rút lại là
    xoá được khỏi mô hình là hứa một điều hệ thống không làm được.

Ba cột trên stool_records:
    nghien_cuu_dong_y_luc     thời điểm đồng ý gần nhất
    nghien_cuu_phien_ban      phiên bản thông báo đã được đồng ý
    nghien_cuu_rut_luc        thời điểm rút lại (None nếu đang đồng ý)
    Rút lại KHÔNG xoá thời điểm đồng ý: cần trả lời được câu "ảnh này đã từng được phép
    dùng từ lúc nào tới lúc nào" nếu có ai hỏi nó có nằm trong một lần xuất cũ hay không.
"""
from datetime import datetime, timezone

# Tăng giá trị này MỖI KHI phạm vi sử dụng dưới đây mở rộng. Đồng ý cũ — cả trên từng ca
# lẫn lựa chọn mặc định của tài khoản — tự hết hiệu lực.
PHIEN_BAN_THONG_BAO = "2026-09-23"


def thong_bao() -> dict:
    return {
        "phien_ban": PHIEN_BAN_THONG_BAO,
        "muc_dich": (
            "Dùng ảnh và nhãn đã được bác sĩ xác nhận để huấn luyện và đánh giá mô hình "
            "phân loại hình thái phân theo thang BITSS, trong phạm vi nghiên cứu của dự án."
        ),
        "du_lieu_duoc_dung": [
            "Ảnh bạn tải lên (đã xoá toàn bộ thông tin vị trí và thiết bị)",
            "Nhãn BITSS do bác sĩ xác nhận",
            "Tháng tuổi và chế độ ăn của bé tại thời điểm chụp",
            "Một mã ẩn danh cho mỗi bé, KHÔNG phải tên hay mã hồ sơ",
        ],
        "khong_bao_gio_dung": [
            "Tên bé, tên phụ huynh, email",
            "Ghi chú tự do trong hồ sơ bé",
            "Nội dung trò chuyện với trợ lý",
        ],
        "chi_ca_dat_dieu_kien": (
            "Chỉ những ca đã được bác sĩ duyệt và xác nhận nhãn mới có thể được dùng."
        ),
        "rut_lai": (
            "Bạn có thể rút lại cho từng ảnh bất cứ lúc nào. Ảnh sẽ không được đưa vào bất kỳ "
            "lần xuất dữ liệu nào sau thời điểm rút. Lưu ý: nếu ảnh đã được dùng để huấn luyện "
            "một mô hình trước đó, mô hình đó không thể xoá những gì đã học từ ảnh."
        ),
    }


def _bay_gio():
    return datetime.now(timezone.utc)


def ca_con_hieu_luc(record) -> bool:
    """Ca này đang có đồng thuận nghiên cứu hợp lệ với thông báo HIỆN HÀNH không."""
    if record is None:
        return False
    return (
        getattr(record, "nghien_cuu_dong_y_luc", None) is not None
        and getattr(record, "nghien_cuu_rut_luc", None) is None
        and getattr(record, "nghien_cuu_phien_ban", None) == PHIEN_BAN_THONG_BAO
    )


def mac_dinh_con_hieu_luc(user) -> bool:
    """Lựa chọn mặc định của tài khoản có còn áp được cho ảnh upload mới không."""
    return getattr(user, "nghien_cuu_mac_dinh_phien_ban", None) == PHIEN_BAN_THONG_BAO


def ghi_cho_ca(record) -> None:
    """Ghi đồng thuận cho một ca với phiên bản hiện hành. Người gọi tự commit."""
    record.nghien_cuu_dong_y_luc = _bay_gio()
    record.nghien_cuu_phien_ban = PHIEN_BAN_THONG_BAO
    record.nghien_cuu_rut_luc = None


def rut_cho_ca(record) -> bool:
    """Rút đồng thuận của một ca. Trả False nếu ca vốn không có đồng thuận nào để rút.

    Giữ lại nghien_cuu_dong_y_luc: xem giải thích "Ba cột" ở đầu file.
    """
    if record.nghien_cuu_dong_y_luc is None or record.nghien_cuu_rut_luc is not None:
        return False
    record.nghien_cuu_rut_luc = _bay_gio()
    return True
