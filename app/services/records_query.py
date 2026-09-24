"""Truy vấn danh sách ca phân tích, dùng chung cho tầng HTTP và công cụ của agent.

Vì sao tách ra thành file riêng thay vì viết thẳng trong records.py:
    Cùng một câu hỏi — "người này được thấy những ca nào" — sắp có hai người hỏi:
    endpoint GET /api/v1/records và công cụ xu hướng của agent (P6.3). Viết hai lần
    nghĩa là hai bộ lọc phạm vi, và chỉ cần bộ nào lỏng hơn là toàn bộ công sức khoá
    endpoint ở P3 bị đi vòng qua ô chat. Đây đúng là lý do app/services/authz.py ra đời,
    chỉ khác ở chỗ lần này thứ phải dùng chung là cả câu truy vấn chứ không riêng quy tắc.

    Nên file này quyết định phạm vi, bộ lọc và phân trang; hai tầng trên chỉ dịch kết quả
    sang ngôn ngữ của mình — HTTPException cho client, dict báo lỗi cho model.

Như authz.py, file này KHÔNG biết gì về HTTP và không ném HTTPException.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from setup_database import Child, DoctorReview, StoolRecord
from app.services import bitss
from app.services.authz import owned_child_ids

# Bản sao của CHECK constraint chk_inference_status_valid và chk_review_status_valid
# trong setup_database.py. Mục 15c trong verify_task.py giữ cho hai nơi không lệch nhau:
# một bộ lọc chấp nhận trạng thái mà database không có sẽ lặng lẽ trả về rỗng mãi mãi.
TRANG_THAI_SUY_LUAN = ("queued", "processing", "completed", "failed")
TRANG_THAI_DUYET = ("not_required", "pending", "approved", "rejected", "needs_more_info")

GIOI_HAN_MAC_DINH = 20
GIOI_HAN_TOI_DA = 100


class LocKhongHopLe(ValueError):
    """Tham số lọc sai. Mỗi tầng tự dịch sang ngôn ngữ của mình (422 hoặc dict lỗi)."""

    def __init__(self, truong: str, thong_diep: str):
        super().__init__(thong_diep)
        self.truong = truong


def format_confidence(confidence) -> Optional[str]:
    if confidence is None:
        return None
    return f"{round(confidence * 100, 1)}%"


def tom_tat_record(r, ten_be: Optional[str] = None) -> dict:
    """Dòng tóm tắt một ca, dùng chung cho mọi danh sách.

    Có đúng một định nghĩa "một dòng ca trông như thế nào" để danh sách qua HTTP và
    danh sách agent đọc được không mô tả cùng một ca bằng hai bộ trường khác nhau.
    """
    d = {
        "record_id": r.id,
        "child_id": r.child_id,
        "thang_tuoi": r.age_months_at_observation,
        "che_do_an": r.feeding_type_at_observation,
        "inference_status": r.inference_status,
        "bitss_class": r.ai_predicted_class,
        "bitss_nhan": bitss.label(r.ai_predicted_class),
        "confidence": format_confidence(r.ai_confidence),
        "is_uncertain": r.is_uncertain,
        "review_status": r.review_status,
        "observed_at": r.observed_at.strftime("%Y-%m-%d %H:%M"),
    }
    # Tên bé chỉ xuất hiện khi người gọi chủ động đưa vào. Danh sách theo MỘT bé đã có
    # tên ở cấp trên rồi, nhắc lại ở từng dòng chỉ là PII rải thêm không thêm thông tin.
    if ten_be is not None:
        d["ten_be"] = ten_be
    return d


def ve_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Đưa một mốc thời gian về UTC không kèm tzinfo, để so sánh được với database.

    SQLite không có kiểu datetime có múi giờ: SQLAlchemy ghi xuống một chuỗi và VỨT
    tzinfo đi. Nên khi đọc lên, observed_at luôn là datetime naive hiểu ngầm là UTC.
    So một datetime có tzinfo với cột đó là so hai định dạng chuỗi khác nhau — nó không
    nổ, nó chỉ lặng lẽ lọc ra sai số dòng, và đó là kiểu lỗi không ai phát hiện được
    bằng mắt vì kết quả vẫn trông hợp lý.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _kiem_tra_phan_trang(limit: int, offset: int) -> None:
    if limit < 1 or limit > GIOI_HAN_TOI_DA:
        raise LocKhongHopLe(
            "limit", f"'limit' phải nằm trong khoảng 1 đến {GIOI_HAN_TOI_DA}."
        )
    if offset < 0:
        raise LocKhongHopLe("offset", "'offset' không được âm.")


def liet_ke(
        db,
        principal,
        *,
        child_id: Optional[int] = None,
        inference_status: Optional[str] = None,
        review_status: Optional[str] = None,
        chi_ca_khong_chac: bool = False,
        tu_ngay: Optional[datetime] = None,
        limit: int = GIOI_HAN_MAC_DINH,
        offset: int = 0,
) -> dict:
    """Liệt kê ca phân tích trong PHẠM VI người này được xem.

    Phạm vi lấy từ authz.owned_child_ids, và phân biệt None với [] ở đó chính là chỗ
    dễ hỏng nhất: None nghĩa là bác sĩ, không áp bộ lọc; [] nghĩa là phụ huynh chưa có
    hồ sơ bé nào, phải thấy đúng con số không. Lẫn hai giá trị này thì hoặc bác sĩ
    không thấy gì, hoặc — tệ hơn nhiều — phụ huynh chưa có con thấy toàn bộ hệ thống.
    """
    _kiem_tra_phan_trang(limit, offset)

    q = db.query(StoolRecord, Child.name).join(Child, Child.id == StoolRecord.child_id)

    # Hồ sơ đã xoá mềm thì ca của bé cũng rời khỏi mọi danh sách. Cố ý áp cho CẢ bác sĩ:
    # deleted_at tồn tại để phục vụ yêu cầu xoá dữ liệu của người nhà, và một cơ chế xoá
    # mà dữ liệu vẫn hiện ra với một nhóm người dùng nào đó thì không phải là xoá.
    q = q.filter(Child.deleted_at.is_(None))

    pham_vi = owned_child_ids(db, principal)
    if pham_vi is not None:
        # Cố ý KHÔNG viết "if pham_vi:" rồi mới lọc. Một danh sách rỗng đi qua in_() sẽ
        # thành điều kiện luôn sai — đúng thứ ta muốn. Còn nhánh if kia tạo ra một đường
        # chạy mà bộ lọc phạm vi bị bỏ qua hoàn toàn, và đó là đường duy nhất cần lỡ tay
        # một lần để rò toàn bộ dữ liệu.
        q = q.filter(StoolRecord.child_id.in_(pham_vi))

    if child_id is not None:
        # Lọc theo bé là phép GIAO với phạm vi, không phải thay thế phạm vi: bộ lọc trên
        # đã áp rồi nên phụ huynh hỏi child_id của người khác sẽ nhận danh sách rỗng.
        # Rỗng chứ không phải 403 — báo 403 là xác nhận child_id đó có thật, biến endpoint
        # thành công cụ dò xem hệ thống đang quản lý những hồ sơ nào.
        q = q.filter(StoolRecord.child_id == child_id)

    # Trạng thái sai thì báo lỗi chứ không trả rỗng. Rỗng ở đây là một lời nói dối:
    # client không phân biệt được "gõ sai tên trạng thái" với "bé chưa có ca nào".
    if inference_status is not None:
        if inference_status not in TRANG_THAI_SUY_LUAN:
            raise LocKhongHopLe(
                "inference_status",
                f"'inference_status' phải thuộc {list(TRANG_THAI_SUY_LUAN)}.",
            )
        q = q.filter(StoolRecord.inference_status == inference_status)

    if review_status is not None:
        if review_status not in TRANG_THAI_DUYET:
            raise LocKhongHopLe(
                "review_status",
                f"'review_status' phải thuộc {list(TRANG_THAI_DUYET)}.",
            )
        q = q.filter(StoolRecord.review_status == review_status)

    if chi_ca_khong_chac:
        q = q.filter(StoolRecord.is_uncertain.is_(True))

    if tu_ngay is not None:
        q = q.filter(StoolRecord.observed_at >= ve_utc_naive(tu_ngay))

    tong = q.count()

    # Sắp xếp phụ theo id là bắt buộc chứ không phải cho đẹp: observed_at hoàn toàn có
    # thể trùng nhau (nhiều ảnh chụp trong cùng một phút, hoặc dữ liệu nhập theo lô).
    # Thứ tự không ổn định cộng với phân trang sẽ làm một ca hiện hai lần ở hai trang
    # khác nhau, hoặc biến mất khỏi cả hai — và không ai nhận ra cho tới lúc bác sĩ
    # đếm thiếu một ca.
    rows = (
        q.order_by(StoolRecord.observed_at.desc(), StoolRecord.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    return {
        "total": tong,
        "limit": limit,
        "offset": offset,
        "records": [tom_tat_record(r, ten_be=ten) for r, ten in rows],
    }


# =====================================================================
# Tổng hợp xu hướng
# =====================================================================

SO_NGAY_MAC_DINH = 14
SO_NGAY_TOI_DA = 180

# Cần ít nhất chừng này ca CÓ NHÃN mới nói được câu nào về xu hướng, và mỗi nửa
# cửa sổ phải có ít nhất 2 ca. Dưới ngưỡng đó thì thứ tính ra được là nhiễu chứ
# không phải xu hướng — mà nhiễu được model kể lại bằng giọng tự tin cho một phụ
# huynh đang lo thì còn tệ hơn là không trả lời.
TOI_THIEU_CA_CO_NHAN = 4
TOI_THIEU_MOI_NUA = 2

# Chênh lệch dưới mức này trên trục 1-4 coi như không đổi. Không có ngưỡng thì mọi
# dao động lặt vặt đều được báo cáo thành "đang chuyển biến".
NGUONG_COI_LA_DOI = 0.5


def nhan_da_duyet(db, record_ids) -> dict:
    """Nhãn bác sĩ đã chốt cho từng ca: {record_id: confirmed_bitss}.

    Chỉ lấy review is_final — bản nháp hay lượt 'needs_more_info' chưa phải kết luận.
    """
    if not record_ids:
        return {}
    rows = (
        db.query(DoctorReview.record_id, DoctorReview.confirmed_bitss)
        .filter(
            DoctorReview.record_id.in_(list(record_ids)),
            DoctorReview.is_final.is_(True),
            DoctorReview.confirmed_bitss.isnot(None),
        )
        .all()
    )
    return {rid: nhan for rid, nhan in rows}


def _vi_tri_trung_binh(nhan_list):
    return round(sum(nhan_list) / len(nhan_list), 2) if nhan_list else None


def tong_hop_xu_huong(db, principal, child_id: int, so_ngay: int = SO_NGAY_MAC_DINH) -> dict:
    """Tổng hợp diễn biến của MỘT bé trong cửa sổ so_ngay ngày gần nhất.

    Hàm này chỉ đếm và so sánh, tuyệt đối không kết luận lâm sàng. Nó trả về con số
    kèm đúng những cảnh báo cần thiết để tầng trên không diễn giải quá tay.

    Ba quyết định đáng nói:

    1. Ca CHƯA có kết quả được đếm riêng chứ không bị bỏ qua im lặng. Một chuỗi 10 ca
       mà 8 ca còn nằm trong hàng chờ, nếu chỉ trả về phân bố của 2 ca còn lại, sẽ trông
       y hệt một xu hướng dựa trên 10 ca. Người đọc không có cách nào biết mình đang
       nhìn phần nổi của tảng băng.

    2. Nhãn bác sĩ đã chốt THẮNG nhãn AI. Chúng tồn tại song song trong cùng một bảng,
       và cả hệ thống này được dựng trên nguyên tắc mọi ca đều phải qua người duyệt —
       nên khi đã có kết luận của người thì dùng nhãn máy là tự phủ nhận chính mình.

    3. Không đủ dữ liệu thì TỪ CHỐI nói về xu hướng, trả 'xu_huong: None' kèm lý do,
       thay vì tính bừa trên 2 điểm rồi để model kể lại như một diễn biến có thật.
    """
    if so_ngay < 1 or so_ngay > SO_NGAY_TOI_DA:
        raise LocKhongHopLe(
            "so_ngay", f"'so_ngay' phải nằm trong khoảng 1 đến {SO_NGAY_TOI_DA}."
        )

    bay_gio = datetime.now(timezone.utc)
    moc_dau = bay_gio - timedelta(days=so_ngay)

    # Đi qua đúng liet_ke để phạm vi xem được áp y hệt endpoint HTTP. Nếu hàm này tự
    # viết truy vấn riêng thì công cụ của agent lại thành đường vòng qua lớp phân quyền.
    ket_qua = liet_ke(
        db, principal, child_id=child_id, tu_ngay=moc_dau, limit=GIOI_HAN_TOI_DA
    )
    records = ket_qua["records"]

    if ket_qua["total"] == 0:
        return {
            "child_id": child_id,
            "so_ngay": so_ngay,
            "tong_so_ca": 0,
            "xu_huong": None,
            "ly_do_chua_ket_luan": (
                f"Không có ca nào trong {so_ngay} ngày gần nhất, hoặc bạn không có "
                "quyền xem hồ sơ bé này."
            ),
        }

    # Cửa sổ dài hơn số ca lấy được thì phần thiếu phải được nói ra, không được lờ đi.
    da_cat_bot = ket_qua["total"] > len(records)

    da_duyet = nhan_da_duyet(db, [r["record_id"] for r in records])

    co_nhan = []      # (observed_at_str, nhan, nguon_nhan)
    chua_co_ket_qua = 0
    for r in records:
        nhan = da_duyet.get(r["record_id"])
        nguon = "bac_si"
        if nhan is None:
            nhan = r["bitss_class"]
            nguon = "ai"
        if nhan is None:
            chua_co_ket_qua += 1
            continue
        co_nhan.append((r["observed_at"], nhan, nguon))

    # records đang xếp mới nhất trước; đảo lại để đọc theo chiều thời gian.
    co_nhan.reverse()

    phan_bo = {str(c): 0 for c in sorted(bitss.BITSS_CLASSES)}
    for _, nhan, _ in co_nhan:
        if str(nhan) in phan_bo:
            phan_bo[str(nhan)] += 1

    ket = {
        "child_id": child_id,
        "so_ngay": so_ngay,
        "tong_so_ca": ket_qua["total"],
        "so_ca_co_nhan": len(co_nhan),
        "so_ca_chua_co_ket_qua": chua_co_ket_qua,
        "so_ca_dung_nhan_bac_si": sum(1 for _, _, ng in co_nhan if ng == "bac_si"),
        "so_ca_khong_chac": sum(1 for r in records if r["is_uncertain"]),
        "phan_bo_nhom": phan_bo,
        "ten_nhom": {str(c): bitss.label(c) for c in sorted(bitss.BITSS_CLASSES)},
        "nhan_dung_de_tinh": (
            "Ưu tiên nhãn bác sĩ đã chốt; ca nào chưa có bác sĩ duyệt thì dùng nhãn AI."
        ),
    }
    if da_cat_bot:
        ket["canh_bao_cat_bot"] = (
            f"Bé có {ket_qua['total']} ca trong khoảng này nhưng chỉ "
            f"{len(records)} ca gần nhất được đưa vào tính toán."
        )
    if chua_co_ket_qua:
        ket["canh_bao_thieu_ket_qua"] = (
            f"{chua_co_ket_qua} ca chưa có kết quả phân loại nên không nằm trong phân bố trên."
        )

    # --- Xu hướng: so nửa cũ với nửa mới của cửa sổ ---
    if len(co_nhan) < TOI_THIEU_CA_CO_NHAN:
        ket["xu_huong"] = None
        ket["ly_do_chua_ket_luan"] = (
            f"Mới có {len(co_nhan)} ca có kết quả trong {so_ngay} ngày, "
            f"cần ít nhất {TOI_THIEU_CA_CO_NHAN} ca mới nói được về xu hướng."
        )
        return ket

    giua = len(co_nhan) // 2
    nua_cu = [n for _, n, _ in co_nhan[:giua]]
    nua_moi = [n for _, n, _ in co_nhan[giua:]]

    if len(nua_cu) < TOI_THIEU_MOI_NUA or len(nua_moi) < TOI_THIEU_MOI_NUA:
        ket["xu_huong"] = None
        ket["ly_do_chua_ket_luan"] = (
            "Số ca hai nửa khoảng thời gian chưa đủ cân để so sánh."
        )
        return ket

    tb_cu = _vi_tri_trung_binh(nua_cu)
    tb_moi = _vi_tri_trung_binh(nua_moi)
    chenh = round(tb_moi - tb_cu, 2)

    if abs(chenh) < NGUONG_COI_LA_DOI:
        chieu = "khong_doi_ro_ret"
    elif chenh > 0:
        chieu = "dich_ve_phia_long_hon"
    else:
        chieu = "dich_ve_phia_cung_hon"

    ket["xu_huong"] = {
        "chieu": chieu,
        "vi_tri_trung_binh_nua_cu": tb_cu,
        "vi_tri_trung_binh_nua_moi": tb_moi,
        "chenh_lech": chenh,
        "so_ca_nua_cu": len(nua_cu),
        "so_ca_nua_moi": len(nua_moi),
        "cach_doc": (
            "Trục BITSS 1-4 đi từ phân cứng (1) đến toé nước (4). Số trung bình chỉ là "
            "cách tóm tắt vị trí trên trục đó, KHÔNG phải một nhãn BITSS và không được "
            "đọc thành tên nhóm. Đây là mô tả dữ liệu đã ghi nhận, không phải chẩn đoán."
        ),
    }
    return ket
