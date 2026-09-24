"""Quy tắc "ai được xem gì", viết dưới dạng hàm thuần trả về True/False.

Vì sao tách khỏi app/api/deps.py:
    Cùng một câu hỏi — "người này có được đọc ca phân tích kia không" — được hỏi ở hai
    nơi với hai cách trả lời khác nhau. Tầng HTTP cần một exception 404. Tầng agent cần
    một dict báo lỗi để model diễn đạt lại cho phụ huynh; ném HTTPException ra giữa vòng
    lặp gọi tool của Gemini sẽ chui qua SDK rồi nổ thành 500, tức là lỗi phân quyền bị
    báo cho người dùng như một sự cố hệ thống.

    Nếu để mỗi tầng tự viết điều kiện của mình thì chúng sẽ lệch nhau — không phải nếu,
    mà là khi nào. Lệch theo hướng tầng agent lỏng hơn tầng HTTP nghĩa là toàn bộ công
    sức khoá endpoint ở đợt trước bị đi vòng qua ô chat. Nên quy tắc nằm đúng một chỗ:
    file này quyết định, các tầng trên chỉ dịch kết quả sang ngôn ngữ của mình.

Nguyên tắc: file này KHÔNG biết gì về HTTP và KHÔNG ném exception phân quyền.
"""
from dataclasses import dataclass
from typing import Optional

ROLE_PARENT = "parent"
ROLE_DOCTOR = "doctor"
ROLE_ADMIN = "admin"

# Hai vai trò được xem hồ sơ của người lạ vì nghề nghiệp đòi hỏi thế.
ROLES_XEM_MOI_HO_SO = (ROLE_DOCTOR, ROLE_ADMIN)


@dataclass(frozen=True)
class CurrentUser:
    """Ảnh chụp danh tính người gọi, cố ý KHÔNG phải đối tượng ORM.

    Trả thẳng đối tượng User của SQLAlchemy ra ngoài sẽ kéo theo một instance đã rời
    session: chạm vào quan hệ chưa nạp thì nổ DetachedInstanceError ở tận tầng endpoint,
    xa chỗ gây lỗi. Ngoài ra dataclass đóng băng khiến không endpoint nào lỡ tay sửa
    danh tính người gọi rồi ghi ngược xuống database.
    """
    id: int
    email: str
    full_name: str
    role: str


def la_nhan_vien_y_te(principal) -> bool:
    """Bác sĩ hoặc admin — nhóm được phép đọc hồ sơ của người lạ."""
    return isinstance(principal, CurrentUser) and principal.role in ROLES_XEM_MOI_HO_SO


def da_xoa_mem(child) -> bool:
    """Hồ sơ bé này đã bị xoá mềm chưa.

    Tách thành một hàm có tên riêng thay vì viết thẳng "child.deleted_at is not None" ở
    từng chỗ, để câu hỏi "chỗ nào đang tôn trọng việc xoá" trả lời được bằng một lần grep.
    Trước P7.1 thì điều kiện này nằm rải rác ở tầng truy vấn, và đúng như dự đoán, phần
    lớn chỗ gọi đã quên nó.
    """
    return getattr(child, "deleted_at", None) is not None


def can_view_child(db, child, principal) -> bool:
    """Người này có được xem hồ sơ của bé kia không.

    Mặc định là KHÔNG: hàm chỉ trả True khi rơi đúng vào một nhánh được liệt kê. Viết
    theo hướng ngược lại ("ai không thuộc diện cấm thì cho qua") sẽ tự động mở cửa cho
    mọi loại principal thêm vào sau này.
    """
    if child is None or principal is None:
        return False

    # Kiểm tra xoá mềm đặt TRƯỚC nhánh nhân viên y tế, và đó là toàn bộ điểm mấu chốt.
    # deleted_at tồn tại để phục vụ yêu cầu xoá dữ liệu của gia đình; một cơ chế xoá mà
    # dữ liệu vẫn hiện với một nhóm người dùng nào đó thì không phải là xoá, và với
    # người nhà đã yêu cầu xoá thì "chỉ bác sĩ mới thấy" không phải một lời giải thích
    # chấp nhận được.
    #
    # Vì sao nằm ở đây chứ không ở từng câu truy vấn: trước P7.1 nó nằm ở tầng truy vấn,
    # và chỉ 2 trong số 7 đường đọc nhớ áp nó. Quy tắc đặt trong hàm quyết định thì
    # không có chỗ gọi nào quên được — muốn xem hồ sơ bé thì phải đi qua đây.
    if da_xoa_mem(child):
        return False

    if la_nhan_vien_y_te(principal):
        return True
    if isinstance(principal, CurrentUser):
        return child.owner_user_id == principal.id
    return False


