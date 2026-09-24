"""Hạn mức gọi cho các endpoint đắt, giữ trong bộ nhớ tiến trình.

Vì sao chỉ bốn nhóm chứ không phải mọi endpoint:
    Đặt hạn mức ở khắp nơi là nghi thức bảo mật, không nhắm vào mối đe doạ nào có thật:
    các endpoint GET đều đã xác thực, phân trang chặn ở 100, truy vấn có index. Bốn nhóm
    trong HAN_MUC bên dưới mỗi nhóm có một lý do đo được — hoặc tốn tiền thật mỗi lần
    gọi, hoặc đốt 200ms CPU mà không cần đăng nhập, hoặc ghi xuống đĩa không hạn ngạch.

    Các endpoint inference-* CỐ Ý không nằm trong danh sách: đó là worker của chính mình,
    đặt hạn mức lên nó là tự bóp nghẹt hàng đợi xử lý — có hại chứ không phải trung tính.

Vì sao tự viết thay vì thêm một thư viện:
    Thứ cần ở đây là một cửa sổ trượt đếm theo khoá. Thêm một phụ thuộc mới vào một dự
    án y tế để có ngần ấy logic là đổi một bề mặt tấn công lấy ba chục dòng code.

Giới hạn, ghi rõ để không ai tưởng đây là hạn mức cấp hệ thống:
    1. Trạng thái nằm TRONG TIẾN TRÌNH. Chạy nhiều worker thì mỗi worker có sổ riêng,
       nên hạn mức thực tế nhân lên theo số worker. Khởi động lại là mất sổ. Muốn đúng
       ở nhiều tiến trình thì phải chuyển sang kho dùng chung (Redis), và khi đó chỉ
       cần thay thân hai hàm dưới đây.
    2. Người chưa đăng nhập được nhận diện bằng địa chỉ IP. IP dùng chung (trường học,
       văn phòng, NAT nhà mạng) sẽ chia nhau một hạn mức; đổi lại, đây là thứ duy nhất
       có được khi chưa có danh tính.
    3. CỐ Ý không đọc X-Forwarded-For. Header đó do client gửi nên giả mạo được: tin nó
       nghĩa là ai cũng vượt hạn mức bằng cách bịa một IP mới mỗi request — tức là lớp
       bảo vệ này biến mất đúng lúc cần nhất.

       Việc tin header đó thuộc về uvicorn, qua FORWARDED_ALLOW_IPS — nó chỉ tin khi request
       đến từ đúng IP của proxy. Cấu hình sai thì mọi người dùng mang chung IP của proxy và
       cả trang chỉ còn một hạn mức. Cách cấu hình (và cái bẫy đặt biến này trong .env) ghi
       ở .env.example; deps.canh_bao_neu_proxy_chua_duoc_tin ghi log khi thấy dấu hiệu.
"""
import os
import threading
import time
from collections import OrderedDict

def _doc(ten_nhom: str, khach: int, nguoi_dung: int, cua_so: int) -> tuple:
    hoa = ten_nhom.upper()
    return (
        int(os.getenv(f"RATE_{hoa}_ANON", str(khach))),
        int(os.getenv(f"RATE_{hoa}_USER", str(nguoi_dung))),
        int(os.getenv(f"RATE_{hoa}_WINDOW", str(cua_so))),
    )


# Hạn mức theo NHÓM endpoint: (người lạ, đã đăng nhập, cửa sổ giây).
#
# Chỉ có bốn nhóm, và việc danh sách này ngắn là có chủ ý. Đặt hạn mức lên mọi endpoint
# là nghi thức bảo mật chứ không nhắm vào mối đe doạ nào có thật: các endpoint GET đều đã
# xác thực, phân trang chặn ở 100 và truy vấn có index. Bốn nhóm dưới đây mỗi nhóm có một
# lý do cụ thể, đo được.
#
#   chat      — endpoint DUY NHẤT vừa không cần đăng nhập vừa tốn tiền thật mỗi lần gọi.
#   dang_ky   — đo thật: 208ms bcrypt mỗi lần gọi, và không cần đăng nhập. Vấn đề không
#               nằm ở số request/giây mà ở CHIẾM DỤNG LUỒNG: FastAPI chạy endpoint đồng
#               bộ trong threadpool 40 luồng, nên 40 kết nối lặp là threadpool đầy vĩnh
#               viễn, và bác sĩ đang duyệt ca xếp hàng phía sau. Cộng thêm: tạo tài khoản
#               không giới hạn, mà mỗi tài khoản lại mở khoá cho upload.
#   dang_nhap — dò mật khẩu không giới hạn, không khoá tài khoản, không đếm lần sai.
#               Sau khi vá lỗi hash giả (xem security.DUMMY_PASSWORD_HASH), nhánh "email
#               không tồn tại" cũng tốn 200ms thật, nên nó gia nhập luôn nhóm khuếch đại
#               CPU ở trên. Hai vấn đề, một chốt chặn.
#   upload    — đã xác thực nên truy được trách nhiệm, nhưng decode tới 40 triệu pixel
#               (~120MB RAM) và ghi tới 10MB xuống đĩa, mà KHÔNG có hạn ngạch đĩa ở bất
#               kỳ đâu. Khoá tài khoản được, nhưng ổ đĩa đã đầy trước khi ai kịp nhận ra.
#
# CỐ Ý không có nhóm cho các endpoint inference-*: đó là worker của chính mình, đặt hạn
# mức lên nó là tự bóp nghẹt hàng đợi xử lý — có hại chứ không phải trung tính.
HAN_MUC = {
    "chat": _doc("chat", 6, 20, 600),
    # Hai nhóm dưới luôn được gọi lúc chưa đăng nhập, nên chỉ cột đầu có tác dụng.
    # Vẫn điền bằng nhau để không ai đọc nhầm thành "người đăng nhập rồi thì thoải mái".
    "dang_ky": _doc("dang_ky", 5, 5, 600),
    "dang_nhap": _doc("dang_nhap", 10, 10, 600),
    # Ngược lại, upload bắt buộc đăng nhập nên chỉ cột thứ hai có tác dụng.
    "upload": _doc("upload", 30, 30, 600),
}

