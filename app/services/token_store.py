"""Sổ thu hồi token: ghi nhận token nào không được dùng nữa dù chữ ký vẫn đúng.

Vì sao là một module riêng chứ không nằm trong security.py:
    security.py có một ranh giới được ghi rõ ngay trong docstring của nó — không biết gì
    về database. Ranh giới đó có ích: nó khiến phần mật mã kiểm tra được mà không cần
    dựng dữ liệu, và giữ cho "chữ ký này có hợp lệ không" tách khỏi "hệ thống có còn
    chấp nhận nó không". Hai câu hỏi khác nhau, và câu thứ hai mới là câu cần tra bảng.

Vì sao không nằm luôn trong deps.py:
    deps.py là tầng API. Endpoint đăng xuất cần GHI vào sổ này, và để auth.py phải import
    từ deps.py là đi ngược chiều phụ thuộc — tầng API gọi sang tầng API. Đặt ở services
    thì cả hai bên cùng gọi xuống, không bên nào gọi ngang.

Giới hạn đã biết, ghi lại để không ai tưởng đây là cơ chế hoàn chỉnh:
    Thu hồi chỉ có hiệu lực với token do CHÍNH hệ thống này phát và còn đọc được jti.
    Đây cũng không phải "đăng xuất mọi thiết bị": mỗi lần đăng nhập là một jti riêng,
    nên thu hồi một token không đụng tới các token khác của cùng tài khoản. Muốn chặn
    sạch một tài khoản thì công cụ đúng là cờ is_active, và nó đã có sẵn.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError

from setup_database import RevokedToken


def _ve_utc_naive(dt: datetime) -> datetime:
    """Bỏ tzinfo để so sánh được với cột DateTime của SQLite.

    Cùng lý do đã ghi ở app/services/records_query.py: SQLite không lưu múi giờ, nên một
    datetime có tzinfo đem so với cột đọc lên từ database là so hai định dạng khác nhau.
    Nó không nổ, nó chỉ lặng lẽ cho ra kết quả sai.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def thu_hoi(db, payload: dict, ly_do: str = "logout") -> bool:
    """Ghi jti của một token đã giải mã vào sổ thu hồi.

    Nhận payload đã decode chứ không nhận chuỗi token thô: như vậy hàm này không bao giờ
    phải tự kiểm chữ ký, và cũng không có đường nào gọi nó với một token chưa được xác
    thực. Chuỗi token gốc cũng không đi qua đây, nên không có nguy cơ nó lọt vào log.

    Trả về False nếu token không có jti — token cũ do bản trước phát ra có thể thiếu
    trường này. Không thu hồi được thì phải nói ra, chứ không được báo thành công.
    """
    jti = payload.get("jti")
    exp = payload.get("exp")
    sub = payload.get("sub")
    if not jti or not exp or not sub:
        return False

    dong = RevokedToken(
        jti=jti,
        user_id=int(sub),
        expires_at=datetime.fromtimestamp(int(exp), tz=timezone.utc).replace(tzinfo=None),
        revoked_at=datetime.now(timezone.utc).replace(tzinfo=None),
        reason=ly_do,
    )
    try:
        db.add(dong)
        db.commit()
    except IntegrityError:
        # Đã thu hồi rồi. Bấm đăng xuất hai lần, hoặc hai tab cùng gửi, đều phải là
        # chuyện vô hại — không phải lỗi để báo về client.
        db.rollback()
    return True


def da_thu_hoi(db, jti: Optional[str]) -> bool:
    """Token này có nằm trong sổ thu hồi không.

    jti rỗng trả về False chứ không phải True: hàm này chỉ trả lời đúng một câu hỏi hẹp.
    Việc token thiếu jti có đáng bị từ chối hay không là quyết định của tầng gọi, và trộn
    hai quyết định vào một giá trị trả về sẽ khiến chỗ gọi không phân biệt được "đã thu
    hồi" với "token dị dạng".
    """
    if not jti:
        return False
    return db.query(RevokedToken.jti).filter(RevokedToken.jti == jti).first() is not None


def don_token_het_han(db) -> int:
    """Xoá các dòng đã quá hạn, trả về số dòng đã xoá.

    Token quá exp thì decode_token đã từ chối từ trước khi chạm tới bảng này, nên giữ lại
    không mua thêm an toàn nào — chỉ làm dài thêm thứ được tra ở MỖI request. Không dọn
    thì bảng chỉ có lớn lên, và nó lớn theo số lần đăng xuất của toàn hệ thống.

    Được gọi lúc đăng xuất (chỗ đó vốn đã ghi vào bảng), và trong lần dọn định kỳ của
    chat_history.don_dinh_ky — vì nếu chỉ dọn lúc đăng xuất thì hệ thống không còn ai đăng
    xuất sẽ không bao giờ dọn.
    """
    bay_gio = _ve_utc_naive(datetime.now(timezone.utc))
    so_dong = (
        db.query(RevokedToken)
        .filter(RevokedToken.expires_at < bay_gio)
        .delete(synchronize_session=False)
    )
    db.commit()
    return so_dong
