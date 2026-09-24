"""Điểm vào của trợ lý AI.

Endpoint này mở cho cả người chưa đăng nhập, nhưng "mở" không còn đồng nghĩa với
"thấy hết". Danh tính đọc được tới đâu thì trợ lý trả lời tới đó: người lạ chỉ hỏi được
kiến thức chung, phụ huynh xem được con mình, bác sĩ xem được ca đang duyệt. Việc phân
loại đó nằm trong app/services/agent_service.py.

Vì sao đây là endpoint DUY NHẤT còn mở sau khi P4 bỏ luồng khách vãng lai:
    Phần kiến thức chung không đọc database, không chứa PII và hai công cụ phục vụ nó
    không nhận tham số nào — không có gì để rò. Đóng nốt cửa này chỉ đổi lấy việc một
    phụ huynh đang lo lắng không hỏi nổi "phân lỏng ở bé 2 tháng có bình thường không"
    trước khi quyết định có đăng ký hay không.
"""
import logging
from datetime import timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from setup_database import SessionLocal
from app.api.deps import chan_neu_qua_han_muc, get_optional_principal
from app.services import chat_history, dong_y
from app.services.agent_service import (
    ChuaCauHinhAI, HetHanMucNgay, LoiCauHinhAI, TroLyBanTam, run_agent_chat_chi_tiet,
)
from app.services.authz import CurrentUser, la_nhan_vien_y_te

router = APIRouter(prefix="/api/v1", tags=["Chat"])

MAX_MESSAGE_LENGTH = 2000

# Dùng khi Google không kèm RetryInfo trong phản hồi lỗi.
RETRY_AFTER_MAC_DINH = 30

# Giờ hiển thị cho người dùng. Việt Nam không đổi giờ theo mùa nên một độ lệch cố định là đúng.
_GIO_VIET_NAM = timezone(timedelta(hours=7))


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_MESSAGE_LENGTH)


class CaDaTraCuu(BaseModel):
    record_id: int
    inference_status: Optional[str] = None


class ThongTinAnToan(BaseModel):
    """Những gì BACKEND đã làm với câu trả lời. Không trường nào do model sinh ra.

    Tách khỏi 'reply' để UI trình bày được hai thứ khác nhau cho khác nhau: lời diễn giải
    của Gemini, và những gì hệ thống tự kiểm soát. Trước đây client chỉ nhận một khối chữ
    liền và không có cách nào biết câu trả lời đã bị chặn, bị nhắc sửa, hay được chèn lời
    dặn đi khám.

    Đọc đúng nghĩa từng trường — tên được chọn để không hứa quá khả năng thật:
      nguon_cau_tra_loi  'model' | 'chan_an_toan' | 'du_phong'.
      ly_do              mã lý do khi không phải 'model' nguyên bản.
      da_nhac_sua        model từng vi phạm, được nhắc, và bản sửa đã đạt.
      tin_hieu_tu_khoa   từ khoá cảnh báo KHỚP CHUỖI trong tin nhắn người dùng. Không phải
                         phát hiện triệu chứng, không phải phân luồng — không có trường nào ở
                         đây làm việc đó, vì hệ thống không tính được nó một cách đáng tin.
      canh_bao_backend   đoạn backend chèn vào CUỐI 'reply'. Đã nằm sẵn trong 'reply' để
                         client bỏ qua khối này vẫn hiển thị nó.
      ca_da_tra_cuu      các ca mà công cụ thật sự trả dữ liệu trong lượt này.
    """
    nguon_cau_tra_loi: str = "model"
    ly_do: List[str] = []
    da_nhac_sua: bool = False
    tin_hieu_tu_khoa: List[str] = []
    canh_bao_backend: Optional[str] = None
    ca_da_tra_cuu: List[CaDaTraCuu] = []