CUA_SO_MAC_DINH = 600

# Chặn trên số khoá theo dõi cùng lúc, để bảng không phình vô hạn khi bị gọi từ nhiều IP.
# Chạm trần thì loại khoá cũ nhất chứ KHÔNG từ chối khoá mới: từ chối sẽ biến một đợt
# tải cao thành sự cố chặn nhầm người dùng thật — tự gây ra đúng thứ đang phòng.
MAX_KHOA = int(os.getenv("RATE_MAX_KEYS", "10000"))

# OrderedDict để loại được khoá cũ nhất theo thứ tự chạm gần đây.
_so: "OrderedDict[str, list]" = OrderedDict()

# Uvicorn chạy endpoint đồng bộ trong threadpool, nên hai request có thể cùng sửa bảng.
# Thiếu khoá thì hai lời gọi sát nhau đều đọc được số cũ và cùng lọt qua.
_khoa = threading.Lock()


def khoa_cua(principal, dia_chi_ip):
    """Khoá đếm cho một người gọi: theo tài khoản nếu đã đăng nhập, theo IP nếu chưa.

    Tiền tố 'user:' / 'ip:' là bắt buộc chứ không phải cho dễ đọc: thiếu nó thì một IP
    trùng dạng với một id tài khoản sẽ dùng chung ô đếm.
    """
    uid = getattr(principal, "id", None)
    if uid is not None:
        return f"user:{uid}"
    return f"ip:{dia_chi_ip or 'khong-ro'}"


def kiem_tra(nhom: str, khoa: str, bay_gio=None):
    """Ghi nhận một lượt gọi vào một nhóm endpoint. Trả về (cho_phep, so_giay_cho).

    Cửa sổ TRƯỢT chứ không phải cửa sổ cố định theo mốc thời gian: cửa sổ cố định cho
    phép dồn gấp đôi hạn mức quanh thời điểm chuyển mốc (cuối cửa sổ này cộng đầu cửa sổ
    sau), mà đó đúng là lúc một vòng lặp tự động sẽ chạm tới.

    Lượt bị từ chối KHÔNG được ghi vào sổ. Ghi nó vào nghĩa là người đang bị chặn cứ bấm
    lại là tự đẩy lùi thời điểm mình được phục vụ — một hạn mức không bao giờ hết hạn.

    Nhóm nằm trong chính khoá đếm, nên hạn mức của các endpoint không trừ lẫn nhau: một
    phụ huynh vừa tải đủ ảnh cho hôm nay vẫn phải hỏi được trợ lý về chính những ảnh đó.
    """
    bay_gio = time.monotonic() if bay_gio is None else bay_gio
    khach, nguoi_dung, cua_so = HAN_MUC.get(nhom, (0, 0, CUA_SO_MAC_DINH))
    han_muc = nguoi_dung if khoa.startswith("user:") else khach

    khoa_day_du = f"{nhom}|{khoa}"
    moc_cu = bay_gio - cua_so

    with _khoa:
        moc = [t for t in _so.get(khoa_day_du, ()) if t > moc_cu]

        if len(moc) >= han_muc:
            _so[khoa_day_du] = moc
            _so.move_to_end(khoa_day_du)
            # Lượt cũ nhất còn trong cửa sổ hết hạn lúc nào thì lúc đó có chỗ trống.
            cho = moc[0] + cua_so - bay_gio
            return False, max(1, int(cho) + 1)

        moc.append(bay_gio)
        _so[khoa_day_du] = moc
        _so.move_to_end(khoa_day_du)

        _don_dep(bay_gio)
        return True, 0


def _don_dep(bay_gio: float) -> None:
    """Dọn khoá đã hết hiệu lực, rồi cắt bớt nếu vẫn quá trần. Gọi khi đang giữ _khoa.

    Dùng cửa sổ DÀI NHẤT trong bảng làm mốc dọn: mỗi nhóm một cửa sổ riêng, nên dọn theo
    một cửa sổ ngắn sẽ xoá nhầm khoá của nhóm có cửa sổ dài hơn và cấp lại hạn mức cho
    người đang bị chặn.
    """
    cua_so_dai_nhat = max((c for _, _, c in HAN_MUC.values()), default=CUA_SO_MAC_DINH)
    moc_cu = bay_gio - cua_so_dai_nhat
    for k in [k for k, v in _so.items() if not v or v[-1] <= moc_cu]:
        del _so[k]

    while len(_so) > MAX_KHOA:
        _so.popitem(last=False)


def xoa_het() -> None:
    """Xoá toàn bộ sổ đếm. Dùng cho kiểm thử, để các mục không ảnh hưởng lẫn nhau."""
    with _khoa:
        _so.clear()
