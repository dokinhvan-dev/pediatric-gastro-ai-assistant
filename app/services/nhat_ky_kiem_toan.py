"""Nhật ký kiểm toán: ghi ai làm gì với hồ sơ nào vào bảng audit_log.

Hai cách ghi:
    ghi(db, ...)       thêm dòng vào phiên database của chính thao tác. Thao tác có GHI dữ liệu
                       (đồng thuận, duyệt ca, xoá hồ sơ) dùng cách này: hành động và dòng nhật
                       ký cùng commit hoặc cùng mất — không có hành động nào xảy ra mà không
                       để lại dấu vết, cũng không có dấu vết cho hành động không xảy ra.
    ghi_rieng(...)     mở phiên riêng và commit ngay. Dùng cho lượt bị từ chối (phiên của
                       request sẽ không commit vì exception) và cho những chỗ không có phiên.

Đường ĐỌC dữ liệu nhạy cảm (xem ca, xem ảnh, trợ lý đọc hồ sơ) ghi nhật ký TRƯỚC khi trả dữ
liệu: không ghi được thì không trả. Một lượt xem không để lại dấu vết là đúng thứ nhật ký
này sinh ra để không cho phép.

Chỉ ghi mã định danh và mã hành động. Không tên, không email, không nội dung tin nhắn, không
kết quả lâm sàng: nhật ký thường được giữ lâu hơn dữ liệu nó mô tả.
"""
from contextvars import ContextVar
from typing import Optional

from setup_database import AuditLog, SessionLocal
from app.services.authz import CurrentUser

# Mã hành động. Hằng số thay vì chuỗi rải rác: truy vấn nhật ký dựa vào đúng các mã này.
DANG_KY = "dang_ky"
DANG_NHAP = "dang_nhap"
DANG_NHAP_THAT_BAI = "dang_nhap_that_bai"
DANG_XUAT = "dang_xuat"
DONG_Y_AI = "dong_y_ai"
RUT_DONG_Y_AI = "rut_dong_y_ai"
DAT_NGHIEN_CUU_MAC_DINH = "dat_nghien_cuu_mac_dinh"
TAO_HO_SO_BE = "tao_ho_so_be"
XOA_HO_SO_BE = "xoa_ho_so_be"
XOA_LICH_SU_CHAT = "xoa_lich_su_chat"
TAI_ANH_LEN = "tai_anh_len"
XEM_CA = "xem_ca"
XEM_ANH = "xem_anh"
XEM_DANH_SACH_DUYET = "xem_danh_sach_duyet"
DUYET_CA = "duyet_ca"
DONG_Y_NGHIEN_CUU_CA = "dong_y_nghien_cuu_ca"
RUT_DONG_Y_NGHIEN_CUU_CA = "rut_dong_y_nghien_cuu_ca"
TRO_LY_DOC_DU_LIEU = "tro_ly_doc_du_lieu"
WORKER_LAY_ANH = "worker_lay_anh"
WORKER_BAT_DAU = "worker_bat_dau"
WORKER_GHI_KET_QUA = "worker_ghi_ket_qua"
WORKER_BAO_LOI = "worker_bao_loi"
WORKER_THU_LAI = "worker_thu_lai"
# Đã đăng nhập, đối tượng CÓ tồn tại, nhưng không được phép: dấu hiệu dò ID (BOLA).
TRUY_CAP_BI_TU_CHOI = "truy_cap_bi_tu_choi"

THANH_CONG = "success"
BI_TU_CHOI = "denied"

VAI_AN_DANH = "anon"
VAI_WORKER = "worker"
# Đánh dấu worker suy luận: không phải người, không có user_id.
WORKER = object()

# IP của request đang phục vụ. Middleware trong main.py đặt giá trị; contextvar đi theo
# request kể cả khi endpoint đồng bộ chạy trong threadpool.
_ip_hien_tai: ContextVar[Optional[str]] = ContextVar("ip_kiem_toan", default=None)


def dat_ip(ip: Optional[str]):
    return _ip_hien_tai.set(ip)


def bo_ip(moc) -> None:
    _ip_hien_tai.reset(moc)


def _nguoi_lam(principal) -> tuple:
    if principal is WORKER:
        return None, VAI_WORKER
    if isinstance(principal, CurrentUser):
        return principal.id, principal.role
    return None, VAI_AN_DANH


def _dong(principal, hanh_dong: str, loai: Optional[str], doi_tuong_id: Optional[int],
          ket_qua: str, chi_tiet: Optional[str]) -> AuditLog:
    uid, vai = _nguoi_lam(principal)
    return AuditLog(
        actor_user_id=uid, actor_role=vai, action=hanh_dong, object_type=loai,
        object_id=doi_tuong_id, outcome=ket_qua, ip=_ip_hien_tai.get(),
        detail=(chi_tiet or None) and str(chi_tiet)[:100],
    )


def ghi(db, principal, hanh_dong: str, loai: Optional[str] = None, doi_tuong_id: Optional[int] = None,
        ket_qua: str = THANH_CONG, chi_tiet: Optional[str] = None) -> None:
    """Thêm một dòng vào phiên của thao tác; người gọi commit cùng với hành động."""
    db.add(_dong(principal, hanh_dong, loai, doi_tuong_id, ket_qua, chi_tiet))


def ghi_rieng(principal, hanh_dong: str, loai: Optional[str] = None, doi_tuong_id: Optional[int] = None,
              ket_qua: str = THANH_CONG, chi_tiet: Optional[str] = None) -> None:
    """Ghi và commit ngay trong phiên riêng."""
    with SessionLocal() as db:
        db.add(_dong(principal, hanh_dong, loai, doi_tuong_id, ket_qua, chi_tiet))
        db.commit()


def doc_theo_doi_tuong(db, loai: str, doi_tuong_id: int) -> list:
    """Mọi dòng về một đối tượng, cũ trước. Dùng cho điều tra và kiểm thử."""
    return (
        db.query(AuditLog)
        .filter(AuditLog.object_type == loai, AuditLog.object_id == doi_tuong_id)
        .order_by(AuditLog.id.asc())
        .all()
    )