class ChatResponse(BaseModel):
    # Văn bản thường: xuống dòng và gạch đầu dòng '- ', không có markdown (backend đã gỡ,
    # xem agent_service._bo_markdown). Client hiển thị nguyên văn là đúng.
    reply: str
    # Cho client biết câu trả lời này có dựa trên các lượt trước hay không. Người chưa
    # đăng nhập luôn nhận 0, và đó là thứ cần hiển thị được: "trợ lý không nhớ câu vừa
    # rồi" là một hành vi gây bối rối nếu không nói trước, chứ không phải một lỗi.
    so_luot_nho: int = 0
    # Để client biết phải hiển thị gì cạnh ô chat. Bốn giá trị:
    #   chua_dang_nhap  - chỉ kiến thức chung; những gì gõ vào vẫn được gửi cho nhà cung cấp AI
    #   chua_dong_y     - như trên, và nên mời người dùng xem thông báo rồi đồng ý
    #   da_dong_y       - trợ lý đọc được hồ sơ bé
    #   nhan_vien_y_te  - đồng thuận xét theo từng gia đình, không theo người hỏi
    # Không có trường này thì client không phân biệt được "trợ lý không đọc được hồ sơ vì
    # chưa đồng ý" với "trợ lý đọc hồ sơ nhưng không thấy gì", và người dùng cũng không bao
    # giờ được báo rằng tin nhắn của họ đi ra khỏi hệ thống.
    trang_thai_dong_y: str = "chua_dang_nhap"
    phien_ban_thong_bao: str = dong_y.PHIEN_BAN_THONG_BAO
    # BẮT BUỘC, không có giá trị mặc định. Mặc định sẽ là "nguồn model, không cảnh báo" —
    # tức là một nhánh code quên điền trường này sẽ gửi cho client một lời khẳng định sai
    # mà trông hoàn toàn hợp lệ. Bắt buộc thì quên điền là nổ lỗi ngay, không nói dối.
    an_toan: ThongTinAnToan


def _trang_thai_dong_y(principal) -> str:
    if not isinstance(principal, CurrentUser):
        return "chua_dang_nhap"
    if la_nhan_vien_y_te(principal):
        return "nhan_vien_y_te"
    with SessionLocal() as db:
        return "da_dong_y" if dong_y.nguoi_dung_da_dong_y(db, principal.id) else "chua_dong_y"


@router.get("/chat/thong-bao-du-lieu")
def thong_bao_du_lieu():
    """Nội dung thông báo về dữ liệu gửi cho nhà cung cấp AI, để client hiển thị.

    Mở cho người chưa đăng nhập: thông báo phải đọc được TRƯỚC khi quyết định có dùng ô
    chat hay không, và người chưa đăng nhập cũng đang gửi tin nhắn của họ đi.

    Danh sách trong đây được mục 24 của verify_task.py đối chiếu với payload thật, nên nó
    không thể nói một đằng trong khi hệ thống gửi một nẻo.
    """
    return dong_y.thong_bao()


