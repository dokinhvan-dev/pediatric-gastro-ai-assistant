"""Đồng thuận chia sẻ dữ liệu với nhà cung cấp AI, và nội dung thông báo mà người dùng đồng ý.

Vì sao module này tồn tại:
    Mỗi lượt chat gửi dữ liệu của bé ra khỏi hệ thống, sang máy chủ của nhà cung cấp model.
    Trước module này, phụ huynh dùng ô chat mà không hề được báo điều đó, và không có cách
    nào nói "không". Mục 24 trong verify_task.py đã kiểm kê chính xác cái gì đi ra; module
    này là nửa còn lại: người có dữ liệu được biết và được chọn.

AI LÀ NGƯỜI ĐỒNG Ý — điểm dễ làm sai nhất:
    Đồng thuận gắn với CHỦ HỒ SƠ BÉ, không gắn với người đang hỏi. Khi bác sĩ hỏi trợ lý
    về một ca, dữ liệu gửi đi là của đứa trẻ đó, nên câu cần hỏi là "gia đình bé đã đồng
    ý chưa", không phải "bác sĩ đã đồng ý chưa". Gắn nhầm vào người hỏi thì một bác sĩ
    bấm đồng ý một lần là mở đường gửi hồ sơ của mọi gia đình, kể cả gia đình đã từ chối.

Đồng ý với CÁI GÌ — vì sao có phiên bản:
    Người dùng đồng ý với một nội dung thông báo cụ thể. Nếu sau này hệ thống gửi thêm một
    loại dữ liệu mới, sự đồng ý cũ không phủ được thứ họ chưa từng được báo. Nên mỗi lần
    đồng ý ghi lại phiên bản thông báo, và đồng ý chỉ còn hiệu lực khi phiên bản đó trùng
    với phiên bản hiện hành. Đổi thông báo thì phải tăng PHIEN_BAN_THONG_BAO, và mọi người
    tự động quay về trạng thái chưa đồng ý cho tới khi xem lại.

Chưa đồng ý thì sao:
    Ô chat vẫn dùng được ở phạm vi kiến thức chung, y như người chưa đăng nhập: không công
    cụ nào đọc database, nên không có hồ sơ nào đi ra. Nhưng thứ người dùng tự gõ thì vẫn
    luôn được gửi đi — đó là bản chất của việc trò chuyện với một model ở nơi khác, và
    thông báo phải nói thẳng điều đó thay vì để người dùng tưởng "chưa đồng ý = không gửi gì".
"""
from datetime import datetime, timezone

# Tăng giá trị này MỖI KHI nội dung dưới đây đổi theo hướng gửi thêm dữ liệu. Đồng ý cũ sẽ
# tự hết hiệu lực. Bớt dữ liệu thì không bắt buộc tăng, vì sự đồng ý cũ vẫn phủ được.
PHIEN_BAN_THONG_BAO = "2026-09-23"

NHA_CUNG_CAP = "Google (Gemini API)"

# Danh sách mã và mô tả của dữ liệu đi ra / không đi ra khi CÓ đồng thuận.
# Mục 24 trong verify_task.py so tập mã ở đây với payload thật bắt được: thông báo nói một
# đằng mà hệ thống gửi một nẻo thì bài test báo trượt. Một thông báo đồng thuận không được
# phép lạc hậu so với hành vi thật — lúc đó nó không còn là thông báo mà là lời nói dối.
DU_LIEU_GUI_DI = {
    "tin_nhan_va_lich_su": "Những gì bạn gõ trong ô chat và nội dung các lượt trò chuyện gần đây",
    "ten_be": "Tên bé",
    "thong_tin_co_ban": "Tháng tuổi, cân nặng, chế độ ăn của bé",
    "yeu_to_lam_sang": "Các yếu tố lâm sàng bạn đã chọn trong hồ sơ bé (ví dụ: dị ứng đạm sữa bò)",
    "ket_qua_phan_tich": "Kết quả phân loại BITSS, độ tin cậy, trạng thái duyệt của các ca",
    "loi_dan_bac_si": "Lời dặn của bác sĩ trên các ca đã được duyệt",
}

DU_LIEU_KHONG_GUI = {
    "anh": "Ảnh bạn tải lên và đường dẫn lưu ảnh",
    "email_mat_khau": "Email và mật khẩu",
    "ghi_chu_tu_do": "Phần ghi chú tự do trong hồ sơ bé (chỉ bác sĩ đọc)",
    "ten_bac_si": "Tên bác sĩ đã duyệt ca",
    "ho_ten_nguoi_hoi": "Họ tên của bạn",
}