def can_view_record(db, record, principal) -> bool:
    """Người này có được xem ca phân tích kia không.

    Đây chính là quy tắc vá lỗ hổng IDOR: trước khi có nó, mọi ca phân tích đều đọc
    được chỉ bằng cách đếm record_id từ 1 trở lên.

    Hai nhóm được phép, không có nhóm thứ ba:
      - Bác sĩ và admin: xem được mọi ca, vì nghề của họ là duyệt ca của người lạ.
      - Phụ huynh: chỉ ca của con mình, đối chiếu qua children.owner_user_id.

    Nhóm khách vãng lai đã biến mất cùng luồng guest ở P4. Nhờ vậy quyền xem một ca giờ
    quy về đúng một câu hỏi — ai sở hữu hồ sơ bé gắn với ca đó — thay vì hai đường song
    song mà chỉ cần một đường lỏng hơn là cả quy tắc vô nghĩa.

    Vì sao KHÔNG có nhánh tắt "là bác sĩ thì cho qua ngay" ở đây, dù nghe rất hợp lý:
        Nhánh đó từng tồn tại, và nó trả True TRƯỚC khi kịp nạp hồ sơ bé. Nghĩa là mọi
        quy tắc gắn vào hồ sơ bé — mà xoá mềm là một — không bao giờ được hỏi tới trên
        đường đọc một ca. Lúc thêm kiểm tra xoá mềm vào can_view_child ở P7.1, hàm này
        vẫn để lọt bác sĩ đọc ca của bé đã xoá, và bài 18b bắt được đúng chỗ đó.

        Bỏ nhánh tắt không làm bác sĩ mất quyền gì: can_view_child đã có sẵn nhánh nhân
        viên y tế. Khác biệt duy nhất là giờ bác sĩ đi qua cùng một cửa với mọi người,
        nên mọi quy tắc mới thêm vào cửa đó tự động áp cho họ. Hai hàm quyết định mà một
        hàm có lối đi vòng thì lối vòng ấy chính là chỗ quy tắc đi lạc.
    """
    from setup_database import Child   # nội bộ hàm để tránh phụ thuộc vòng lúc import

    if record is None or principal is None:
        return False

    if isinstance(principal, CurrentUser):
        # child_id nay là NOT NULL ở tầng database, nhưng vẫn kiểm tra: hàm này cũng
        # được gọi với đối tượng chưa commit, và một None lọt xuống truy vấn bên dưới
        # sẽ thành "Child.id == NULL" — không khớp gì, nhưng vì lý do sai.
        if record.child_id is None:
            return False
        child = db.query(Child).filter(Child.id == record.child_id).first()
        return can_view_child(db, child, principal)

    return False


def owned_child_ids(db, principal) -> Optional[list]:
    """Danh sách id các bé mà người này sở hữu. None nghĩa là "không giới hạn" (bác sĩ).

    Phân biệt None với [] là có chủ ý: danh sách rỗng nghĩa là phụ huynh chưa tạo hồ sơ
    bé nào, còn None nghĩa là không áp bộ lọc. Dùng chung một giá trị cho hai ý nghĩa đó
    sẽ khiến một câu truy vấn lỡ tay biến bác sĩ thành người không thấy gì, hoặc tệ hơn,
    biến phụ huynh chưa có con thành người thấy tất cả.
    """
    from setup_database import Child

    if la_nhan_vien_y_te(principal):
        return None
    if isinstance(principal, CurrentUser):
        # Bé đã xoá mềm KHÔNG nằm trong phạm vi, y hệt quy tắc ở can_view_child. Hai hàm
        # này trả lời cùng một câu hỏi ở hai dạng (một đối tượng / một danh sách id), nên
        # để chúng lệch nhau về điều kiện xoá là tạo ra đúng loại lỗ hổng mà cả file này
        # sinh ra để chặn.
        rows = (
            db.query(Child.id)
            .filter(Child.owner_user_id == principal.id, Child.deleted_at.is_(None))
            .all()
        )
        return [r[0] for r in rows]
    return []