@router.post("/chat", response_model=ChatResponse)
def handle_chat(
        request: ChatRequest,
        http_request: Request,
        principal: Optional[object] = Depends(get_optional_principal),
):
    # Validate nằm NGOÀI try: nếu nằm trong, HTTPException 400 sẽ bị chính except Exception
    # bên dưới bắt lại và bị biến thành 500.
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Nội dung tin nhắn không được để trống.")

    # Hạn mức đặt SAU khi đã xác định danh tính (get_optional_principal chạy như một
    # dependency) nhưng TRƯỚC khi chạm vào database hay gọi Gemini — chỗ tốn kém nằm ở
    # phía sau dòng này, nên chặn trước dòng này mới có tác dụng.
    #
    # Đây là endpoint duy nhất vừa mở cho người chưa đăng nhập vừa tốn tiền mỗi lần gọi.
    # Không có chốt này thì một vòng lặp vô danh đốt sạch hạn mức Gemini, và người mất
    # dịch vụ là phụ huynh đang cần hỏi, không phải kẻ gọi.
    chan_neu_qua_han_muc(
        "chat", http_request, principal,
        "Bạn đang gửi câu hỏi quá nhanh. Vui lòng chờ một chút rồi thử lại. "
        "Nếu bé đang có dấu hiệu bất thường cần xử trí ngay, hãy liên hệ trực tiếp bác sĩ.",
    )

    # Lịch sử lấy theo principal, KHÔNG theo bất cứ trường nào trong request body.
    # ChatRequest cố ý không có session_id: thêm một trường như thế là trao cho client
    # quyền chọn mình đọc hội thoại của ai.
    with SessionLocal() as db:
        lich_su = chat_history.doc_lich_su(db, principal)

    try:
        # principal đi xuống như một đối số của server, không phải một phần của prompt.
        # Đây là toàn bộ điểm khác biệt: thứ nằm trong prompt thì model sửa được, thứ nằm
        # trong closure của công cụ thì không.
        ket_qua = run_agent_chat_chi_tiet(request.message, principal=principal, lich_su=lich_su)
        reply_text = ket_qua.reply
    except HTTPException:
        # Giữ nguyên các lỗi nghiệp vụ đã được chủ động ném ra từ tầng dưới
        raise
    except ChuaCauHinhAI:
        # Đặt TRƯỚC nhánh except Exception, vì ChuaCauHinhAI là con của RuntimeError.
        #
        # 503 không kèm Retry-After: chờ bao lâu cũng không tự hết, nên không có con số nào
        # đúng để đưa ra. Và câu chữ phải nói đây là tính năng đang tắt, không phải sự cố —
        # "thử lại sau" ở đây là một lời nói sai khiến phụ huynh thử lại mãi.
        raise HTTPException(
            status_code=503,
            detail=(
                "Trợ lý AI hiện chưa được bật trên hệ thống này. Các chức năng khác vẫn dùng "
                "bình thường. Nếu bé đang có dấu hiệu bất thường cần xử trí ngay, hãy liên hệ "
                "trực tiếp bác sĩ."
            ),
        )
    except LoiCauHinhAI:
        # Model đã bị ngừng, hoặc khoá API sai/bị thu hồi. Cùng tinh thần với ChuaCauHinhAI:
        # 503 KHÔNG kèm Retry-After vì không có con số nào đúng — nó chỉ hết khi có người
        # sửa cấu hình. Không nói với phụ huynh là model hay khoá: đó là việc của người vận
        # hành, và log đã ghi đủ chi tiết kèm cách sửa.
        raise HTTPException(
            status_code=503,
            detail=(
                "Trợ lý AI đang tạm ngưng vì một sự cố cấu hình phía hệ thống, cần quản trị viên "
                "xử lý — thử lại lúc này sẽ chưa được. Các chức năng khác vẫn dùng bình thường. "
                "Nếu bé đang có dấu hiệu bất thường cần xử trí ngay, hãy liên hệ trực tiếp bác sĩ."
            ),
        )
    except HetHanMucNgay as het:
        # Đặt TRƯỚC nhánh TroLyBanTam vì đây là con của nó. Khác ở chỗ phải nói THẬT: không
        # phải "thử lại sau ít phút" — hôm nay thử bao nhiêu lần cũng vậy. Retry-After là
        # số giây tới lúc hạn mức chắc chắn đã làm mới, không phải retryDelay của Google.
        gio = het.mo_lai_luc.astimezone(_GIO_VIET_NAM).strftime("%H:%M ngày %d/%m")
        raise HTTPException(
            status_code=503,
            detail=(
                "Trợ lý AI đã dùng hết lượt trong ngày của hệ thống. Bạn có thể dùng lại "
                f"sau {gio} (giờ Việt Nam). Các chức năng khác vẫn dùng bình thường. "
                "Nếu bé đang có dấu hiệu bất thường cần xử trí ngay, hãy liên hệ trực tiếp bác sĩ."
            ),
            headers={"Retry-After": str(max(int(het.cho_giay), 1))},
        )
    except TroLyBanTam as ban:
        # Nhà cung cấp model quá tải, đã thử lại mà vẫn vậy. Đây KHÔNG phải 500: không
        # có gì trong hệ thống hỏng cả, và tình trạng này tự hết. Trả 503 kèm Retry-After
        # để client biết chờ rồi gọi lại là có cửa, thay vì báo với phụ huynh rằng ứng
        # dụng theo dõi sức khoẻ con họ đang lỗi.
        #
        # Retry-After lấy đúng con số Google trả về nếu có (họ nói "33s" thì ghi 33).
        # Một hằng số ta tự bịa sẽ hoặc khiến client quay lại quá sớm và ăn 429 tiếp,
        # hoặc bắt họ chờ lâu hơn cần thiết.
        cho = int(ban.cho_giay) if ban.cho_giay else RETRY_AFTER_MAC_DINH
        raise HTTPException(
            status_code=503,
            detail=(
                "Trợ lý AI đang quá tải, vui lòng thử lại sau ít phút. "
                "Nếu bé đang có dấu hiệu bất thường cần xử trí ngay, hãy liên hệ trực tiếp bác sĩ."
            ),
            headers={"Retry-After": str(max(cho, 1))},
        )
    except Exception:
        # Không bao giờ trả str(e) ra client: exception nội bộ có thể chứa API key,
        # câu SQL, hoặc đường dẫn file trên máy chủ.
        raise HTTPException(
            status_code=500,
            detail="Trợ lý AI tạm thời không phản hồi được, vui lòng thử lại sau."
        )

    # Chỉ ghi khi đã CÓ câu trả lời trong tay. Mọi nhánh lỗi ở trên đều đã raise, nên
    # tới được dòng này nghĩa là lượt đó trọn vẹn. Ghi câu hỏi sớm hơn (ngay đầu hàm)
    # sẽ tích lại trong lịch sử những câu chưa bao giờ được trả lời, và lượt sau model
    # đọc phải một cuộc đối thoại có câu hỏi treo lơ lửng rồi cố trả lời chúng.
    with SessionLocal() as db:
        chat_history.ghi_luot(db, principal, request.message, reply_text)

        # Dọn dữ liệu quá hạn theo nhịp (tối đa một lần mỗi CHAT_DON_MOI_GIAY giây mỗi tiến
        # trình). Lỗi ở đây KHÔNG được làm hỏng câu trả lời: phụ huynh đã có câu trả lời
        # trong tay, và việc dọn sẽ được thử lại ở nhịp sau. Ghi mức error để không ai
        # tưởng chính sách lưu trữ đang được thực thi trong khi nó đang hỏng.
        try:
            chat_history.don_dinh_ky(db)
        except Exception:
            logging.getLogger("bitss.chat").exception("don du lieu qua han that bai")

    return ChatResponse(
        reply=reply_text,
        so_luot_nho=len(lich_su) // 2,
        an_toan=ThongTinAnToan(
            nguon_cau_tra_loi=ket_qua.nguon,
            ly_do=ket_qua.ly_do,
            da_nhac_sua=ket_qua.da_nhac_sua,
            tin_hieu_tu_khoa=ket_qua.tin_hieu_tu_khoa,
            canh_bao_backend=ket_qua.canh_bao_backend,
            ca_da_tra_cuu=[CaDaTraCuu(**c) for c in ket_qua.ca_da_tra_cuu],
        ),
        trang_thai_dong_y=_trang_thai_dong_y(principal),
    )


@router.delete("/chat/history")
def clear_chat_history(principal: Optional[object] = Depends(get_optional_principal)):
    """Xoá lịch sử hội thoại của chính người đang đăng nhập.

    Phạm vi xoá lấy từ token, y như phạm vi đọc — không có tham số nào chỉ định xoá của
    ai, nên không có đường nào xoá nhầm sang người khác.

    Người chưa đăng nhập vốn không có lịch sử, nên ở đây trả về 0 chứ không phải 401:
    yêu cầu của họ đã được thoả mãn sẵn, và bắt đăng nhập để xoá một thứ không tồn tại
    chỉ làm client phải viết thêm nhánh xử lý cho một trường hợp vô nghĩa.
    """
    with SessionLocal() as db:
        so_dong = chat_history.xoa_lich_su(db, principal)
    return {"status": "success", "so_tin_da_xoa": so_dong}
