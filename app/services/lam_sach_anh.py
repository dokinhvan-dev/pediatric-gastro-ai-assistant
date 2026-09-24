"""Xoá siêu dữ liệu khỏi ảnh phụ huynh tải lên, trước khi ảnh chạm tới đĩa.

Vì sao module này tồn tại, và vì sao nó tách khỏi "module tiền xử lý" vẫn đang chờ:
    Ảnh chụp bằng điện thoại mang theo EXIF: toạ độ GPS nơi chụp — tức là địa chỉ nhà của
    một đứa trẻ — cùng hãng máy, giờ chụp chính xác tới giây. Trước đây ảnh được lưu NGUYÊN
    BẢN, kể cả phần đó.

    Việc xoá EXIF từng bị hoãn chung với cả module tiền xử lý, với lý do phải chờ đồng đội
    chốt input shape, chiến lược resize và bộ mean/std. Lý do ấy đúng với resize và chuẩn
    hoá — làm lệch với lúc huấn luyện thì độ chính xác tụt mà không báo lỗi — nhưng KHÔNG
    liên quan gì tới EXIF. Gộp hai thứ vào một chỗ hoãn đã giữ một việc bảo vệ riêng tư làm
    được ngay trong hàng chờ vô thời hạn. Resize và chuẩn hoá vẫn nằm ngoài module này, và
    vẫn phải chờ thông số của mô hình.

Vì sao mã hoá LẠI ảnh thay vì cắt byte siêu dữ liệu ra:
    1. Hướng xoay. Điện thoại không xoay điểm ảnh khi chụp dọc — nó ghi thẻ Orientation vào
       EXIF và để phần mềm xem ảnh tự xoay. Xoá EXIF mà không áp hướng xoay trước thì mọi
       ảnh chụp dọc sẽ nằm ngang, cả khi bác sĩ xem lẫn khi mô hình đọc. Áp hướng xoay nghĩa
       là phải đổi điểm ảnh, tức là phải mã hoá lại.
    2. Siêu dữ liệu có nhiều chỗ trú: EXIF, XMP (cũng có thể chứa GPS), IPTC, comment, các
       chunk text của PNG. Cắt từng loại theo cấu trúc byte của từng định dạng là ba bộ
       phân tích tự viết, mỗi bộ một chỗ để sót. Mã hoá lại chỉ ghi những gì ta chủ động
       truyền vào, nên mọi thứ không được liệt kê đều tự rơi mất.

Cái giá, đã cân nhắc:
    JPEG và WEBP mã hoá lại thì mất thêm một chút chất lượng. Chọn quality=95 và giữ nguyên
    độ phân giải màu (subsampling 4:4:4) để mức mất đó nhỏ hơn nhiều so với bước thu nhỏ về
    224x224 mà mô hình của đồng đội sẽ làm. PNG mã hoá lại thì không mất gì.

Giữ lại ICC profile, cố ý:
    Màu phân là thông tin lâm sàng. Ảnh chụp bằng điện thoại đời mới thường ở không gian màu
    rộng; bỏ ICC thì trình duyệt của bác sĩ hiển thị lệch màu. Nội dung ICC là mô tả không
    gian màu, không phải dữ liệu cá nhân.

Điểm cần báo cho đồng đội phụ trách mô hình:
    src/dataset.py bên đó mở ảnh bằng Image.open(...).convert("RGB") mà KHÔNG gọi
    exif_transpose. Ảnh do hệ thống này lưu thì đã được xoay đúng chiều và không còn thẻ
    Orientation; nhưng nếu ảnh huấn luyện của họ có thẻ đó, mô hình đã học trên ảnh nằm
    ngang. Cần thống nhất trước khi ghép hai phần.
"""
from io import BytesIO

from PIL import Image, ImageOps

# Chất lượng mã hoá lại cho định dạng có mất mát. Xem giải thích "Cái giá" ở trên.
CHAT_LUONG = 95

# Các khoá trong img.info có thể mang dữ liệu nhận dạng. icc_profile cố ý KHÔNG ở đây.
_KHOA_SIEU_DU_LIEU = ("exif", "xmp", "XML:com.adobe.xmp", "comment", "photoshop", "iptc")


def con_sieu_du_lieu(img) -> list:
    """Liệt kê các loại siêu dữ liệu nhận dạng còn trong một ảnh đã mở. Rỗng = sạch."""
    thay = []
    try:
        if len(img.getexif()) > 0:
            thay.append("exif")
    except Exception:
        # EXIF hỏng vẫn là EXIF. Không đọc được thì coi như còn, để phía gọi làm sạch.
        thay.append("exif_hong")
    for k in _KHOA_SIEU_DU_LIEU:
        if k in img.info and k not in thay:
            thay.append(k)
    # Chunk text của PNG (tEXt, zTXt, iTXt). Thuộc tính .text chỉ có ở ảnh PNG.
    if getattr(img, "format", None) == "PNG" and getattr(img, "text", None):
        thay.append("png_text")
    return thay


def lam_sach(file_bytes: bytes) -> bytes:
    """Trả về ảnh cùng định dạng, đã xoay đúng chiều, không còn siêu dữ liệu nhận dạng.

    Phía gọi phải kiểm định ảnh TRƯỚC (định dạng, kích thước, decompression bomb) — hàm này
    tin rằng ảnh đã hợp lệ và chỉ lo phần làm sạch.
    """
    with Image.open(BytesIO(file_bytes)) as goc:
        dinh_dang = (goc.format or "").upper()
        icc = goc.info.get("icc_profile")
        trong_suot = goc.info.get("transparency")

        try:
            anh = ImageOps.exif_transpose(goc)
        except Exception:
            # EXIF hỏng tới mức không đọc được hướng xoay: giữ nguyên chiều. Ảnh có thể nằm
            # ngang, nhưng thà vậy còn hơn từ chối một ảnh hợp lệ chỉ vì thẻ phụ bị hỏng.
            anh = goc.copy()
        anh.load()

    ra = BytesIO()
    tuy_chon = {}
    if icc:
        tuy_chon["icc_profile"] = icc

    if dinh_dang == "JPEG":
        if anh.mode not in ("RGB", "L"):
            anh = anh.convert("RGB")
        anh.save(ra, format="JPEG", quality=CHAT_LUONG, subsampling=0, optimize=True, **tuy_chon)
    elif dinh_dang == "PNG":
        if trong_suot is not None:
            tuy_chon["transparency"] = trong_suot
        anh.save(ra, format="PNG", optimize=True, **tuy_chon)
    elif dinh_dang == "WEBP":
        anh.save(ra, format="WEBP", quality=CHAT_LUONG, **tuy_chon)
    else:
        # Không bao giờ xảy ra nếu phía gọi đã kiểm định whitelist định dạng. Ném lỗi thay
        # vì trả nguyên byte gốc: trả nguyên byte gốc là lặng lẽ bỏ qua đúng việc hàm này
        # sinh ra để làm.
        raise ValueError(f"Định dạng '{dinh_dang}' không nằm trong danh sách được làm sạch.")

    return ra.getvalue()