def thong_bao() -> dict:
    """Nội dung thông báo để client hiển thị trước khi người dùng quyết định."""
    return {
        "phien_ban": PHIEN_BAN_THONG_BAO,
        "nha_cung_cap": NHA_CUNG_CAP,
        "tom_tat": (
            "Trợ lý AI chạy trên máy chủ của một nhà cung cấp bên ngoài. Khi bạn đồng ý, hệ "
            "thống sẽ gửi kèm hồ sơ của bé để trợ lý trả lời sát với tình trạng của bé. "
            "Khi chưa đồng ý, trợ lý chỉ trả lời kiến thức chung — nhưng những gì bạn tự gõ "
            "vẫn luôn được gửi đi, vì đó là cách ô chat hoạt động."
        ),
        "gui_di_khi_dong_y": [{"ma": m, "mo_ta": t} for m, t in DU_LIEU_GUI_DI.items()],
        "khong_bao_gio_gui": [{"ma": m, "mo_ta": t} for m, t in DU_LIEU_KHONG_GUI.items()],
        "rut_lai": (
            "Bạn có thể rút lại đồng ý bất cứ lúc nào; có hiệu lực ngay từ lượt chat kế tiếp. "
            "Rút lại đồng ý sẽ xoá toàn bộ lịch sử trò chuyện của bạn."
        ),
        "luu_tru_lich_su": _luu_tru_lich_su(),
    }


def _luu_tru_lich_su() -> dict:
    """Thời hạn lưu lịch sử, đọc từ đúng hằng số mà việc dọn dùng — không chép số ra đây.

    Chép "90 ngày" thành chữ trong thông báo thì ngày ai đó đổi CHAT_LUU_TRU_NGAY, thông báo
    sẽ nói một đằng còn hệ thống làm một nẻo, và thông báo là thứ người dùng dựa vào để đồng ý.
    """
    from app.services.chat_history import SO_NGAY_LUU_TRU
    return {
        "so_ngay": SO_NGAY_LUU_TRU,
        "mo_ta": (
            f"Lịch sử trò chuyện được lưu tối đa {SO_NGAY_LUU_TRU} ngày rồi tự động xoá. "
            "Bạn có thể tự xoá sớm hơn bất cứ lúc nào."
        ),
    }


def con_hieu_luc(user) -> bool:
    """Người dùng này đang có đồng thuận hợp lệ với thông báo HIỆN HÀNH không.

    Nhận đối tượng User của ORM (hoặc bất cứ thứ gì có hai thuộc tính đó). None thì False:
    không tìm thấy người dùng thì không có đồng thuận nào để nói tới.
    """
    if user is None:
        return False
    return (
        getattr(user, "dong_y_ai_luc", None) is not None
        and getattr(user, "dong_y_ai_phien_ban", None) == PHIEN_BAN_THONG_BAO
    )


def nguoi_dung_da_dong_y(db, user_id) -> bool:
    """Tra database ở MỖI lần hỏi, không tin một ảnh chụp trạng thái nào.

    Cùng lý do deps.get_current_user tra lại vai trò ở mỗi request: rút lại đồng ý phải có
    hiệu lực ngay, kể cả với một lượt chat đang chạy dở. Mỗi lời gọi công cụ đi qua đây
    lại, nên rút lại giữa chừng thì lời gọi kế tiếp đã bị chặn.
    """
    from setup_database import User

    if user_id is None:
        return False
    return con_hieu_luc(db.query(User).filter(User.id == user_id).first())


def chu_ho_so_da_dong_y(db, child) -> bool:
    """Gia đình sở hữu hồ sơ bé này đã đồng ý chia sẻ với nhà cung cấp AI chưa."""
    if child is None:
        return False
    return nguoi_dung_da_dong_y(db, child.owner_user_id)


def ghi_dong_y(user) -> None:
    """Ghi đồng thuận với phiên bản hiện hành. Người gọi tự commit."""
    user.dong_y_ai_luc = datetime.now(timezone.utc)
    user.dong_y_ai_phien_ban = PHIEN_BAN_THONG_BAO


def rut_dong_y(user) -> None:
    """Xoá đồng thuận. Người gọi tự commit.

    Xoá hẳn chứ không giữ lại dấu vết "đã từng đồng ý": trạng thái cần trả lời là hiện tại
    có được gửi hay không, và một cờ thừa là thêm một chỗ để hai nơi đọc hiểu khác nhau.
    """
    user.dong_y_ai_luc = None
    user.dong_y_ai_phien_ban = None
