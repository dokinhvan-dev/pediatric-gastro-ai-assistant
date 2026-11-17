"""Cấp, thu hồi, liệt kê token dịch vụ riêng của từng worker suy luận.

Người vận hành gọi qua scripts/token_worker.py. Mỗi thao tác ghi một dòng nhật ký kiểm toán
(vai 'van_hanh') cùng transaction với thay đổi.
"""
import re
from datetime import datetime, timedelta, timezone

from setup_database import WorkerToken
from app.services import nhat_ky_kiem_toan as kiem_toan
from app.services import security

SO_NGAY_MAC_DINH = 90
# Trần một năm: token "vĩnh viễn" là đúng thứ bí mật dùng chung cũ, chỉ đổi tên. Hết hạn buộc
# người vận hành cấp lại định kỳ, và token bị lộ mà không ai biết cũng có ngày tự chết.
SO_NGAY_TOI_DA = 365
_TEN_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


def _bay_gio():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cap(db, ten: str, so_ngay: int = SO_NGAY_MAC_DINH) -> tuple:
    """Cấp token mới cho worker 'ten'. Trả (token đầy đủ, dòng WorkerToken); người gọi commit.

    Token đầy đủ chỉ tồn tại trong giá trị trả về này — database chỉ giữ SHA-256 của bí mật.
    """
    ten = (ten or "").strip()
    if not _TEN_HOP_LE.match(ten):
        raise ValueError("Tên worker chỉ gồm chữ thường, số, '-' hoặc '_' (1-50 ký tự), vd. gpu-01.")
    if not 1 <= so_ngay <= SO_NGAY_TOI_DA:
        raise ValueError(f"Hạn dùng phải từ 1 đến {SO_NGAY_TOI_DA} ngày.")
    bi_mat = security.sinh_bi_mat_worker()
    dong = WorkerToken(ten=ten, token_hash=security.bam_bi_mat_worker(bi_mat),
                       expires_at=_bay_gio() + timedelta(days=so_ngay))
    db.add(dong)
    db.flush()
    kiem_toan.ghi(db, kiem_toan.VAN_HANH, kiem_toan.CAP_TOKEN_WORKER, "worker_token", dong.id,
                  chi_tiet=f"{ten}/{so_ngay}d")
    return security.ghep_token_worker(dong.id, bi_mat), dong


def thu_hoi(db, token_id: int) -> bool:
    """Thu hồi một token. False nếu không có hoặc đã thu hồi từ trước; người gọi commit."""
    dong = db.get(WorkerToken, token_id)
    if dong is None or dong.revoked_at is not None:
        return False
    dong.revoked_at = _bay_gio()
    kiem_toan.ghi(db, kiem_toan.VAN_HANH, kiem_toan.THU_HOI_TOKEN_WORKER, "worker_token", dong.id,
                  chi_tiet=dong.ten)
    return True


def liet_ke(db) -> list:
    """Mọi token, mới trước, kèm trạng thái. Không bao giờ có bí mật."""
    bay_gio = _bay_gio()
    ket_qua = []
    for d in db.query(WorkerToken).order_by(WorkerToken.id.desc()).all():
        if d.revoked_at is not None:
            trang_thai = "da_thu_hoi"
        elif d.expires_at <= bay_gio:
            trang_thai = "het_han"
        else:
            trang_thai = "hieu_luc"
        ket_qua.append({"id": d.id, "ten": d.ten, "het_han": d.expires_at.strftime("%Y-%m-%d %H:%M"),
                        "trang_thai": trang_thai})
    return ket_qua
