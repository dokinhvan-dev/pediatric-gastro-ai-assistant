"""Lịch sử hội thoại với trợ lý, buộc vào danh tính đã xác thực.

Toàn bộ file này tồn tại để trả lời một câu hỏi rất đời thường: phụ huynh hỏi "bé nhà
tôi dạo này thế nào", trợ lý trả lời, rồi họ hỏi tiếp "thế có cần đi khám không?".
Không có lịch sử thì câu thứ hai rơi vào khoảng không — "cần đi khám" về cái gì?

Ba quyết định đáng nói, vì cả ba đều có cách làm dễ hơn mà sai:

1. Buộc vào user_id lấy từ token, KHÔNG phải session_id do client gửi lên.
   Cách kia dễ hơn nhiều và hoạt động ngay, kể cả cho người chưa đăng nhập. Nhưng một
   mã phiên do client tự khai thì client tự đổi được: đưa mã của người khác lên là đọc
   được hội thoại của họ, mà hội thoại ở đây có tên bé, tuổi, và diễn biến bệnh. Đó là
   IDOR ngang, đúng loại lỗ hổng mà cả P3 đã bỏ công bịt ở tầng bệnh án — mở lại nó ở
   tầng chat thì công kia thành vô nghĩa.

2. CHỈ lưu text của lượt user và lượt model. Không lưu lời gọi công cụ và kết quả.
   Kết quả công cụ là dữ liệu bệnh án vừa đọc từ database. Ghi lại tức là nhân thêm một
   bản sao PII vào bảng không được thiết kế để giữ nó, nằm ngoài luồng xoá dữ liệu theo
   yêu cầu gia đình. Và tai hại hơn về mặt lâm sàng: cho model ăn lại kết quả cũ nghĩa
   là lượt sau trả lời dựa trên ảnh chụp đã cũ. Bác sĩ vừa chốt nhãn xong mà trợ lý vẫn
   đọc nhãn AI của hôm qua là một câu trả lời sai mà nghe vẫn trôi chảy.

3. Chỉ ghi lại khi lượt đó THÀNH CÔNG.
   Ghi câu hỏi trước rồi gọi model là cách lịch sử tích lại những câu hỏi chưa bao giờ
   được trả lời. Lượt sau model đọc phải một cuộc đối thoại có câu hỏi treo lơ lửng và
   sẽ cố trả lời chúng — người dùng nhận lại câu trả lời cho câu mình hỏi lúc hệ thống
   đang lỗi, không phải câu vừa hỏi.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from dotenv import load_dotenv

from setup_database import ChatMessage
from app.services import dong_y
from app.services.authz import la_nhan_vien_y_te

# Số LƯỢT (một lượt = một câu hỏi + một câu trả lời) được nạp lại vào ngữ cảnh.
# Không phải con số tuỳ tiện: ngữ cảnh dài vừa tốn token vừa làm loãng system_instruction,
# mà chính system_instruction mới là chỗ giữ các ràng buộc an toàn lâm sàng. Lịch sử
# càng dài thì tỷ trọng của phần ràng buộc trong tổng ngữ cảnh càng nhỏ.
SO_LUOT_NHO_LAI = 6

# Chặn trên độ dài MỘT tin nhắn được nạp lại. Câu trả lời của model có thể rất dài;
# sáu câu như thế đủ đẩy phần đầu ngữ cảnh ra ngoài cửa sổ.
MAX_KY_TU_MOI_TIN = 2000

logger = logging.getLogger("bitss.chat_history")


def doc_so_ngay_luu_tru(gia_tri) -> int:
    """Đọc thời hạn lưu (ngày) từ cấu hình. Sai thì NỔ lúc khởi động, không lặng lẽ dùng số khác.

    Một chính sách lưu trữ bị đọc sai thành 0 thì xoá sạch lịch sử của mọi người ở lần dọn
    đầu tiên; bị đọc sai thành một số khổng lồ thì chính sách không còn tồn tại. Cả hai đều
    tệ hơn nhiều so với một ứng dụng từ chối khởi động kèm lý do rõ ràng.
    """
    try:
        so = int(str(gia_tri).strip())
    except (TypeError, ValueError):
        raise ValueError(f"CHAT_LUU_TRU_NGAY phai la so nguyen duong, nhan duoc: {gia_tri!r}")
    if so < 1 or so > 3650:
        raise ValueError(f"CHAT_LUU_TRU_NGAY phai trong khoang 1..3650, nhan duoc: {so}")
    return so


# Thời hạn lưu lịch sử trò chuyện. Quá hạn thì tin nhắn bị xoá hẳn khỏi database.
#
# Vì sao cần: lịch sử chat là văn bản tự do có tên bé và diễn biến bệnh, lưu dạng thường
# trong database. Không có thời hạn thì bảng này chỉ lớn lên, giữ dữ liệu y tế của cả những
# gia đình đã thôi dùng ứng dụng từ lâu. 90 ngày là đủ cho mục đích duy nhất của lịch sử —
# trả lời được câu hỏi nối tiếp — với biên rộng; trợ lý chỉ nạp lại 6 lượt gần nhất.
# Tự nạp .env thay vì trông vào module khác đã nạp trước: hằng số này được đọc lúc import,
# nên nếu module này tình cờ được import sớm hơn thì giá trị trong .env bị bỏ qua âm thầm
# và hệ thống giữ dữ liệu theo một thời hạn khác với thời hạn đã hứa trong thông báo.
load_dotenv()
SO_NGAY_LUU_TRU = doc_so_ngay_luu_tru(os.getenv("CHAT_LUU_TRU_NGAY", "90"))

# Khoảng cách tối thiểu giữa hai lần dọn toàn cục trong cùng một tiến trình.
KHOANG_DON_GIAY = int(os.getenv("CHAT_DON_MOI_GIAY", "3600"))

_lan_don_cuoi = None           # time.monotonic() của lần dọn gần nhất, None = chưa dọn lần nào
_khoa_don = threading.Lock()


def moc_het_han(bay_gio=None) -> datetime:
    """Tin nhắn tạo TRƯỚC mốc này là quá hạn. Naive UTC, để so được với cột của SQLite."""
    bay_gio = bay_gio or datetime.now(timezone.utc)
    if bay_gio.tzinfo is not None:
        bay_gio = bay_gio.astimezone(timezone.utc).replace(tzinfo=None)
    return bay_gio - timedelta(days=SO_NGAY_LUU_TRU)


def _user_id_cua(principal) -> Optional[int]:
    """Lấy id người dùng, trả None cho người lạ.

    Người lạ KHÔNG có lịch sử, và đó là hệ quả trực tiếp của quyết định số 1 ở đầu file:
    không có danh tính thì không có gì để buộc vào. Mọi cách chế ra mã phiên cho người
    chưa đăng nhập đều quay về đúng lỗ hổng đã nêu.
    """
    return getattr(principal, "id", None)


def co_luu_lich_su(principal) -> bool:
    """Người này có được lưu và nạp lại lịch sử hội thoại không.

    Bác sĩ và admin KHÔNG có lịch sử, dù đã đăng nhập. Lý do nằm ở chỗ lịch sử của họ chứa
    gì: một bác sĩ hỏi về ca của nhiều gia đình trong cùng một luồng hội thoại, nên lịch sử
    của họ là văn bản tự do trộn dữ liệu của nhiều gia đình khác nhau. Mà mỗi gia đình có
    thể rút đồng thuận, hoặc xoá hồ sơ bé, vào bất cứ lúc nào — và không có cách nào lọc
    văn bản tự do theo gia đình (cùng lý do endpoint xoá hồ sơ bé phải xoá cả lịch sử).

    Hệ quả cụ thể nếu giữ: gia đình A xoá hồ sơ bé, lịch sử của CHÍNH HỌ bị xoá theo, nhưng
    lịch sử của bác sĩ từng hỏi về bé vẫn còn tên bé và vẫn được gửi lại cho model ở lượt
    sau. Lời hứa "dữ liệu không còn ở đâu" bị thủng ở một chỗ không ai nghĩ tới.

    Cái giá: bác sĩ mất trí nhớ hội thoại nhiều lượt. Chấp nhận được, vì bác sĩ tiếp cận
    theo từng ca, và mỗi câu hỏi của họ thường đã kèm sẵn record_id.
    """
    return _user_id_cua(principal) is not None and not la_nhan_vien_y_te(principal)


def doc_lich_su(db, principal) -> List[ChatMessage]:
    """Các lượt gần nhất của CHÍNH người này, xếp theo chiều thời gian.

    Phạm vi đọc đúng bằng phạm vi của token vì bộ lọc user_id lấy từ principal chứ không
    từ tham số nào do client điều khiển được — không có đường nào truyền vào id của
    người khác, kể cả gọi nhầm.
    """
    if not co_luu_lich_su(principal):
        return []
    uid = _user_id_cua(principal)

    # Chưa (hoặc không còn) đồng ý chia sẻ thì KHÔNG nạp lịch sử. Lịch sử của phụ huynh chứa
    # những câu trả lời được dựng từ hồ sơ bé ở các lượt họ còn đồng ý; nạp nó lại khi đồng
    # thuận không còn hiệu lực là gửi hồ sơ bé ra ngoài lần nữa, qua đường vòng.
    # Rút đồng ý thì lịch sử bị XOÁ luôn (app/api/auth.py), nhưng cổng này vẫn cần: tăng
    # phiên bản thông báo làm đồng thuận cũ hết hiệu lực mà không xoá gì cả.
    if not dong_y.nguoi_dung_da_dong_y(db, uid):
        return []

    # Lọc quá hạn NGAY LÚC ĐỌC, không chỉ trông vào lần dọn: việc dọn chạy theo nhịp, nên
    # giữa hai lần dọn luôn có một khoảng tin đã quá hạn mà chưa bị xoá. Chính sách "không
    # dùng dữ liệu quá hạn" phải đúng cả trong khoảng đó.
    # Lấy N*2 dòng mới nhất rồi đảo lại. Sắp xếp giảm dần rồi cắt là cách duy nhất lấy
    # được phần ĐUÔI mà không đọc cả bảng; phần đảo chiều để đọc xuôi làm ở bộ nhớ.
    # id là khoá phụ để hai tin trong cùng một giây không đổi chỗ cho nhau.
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == uid, ChatMessage.created_at >= moc_het_han())
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(SO_LUOT_NHO_LAI * 2)
        .all()
    )
    rows.reverse()

    # Ngữ cảnh phải BẮT ĐẦU bằng lượt của user. Cắt theo số dòng có thể rơi đúng vào
    # giữa một cặp và để lại câu trả lời mồ côi ở đầu; Gemini từ chối contents mở đầu
    # bằng role 'model', nên đây không chỉ là chuyện gọn gàng mà là lỗi 400 thật sự.
    while rows and rows[0].role != "user":
        rows.pop(0)

    return rows


def ghi_luot(db, principal, cau_hoi: str, cau_tra_loi: str) -> bool:
    """Ghi lại MỘT cặp hỏi-đáp đã hoàn tất. Trả False nếu không có gì được ghi.

    Ghi cả cặp trong một commit, không tách làm hai lần. Tách ra thì có cửa sổ mà trong
    đó lịch sử chứa câu hỏi không có câu trả lời — xem quyết định số 3 ở đầu file.
    """
    if not co_luu_lich_su(principal):
        return False
    uid = _user_id_cua(principal)

    hoi = (cau_hoi or "").strip()
    dap = (cau_tra_loi or "").strip()
    # Ràng buộc chk_chat_content_not_blank sẽ chặn ở tầng database, nhưng để nó nổ ra
    # thành IntegrityError giữa một lượt chat là biến chuyện "không có gì để lưu" thành
    # lỗi 500 trả về cho phụ huynh.
    if not hoi or not dap:
        return False

    db.add(ChatMessage(user_id=uid, role="user", content=hoi[:MAX_KY_TU_MOI_TIN]))
    db.add(ChatMessage(user_id=uid, role="model", content=dap[:MAX_KY_TU_MOI_TIN]))
    db.commit()
    return True


def xoa_lich_su_chua_commit(db, principal) -> int:
    """Xoá lịch sử nhưng KHÔNG commit, để người gọi gộp vào transaction của họ.

    Tách ra vì endpoint xoá hồ sơ bé cần xoá hồ sơ và xoá lịch sử trong CÙNG một
    transaction. Hai lần commit riêng tạo ra một cửa sổ mà trong đó hồ sơ đã bị xoá
    nhưng lịch sử vẫn còn — tức là đúng cái lỗ hổng mà việc xoá lịch sử sinh ra để bịt,
    chỉ khác là nó xuất hiện ngẫu nhiên thay vì luôn luôn. Cùng lý do đã ghi ở
    app/api/doctor.py khi ghi review và cập nhật record.
    """
    uid = _user_id_cua(principal)
    if uid is None:
        return 0
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == uid)
        .delete(synchronize_session=False)
    )


def don_lich_su_qua_han(db, bay_gio=None) -> int:
    """Xoá tin nhắn quá hạn của MỌI người dùng. Trả về số dòng đã xoá. Tự commit.

    Vì sao dọn TOÀN CỤC chứ không dọn theo từng người lúc họ chat:
        Dọn theo từng người là cách dễ nghĩ tới nhất — và nó bỏ sót đúng nhóm cần dọn nhất:
        người đã thôi dùng ứng dụng. Họ không bao giờ quay lại để kích hoạt việc dọn, nên dữ
        liệu của họ nằm đó vĩnh viễn — đúng thứ mà một chính sách lưu trữ sinh ra để ngăn.
    """
    so = (
        db.query(ChatMessage)
        .filter(ChatMessage.created_at < moc_het_han(bay_gio))
        .delete(synchronize_session=False)
    )
    db.commit()
    return so


def don_dinh_ky(db, bay_gio_don_dieu=None) -> Optional[dict]:
    """Dọn dữ liệu quá hạn nếu đã tới nhịp. Trả dict số dòng đã dọn theo loại, hoặc None nếu
    chưa tới nhịp.

    Được gọi từ đường chat, và lúc ứng dụng khởi động. Vì sao cần cả cơ chế trong ứng dụng
    thay vì chỉ trông vào cron: một chính sách chỉ được thực thi khi có người nhớ cài cron là
    chính sách sẽ âm thầm không được thực thi ở lần triển khai mà người đó quên. Script
    scripts/don_du_lieu_qua_han.py vẫn có để chạy bằng cron khi server rảnh lâu ngày.

    Gánh luôn sổ token đã thu hồi: nó mắc đúng cái bẫy cũ — chỉ được dọn lúc có người đăng
    xuất — nên các dòng quá hạn nằm lại mãi nếu không ai đăng xuất nữa.
    """
    global _lan_don_cuoi
    bay_gio_dd = time.monotonic() if bay_gio_don_dieu is None else bay_gio_don_dieu
    with _khoa_don:
        if _lan_don_cuoi is not None and bay_gio_dd - _lan_don_cuoi < KHOANG_DON_GIAY:
            return None
        _lan_don_cuoi = bay_gio_dd

    from app.services import token_store
    ket = {
        "tin_nhan_qua_han": don_lich_su_qua_han(db),
        "token_thu_hoi_het_han": token_store.don_token_het_han(db),
    }
    if any(ket.values()):
        logger.info("don du lieu qua han: %s", ket)
    return ket


def dat_lai_nhip_don() -> None:
    """Quên lần dọn gần nhất, để lần gọi kế tiếp dọn ngay. Dùng cho kiểm thử."""
    global _lan_don_cuoi
    with _khoa_don:
        _lan_don_cuoi = None


def xoa_lich_su(db, principal) -> int:
    """Xoá toàn bộ lịch sử chat của chính người này, trả về số dòng đã xoá.

    Cần có vì lịch sử chat là dữ liệu cá nhân do người dùng tạo ra, và họ phải tự xoá
    được mà không cần nhờ tới quản trị viên. Cũng là lối thoát khi trợ lý bám vào một
    hiểu lầm từ lượt trước và không cách nào lái nó ra.
    """
    so_dong = xoa_lich_su_chua_commit(db, principal)
    db.commit()
    return so_dong
