import datetime
import os

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    Boolean, DateTime, Date, ForeignKey, Text, CheckConstraint, Index, event, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Tự nạp .env thay vì trông vào module khác đã nạp trước: DATABASE_URL được đọc lúc import,
# mà module này thường được import SỚM NHẤT (main -> app.api.auth -> đây, trước security /
# agent_service / chat_history), nên không tự nạp thì DATABASE_URL trong .env bị bỏ qua âm
# thầm và server lẫn các script ghi vào data/bitss_clinic.db mặc định. app/api/records.py import
# module này trước khi đọc UPLOAD_DIR, nên UPLOAD_DIR trong .env cũng nhờ dòng này mà có tác dụng.
# load_dotenv() không ghi đè biến môi trường đã có, nên test gán DATABASE_URL trước vẫn thắng.
load_dotenv()

# Cho phép override để test chạy trên database tạm, không đụng vào DB thật. Mặc định nằm trong
# data/ cùng ảnh tải lên: mọi dữ liệu thật dồn về một thư mục không bao giờ được commit.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/bitss_clinic.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """Tài khoản người thật đăng nhập vào hệ thống.

    Worker suy luận CỐ Ý không có mặt ở đây. Worker là máy, không phải người: nó không
    đăng nhập, không hết phiên, không đổi mật khẩu. Ép nó thành một dòng trong bảng này
    sẽ tạo ra một "người dùng" có mật khẩu vĩnh viễn không ai đổi — thứ nguy hiểm hơn
    hẳn một service token được khai báo rõ ràng là service token.
    """
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)

    email = Column(String(255), unique=True, nullable=False, index=True)
    # Chỉ lưu hash. Không bao giờ lưu mật khẩu gốc, kể cả đã mã hoá đối xứng:
    # mã hoá thì giải mã được, còn hash thì không — và đó chính là điều ta muốn.
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)

    # Vai trò KHÔNG bao giờ được nhận từ client lúc đăng ký. Xem app/api/auth.py.
    role = Column(String(20), default="parent", nullable=False)

    # Khoá tài khoản mà không xoá dữ liệu. Được kiểm tra ở MỖI request chứ không chỉ
    # lúc đăng nhập, nên vô hiệu hoá một tài khoản có tác dụng ngay lập tức dù token
    # cũ vẫn còn hạn và vẫn đúng chữ ký.
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)

    # Đồng thuận chia sẻ hồ sơ bé với nhà cung cấp AI. Hai cột đi cùng nhau: thời điểm đồng
    # ý, và PHIÊN BẢN thông báo đã được đồng ý. Chỉ có hiệu lực khi phiên bản trùng với bản
    # hiện hành — thông báo đổi thì đồng ý cũ không phủ được thứ người dùng chưa từng được
    # báo. Xem app/services/dong_y.py.
    dong_y_ai_luc = Column(DateTime, nullable=True)
    dong_y_ai_phien_ban = Column(String(20), nullable=True)

    # Lựa chọn MẶC ĐỊNH cho đồng thuận nghiên cứu của các ảnh upload SAU này: lưu phiên bản
    # thông báo nghiên cứu mà người dùng đã đồng ý áp mặc định. None = không mặc định đồng ý.
    # Chỉ dùng để điền sẵn; đồng thuận thật nằm trên từng ca. Xem dong_y_nghien_cuu.py.
    nghien_cuu_mac_dinh_phien_ban = Column(String(20), nullable=True)

    children = relationship("Child", back_populates="owner")

    __table_args__ = (
        CheckConstraint("role IN ('parent', 'doctor', 'admin')", name="chk_user_role_valid"),
        CheckConstraint("length(trim(email)) > 0", name="chk_user_email_not_blank"),
        CheckConstraint("length(trim(full_name)) > 0", name="chk_user_name_not_blank"),
    )


class Child(Base):
    __tablename__ = "children"
    id = Column(Integer, primary_key=True, index=True)

    # Hồ sơ trẻ BẮT BUỘC có chủ. Trước đây không có cột này nên không tồn tại câu trả lời
    # cho "ai được xem hồ sơ bé nào", và hệ quả là tầng chat phải bịt tạm bằng cách giấu
    # luôn tên trẻ với TẤT CẢ mọi người — kể cả chính bố mẹ bé.
    # Cho phép NULL ở đây đồng nghĩa mở lại đúng lỗ hổng vừa vá, nên để NOT NULL.
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    weight_kg = Column(Float, nullable=True)
    feeding_type = Column(String(50), nullable=False)

    # Ghi chú tự do cho BÁC SĨ đọc. CỐ Ý không còn được gửi cho model.
    #
    # Trước đây cột này được nạp ngược vào ngữ cảnh Gemini, nên nó là một đường cho văn
    # xuôi do người dùng viết đi thẳng vào prompt — không lớp phòng thủ nào quanh đó chạm
    # tới được, vì đây không phải tấn công lấy dữ liệu mà là lái lời khuyên. Giữ lại cột
    # vì lâm sàng cần chỗ ghi những tình huống không danh mục nào phủ hết; chỉ cắt đường
    # đi của nó tới model. Xem app/services/yeu_to_lam_sang.py.
    medical_notes = Column(Text, nullable=True)

    # Yếu tố lâm sàng nền, dạng mã ngăn cách bằng dấu phẩy, lấy từ từ vựng có kiểm soát
    # trong app/services/yeu_to_lam_sang.py. ĐÂY là phần được gửi cho model thay cho
    # medical_notes: mã định danh không đọc được như mệnh lệnh vì chúng không phải câu.
    yeu_to_lam_sang = Column(String(255), nullable=True)

    deleted_at = Column(DateTime, nullable=True)
    pseudonymized_at = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="children")
    records = relationship("StoolRecord", back_populates="child")
    __table_args__ = (CheckConstraint('weight_kg > 0', name='chk_weight_positive'),)


class StoolRecord(Base):
    __tablename__ = "stool_records"
    id = Column(Integer, primary_key=True, index=True)

    # Mọi ca phân tích BẮT BUỘC thuộc về một hồ sơ bé đã đăng ký.
    # Trước đây cột này cho phép NULL để phục vụ luồng khách vãng lai. Luồng đó đã bị bỏ
    # ở P4 vì nó mâu thuẫn với chính mục đích của sản phẩm: một ca của khách là điểm dữ
    # liệu mồ côi — không chuỗi trước sau, bác sĩ mở ra chỉ thấy đúng một tấm ảnh, trong
    # khi thứ cần theo dõi là diễn biến theo thời gian.
    # Hệ quả phụ quan trọng: tuổi và chế độ ăn nay luôn được SUY RA từ hồ sơ bé, thay vì
    # nhận lời tự khai không ai kiểm chứng như đường guest cũ.
    child_id = Column(Integer, ForeignKey("children.id", ondelete="RESTRICT"), nullable=False, index=True)

    age_months_at_observation = Column(Float, nullable=False)
    feeding_type_at_observation = Column(String(50), nullable=False)

    image_path = Column(String(255), nullable=False)
    heatmap_path = Column(String(255), nullable=True)

    ai_predicted_class = Column(Integer, nullable=True)
    ai_confidence = Column(Float, nullable=True)
    is_uncertain = Column(Boolean, default=False, nullable=False)

    inference_status = Column(String(30), default="queued", nullable=False)
    # Lý do suy luận thất bại. Một ca 'failed' mà không ghi lại vì sao thì khi vận hành
    # chỉ thấy ca hỏng chứ không biết hỏng ở đâu (ảnh lỗi? model không load được? hết bộ nhớ?).
    inference_error = Column(Text, nullable=True)
    review_status = Column(String(50), default="pending", nullable=False)

    observed_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)

    # Đồng thuận của gia đình cho phép dùng RIÊNG ẢNH NÀY vào nghiên cứu / huấn luyện. Theo
    # từng ảnh vì hợp đồng dữ liệu của phần mô hình yêu cầu đúng như vậy. Rút lại không xoá
    # thời điểm đồng ý, để còn trả lời được ảnh đã từng được phép dùng trong khoảng nào.
    # Điều kiện xuất dữ liệu đầy đủ nằm ở app/services/xuat_du_lieu.py, không ở đây: nó cần
    # dữ liệu của nhiều bảng, thứ CHECK constraint của SQLite không tham chiếu được.
    nghien_cuu_dong_y_luc = Column(DateTime, nullable=True)
    nghien_cuu_phien_ban = Column(String(20), nullable=True)
    nghien_cuu_rut_luc = Column(DateTime, nullable=True)

    # [FIX CỦA BẠN]: Đã đổi back_populates="child" -> back_populates="records" để mapping hợp lệ
    child = relationship("Child", back_populates="records")
    reviews = relationship("DoctorReview", back_populates="record")

    __table_args__ = (
        # chk_owner_exclusive và chk_guest_not_blank đã bị gỡ cùng với luồng khách vãng lai.
        # Chúng tồn tại để canh "hoặc thuộc bé, hoặc thuộc phiên khách, không cả hai".
        # Khi chỉ còn đúng một đường sở hữu thì NOT NULL trên child_id đã nói trọn điều đó,
        # và một CHECK thừa chỉ tạo ảo giác là còn hai trường hợp phải phân biệt.

        # [FIX CỦA BẠN]: Chặn inference_status rác
        CheckConstraint("inference_status IN ('queued', 'processing', 'completed', 'failed')",
                        name="chk_inference_status_valid"),

        # [FIX CỦA BẠN]: Ràng buộc chặt chẽ trạng thái và kết quả AI
        # Guard IS NOT NULL tường minh: SQLite coi kết quả CHECK là NULL = hợp lệ (pass),
        # nên "ai_predicted_class IN (...)" khi ai_predicted_class NULL sẽ trả NULL chứ không phải FALSE
        # và lọt qua constraint nếu không có IS NOT NULL đứng trước.
        CheckConstraint(
            "(inference_status = 'completed' AND ai_predicted_class IS NOT NULL AND ai_predicted_class IN (1, 2, 3, 4) "
            "AND ai_confidence IS NOT NULL AND ai_confidence >= 0.0 AND ai_confidence <= 1.0) OR "
            "(inference_status IN ('queued', 'processing', 'failed') AND ai_predicted_class IS NULL AND ai_confidence IS NULL)",
            name="chk_inference_status_and_result"
        ),

        CheckConstraint("review_status IN ('not_required', 'pending', 'approved', 'rejected', 'needs_more_info')",
                        name="chk_review_status_valid"),

        # Lý do lỗi chỉ tồn tại ở ca thất bại. Chặn trường hợp ca đã chạy lại thành công
        # nhưng vẫn còn sót thông báo lỗi cũ -> người đọc tưởng ca đang hỏng.
        CheckConstraint("inference_error IS NULL OR inference_status = 'failed'",
                        name="chk_error_only_when_failed"),
        CheckConstraint("ai_predicted_class IS NOT NULL OR is_uncertain = 0",
                        name="chk_uncertainty_requires_inference"),
        CheckConstraint('age_months_at_observation >= 0', name='chk_age_positive'),
    )


class DoctorReview(Base):
    __tablename__ = "doctor_reviews"
    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("stool_records.id", ondelete="RESTRICT"), nullable=False)

    # AI danh tính thật của người chốt, lấy từ token chứ không phải từ request body.
    # SQLite không ràng buộc được "user này phải có role='doctor'" bằng CHECK (không
    # tham chiếu chéo bảng được), nên điều kiện đó do tầng ứng dụng canh — xem app/api/deps.py.
    clinician_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Ảnh chụp tên tại THỜI ĐIỂM duyệt, cố ý lặp dữ liệu với users.full_name.
    # Bác sĩ đổi tên (kết hôn, đổi học hàm) thì hồ sơ bệnh án cũ vẫn phải hiển thị đúng
    # cái tên đã ký lúc đó. Join sang users sẽ viết lại lịch sử một cách âm thầm.
    clinician_name = Column(String(100), nullable=False)
    confirmed_bitss = Column(Integer, nullable=True)
    decision = Column(String(50), default="approved", nullable=False)
    clinical_notes = Column(Text, nullable=True)

    is_final = Column(Boolean, default=False, nullable=False)
    # ĐÁNH GIÁ CHẤT LƯỢNG của bác sĩ: ảnh này có phù hợp làm dữ liệu học không (không mờ,
    # bố cục bình thường, không phải ca hiếm dễ làm lệch tập dữ liệu). KHÔNG phải quyền được
    # dùng: quyền đó thuộc về gia đình (stool_records.nghien_cuu_*). Một ca chỉ được xuất
    # khi cả hai cùng đạt, và điều kiện đó chỉ được kiểm ở app/services/xuat_du_lieu.py.
    eligible_for_training = Column(Boolean, default=False, nullable=False)
    reviewed_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)

    record = relationship("StoolRecord", back_populates="reviews")

    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected', 'needs_more_info')",
                        name="chk_review_decision_valid"),

        # Guard IS NOT NULL tường minh (xem giải thích NULL-leak ở StoolRecord.chk_inference_status_and_result)
        CheckConstraint(
            "(decision = 'approved' AND confirmed_bitss IS NOT NULL AND confirmed_bitss IN (1, 2, 3, 4)) "
            "OR (decision IN ('rejected', 'needs_more_info') AND confirmed_bitss IS NULL)",
            name="chk_review_decision_and_label"
        ),

        # [FIX CỦA BẠN]: Ngăn chặn dữ liệu rác, chốt nhãn sai chui vào tập huấn luyện lại model
        CheckConstraint(
            "eligible_for_training = 0 OR "
            "(is_final = 1 AND decision = 'approved' AND confirmed_bitss IS NOT NULL AND confirmed_bitss IN (1, 2, 3, 4))",
            name="chk_training_requires_final_approved_label"
        ),

        Index("uq_one_final_review_per_record", "record_id", unique=True, sqlite_where=text("is_final = 1")),
    )


class RevokedToken(Base):
    """Danh sách token đã bị thu hồi trước khi hết hạn.

    Vì sao cần tới nó dù token đã có hạn ngắn:
        JWT là thứ tự chứng minh — máy chủ không giữ trạng thái phiên nào cả, nên "đăng
        xuất" ở phía client chỉ là xoá token khỏi bộ nhớ trình duyệt. Bản sao nào đã kịp
        lọt ra ngoài (log proxy, lịch sử chia sẻ máy, ảnh chụp màn hình) vẫn dùng được
        cho tới lúc exp. Với một hệ thống giữ bệnh án nhi khoa thì "12 tiếng nữa nó tự
        hết hiệu lực" không phải một câu trả lời cho phụ huynh vừa báo mất máy.

    Vì sao jti làm khoá chính chứ không phải cột id tự tăng:
        Thu hồi cùng một token hai lần phải là vô hại. Với jti là khoá chính thì lần thứ
        hai đụng ràng buộc trùng khoá và ta bỏ qua; với id tự tăng thì nó lặng lẽ thành
        hai dòng, và bảng được đọc ở MỖI request sẽ phình ra theo số lần bấm nút.

    Vì sao lưu expires_at:
        Để dọn được. Token đã quá exp thì decode_token đã từ chối từ trước khi đụng tới
        bảng này, nên giữ dòng đó lại không mua thêm gì cả — chỉ làm dài thêm thứ phải
        tra ở mỗi request. Không có cột này thì không có cách nào biết dòng nào đã chết.
    """
    __tablename__ = "revoked_tokens"

    jti = Column(String(32), primary_key=True)

    # CASCADE ở đây, khác với RESTRICT của bệnh án. Bệnh án dùng RESTRICT vì xoá mất là
    # mất dữ liệu lâm sàng; dòng này chỉ là sổ sách về một token, mà token của tài khoản
    # đã bị xoá thì vốn đã không đăng nhập được nữa (deps.py tra lại users ở mỗi request).
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)
    reason = Column(String(50), default="logout", nullable=False)


class ChatMessage(Base):
    """Một lượt trong hội thoại với trợ lý, để trả lời được câu hỏi nối tiếp.

    Vì sao gắn vào user_id chứ không phải một session_id do client gửi lên:
        Session id do client tự khai là một IDOR ngang chờ sẵn — đoán hoặc bắt được id
        của người khác là đọc được hội thoại của họ, mà hội thoại ở đây chứa tên bé, tuổi,
        và diễn biến bệnh. Buộc vào danh tính đã xác thực thì phạm vi đọc được của lịch sử
        đúng bằng phạm vi của token, không rộng hơn một ly nào.

    Vì sao người lạ không có lịch sử:
        Không có danh tính thì không có gì để buộc vào, và mọi cách chế ra một mã phiên
        cho người chưa đăng nhập đều quay về đúng vấn đề ở trên.

    Vì sao CHỈ lưu text của lượt user và lượt model, không lưu lời gọi công cụ và kết quả:
        Hai lý do. Một, kết quả công cụ là dữ liệu bệnh án vừa đọc từ database — ghi lại
        tức là tạo thêm một bản sao PII trong bảng không được thiết kế để giữ nó, với
        vòng đời riêng và không nằm trong luồng xoá dữ liệu theo yêu cầu gia đình. Hai,
        cho model ăn lại kết quả cũ nghĩa là lượt sau trả lời dựa trên ảnh chụp đã cũ
        thay vì hỏi lại — bác sĩ vừa chốt nhãn xong mà trợ lý vẫn đọc nhãn AI của hôm qua.
        Vòng lặp gọi công cụ phải chạy lại từ đầu ở mỗi lượt.
    """
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # 'user' và 'model' là đúng tên vai trò của Gemini API, cố ý không đặt tên riêng:
    # một bảng ánh xạ tên nữa chỉ thêm chỗ để lệch.
    role = Column(String(10), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('user', 'model')", name="chk_chat_role_valid"),
        CheckConstraint("length(trim(content)) > 0", name="chk_chat_content_not_blank"),
        # Lịch sử luôn được đọc theo "của ai, gần đây nhất", nên index ghép đúng theo
        # thứ tự đó. Thiếu nó thì mỗi lượt chat quét toàn bảng.
        Index("ix_chat_user_time", "user_id", "created_at"),
    )


# =====================================================================
# Đồng bộ database đã tồn tại với model
#
# Vì sao cần: Base.metadata.create_all() tạo BẢNG còn thiếu nhưng tuyệt đối không đụng tới
# bảng đã tồn tại. Một cột mới thêm vào model sẽ không bao giờ xuất hiện trong database cũ,
# và lỗi chỉ lộ ra lúc chạy — đo thật: mọi lần đăng nhập đều nổ "no such column", ở request
# đầu tiên chứ không phải lúc khởi động. Database thật của dự án từng thiếu hẳn hai bảng
# được thêm ở P6.4, tức là tình huống này không phải giả định.
#
# Vì sao so với METADATA chứ không dùng một danh sách cột viết tay:
#     Bản đầu dùng danh sách COT_THEM_SAU liệt kê tay. Ai thêm cột vào model mà quên thêm
#     vào danh sách thì script migrate không thêm, còn kiểm tra lúc khởi động lại bảo người
#     vận hành "chạy setup_database.py" — một lời khuyên vô dụng cho đúng tình huống đó.
#     So thẳng với model thì danh sách không thể lạc hậu, vì không còn danh sách nào cả.
#
# Dự án chưa dùng công cụ migration (Alembic). ALTER TABLE ADD COLUMN của SQLite chỉ làm
# được với cột cho phép NULL (hoặc có giá trị mặc định ở phía server): nó không viết lại
# bảng, không đụng dữ liệu sẵn có. Cột NOT NULL không có mặc định thì KHÔNG tự thêm được
# — đó là lúc phải dựng migration thật, và kiem_tra_schema() sẽ nói rõ điều đó.
# =====================================================================


def _co_the_tu_them(cot) -> bool:
    return cot.nullable or cot.server_default is not None


def _bo_sung_cot_thieu() -> list:
    """Thêm các cột còn thiếu vào những bảng đã tồn tại. Trả về danh sách cột đã thêm.

    Chạy được nhiều lần mà không gây hại: cột nào đã có thì bỏ qua. Cột không tự thêm
    được (NOT NULL, không mặc định) bị bỏ qua ở đây và được kiem_tra_schema() báo ra.
    """
    from sqlalchemy import inspect

    da_them = []
    thanh_tra = inspect(engine)
    ten_bang_hien_co = set(thanh_tra.get_table_names())

    with engine.begin() as ket_noi:
        for bang in Base.metadata.sorted_tables:
            if bang.name not in ten_bang_hien_co:
                continue    # bảng chưa có thì create_all tạo đủ cột, không cần ALTER
            dang_co = {c["name"] for c in thanh_tra.get_columns(bang.name)}
            for cot in bang.columns:
                if cot.name in dang_co or not _co_the_tu_them(cot):
                    continue
                kieu = cot.type.compile(dialect=engine.dialect)
                # Tên bảng/cột lấy từ model do chính dự án định nghĩa, không từ dữ liệu
                # người dùng, nên ghép chuỗi ở đây không mở ra lối SQL injection nào.
                ket_noi.exec_driver_sql(f"ALTER TABLE {bang.name} ADD COLUMN {cot.name} {kieu}")
                da_them.append(f"{bang.name}.{cot.name}")
    return da_them


def kiem_tra_schema() -> list:
    """So database với model. Trả về danh sách chỗ lệch, rỗng nghĩa là khớp.

    Chỉ ĐỌC, không sửa gì. main.py gọi hàm này lúc khởi động và từ chối chạy nếu có lệch.
    """
    from sqlalchemy import inspect

    thanh_tra = inspect(engine)
    ten_bang_hien_co = set(thanh_tra.get_table_names())
    lech = []
    for bang in Base.metadata.sorted_tables:
        if bang.name not in ten_bang_hien_co:
            lech.append(f"thiếu bảng {bang.name}")
            continue
        dang_co = {c["name"] for c in thanh_tra.get_columns(bang.name)}
        for cot in bang.columns:
            if cot.name in dang_co:
                continue
            if _co_the_tu_them(cot):
                lech.append(f"thiếu cột {bang.name}.{cot.name}")
            else:
                lech.append(
                    f"thiếu cột {bang.name}.{cot.name} (NOT NULL, không tự thêm được — "
                    "cần migration thủ công)"
                )
    return lech


def init_db():
    """Tạo bảng còn thiếu và bổ sung cột còn thiếu cho database đã tồn tại.

    Chạy lệnh này MỖI LẦN triển khai phiên bản mới. Ứng dụng tự kiểm schema lúc khởi động
    và từ chối chạy nếu chưa khớp, nên quên bước này sẽ bị phát hiện ngay chứ không phải
    ở request đầu tiên của một người dùng thật.

    KHÔNG seed dữ liệu mẫu. Dữ liệu demo trước đây (init_and_seed_db) đã bị bỏ vì
    verify_task.py không còn phụ thuộc nó (verify_task.py tự dựng fixture riêng để
    test độc lập với schema demo có thể thay đổi bất cứ lúc nào).
    """
    Base.metadata.create_all(bind=engine)
    da_them = _bo_sung_cot_thieu()
    if da_them:
        print("Da bo sung cot con thieu:", ", ".join(da_them))
    con_lech = kiem_tra_schema()
    if con_lech:
        # Không ném exception: người vận hành đang đứng trước màn hình và cần đọc được
        # đầy đủ danh sách, không phải một traceback.
        print("CANH BAO - schema van con lech, can xu ly thu cong:")
        for dong in con_lech:
            print("   -", dong)
        return
    print("Schema da san sang (khong seed du lieu mau).")


if __name__ == "__main__":
    init_db()