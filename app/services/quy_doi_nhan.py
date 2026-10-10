"""Quy đổi đầu ra của mô hình ảnh (7 loại) sang nhóm BITSS (1-4) mà backend lưu.

Mô hình của phần mô hình ảnh (repo bitss_stool_classification) phân loại ảnh tã vào 7 lớp
Type_1..Type_7, nhãn 0..6, đúng thứ tự 7 ảnh của thang BITSS, mỗi ảnh ứng với một loại của
thang Bristol (BSFS 1-7). Backend, bác sĩ và trợ lý thì làm việc với 4 nhóm BITSS. Module
này là chỗ DUY NHẤT nối hai thang đó.

Bảng quy đổi không viết lại ở đây mà đọc từ bitss.BITSS_CLASSES[*]["bsfs_tuong_duong"]:
nếu glossary đổi (ví dụ thầy thuốc chỉnh lại cách gộp), mọi chỗ dùng đều đổi theo.

Vì sao CỘNG xác suất theo nhóm thay vì lấy loại có xác suất cao nhất rồi mới quy đổi:
câu hỏi backend cần trả lời là "ảnh thuộc nhóm nào", không phải "ảnh thuộc loại nào".
Với đầu ra (0,30; 0,28; 0,02; 0,05; 0,35; 0; 0), loại cao nhất là BSFS 5 (nhóm phân lỏng),
nhưng xác suất nhóm phân cứng là 0,30 + 0,28 + 0,02 = 0,60. Lấy loại cao nhất sẽ báo sai
nhóm với độ tin cậy 0,35; cộng theo nhóm cho đúng nhóm, và độ tin cậy là xác suất của chính
nhóm đó, tức đúng đại lượng mà ngưỡng gắn cờ (uncertainty.py) cần.
"""

import math
from typing import Sequence

from app.services import bitss

SO_LOAI_BSFS = 7
# Tổng xác suất được phép lệch khỏi 1 bao nhiêu (sai số làm tròn của softmax float32).
DUNG_SAI_TONG = 1e-3


def _bang_bsfs_sang_nhom() -> dict:
    """{loại BSFS 1..7: nhóm BITSS}, dựng từ glossary và kiểm phủ đủ, không trùng."""
    bang = {}
    for nhom, mo_ta in bitss.BITSS_CLASSES.items():
        for loai in mo_ta["bsfs_tuong_duong"]:
            if loai in bang:
                raise ValueError(f"BSFS {loai} thuộc hai nhóm BITSS ({bang[loai]} và {nhom})")
            bang[loai] = nhom
    thieu = set(range(1, SO_LOAI_BSFS + 1)) - set(bang)
    if thieu:
        raise ValueError(f"Các loại BSFS {sorted(thieu)} không thuộc nhóm BITSS nào")
    return bang


BSFS_SANG_NHOM = _bang_bsfs_sang_nhom()


def nhom_cua_nhan_mo_hinh(nhan: int) -> int:
    """Nhãn 0..6 của mô hình (Type_1..Type_7) -> nhóm BITSS 1..4."""
    if not isinstance(nhan, int) or isinstance(nhan, bool) or not 0 <= nhan < SO_LOAI_BSFS:
        raise ValueError(f"Nhãn mô hình phải là số nguyên 0..{SO_LOAI_BSFS - 1}, nhận {nhan!r}")
    return BSFS_SANG_NHOM[nhan + 1]


def gop_xac_suat(xac_suat_7_loai: Sequence[float]) -> dict:
    """Gộp vectơ xác suất 7 loại thành xác suất 4 nhóm và chọn nhóm.

    Trả về {"nhom": 1..4, "do_tin_cay": xác suất của nhóm đó, "xac_suat_nhom": {1..4: p}}.
    Khi hai nhóm bằng nhau, chọn nhóm có số nhỏ hơn để kết quả tất định; ca hoà như vậy có
    độ tin cậy tối đa 0,5 nên luôn bị gắn cờ cho bác sĩ xem.

    Từ chối (ValueError) mọi đầu vào không phải một phân phối xác suất: sai độ dài, giá trị
    âm, NaN, vô cực, hoặc tổng lệch khỏi 1 quá dung sai. Không tự chuẩn hoá lại: một vectơ
    tổng 0,7 thường là dấu hiệu lấy nhầm logit hay nhầm lớp, và ghi nó vào hồ sơ như thể
    là xác suất thì bác sĩ sẽ thấy một độ tin cậy không có nghĩa.
    """
    p = list(xac_suat_7_loai)
    if len(p) != SO_LOAI_BSFS:
        raise ValueError(f"Cần đúng {SO_LOAI_BSFS} xác suất (Type_1..Type_7), nhận {len(p)}")
    for gia_tri in p:
        if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)) or not math.isfinite(gia_tri):
            raise ValueError(f"Xác suất phải là số hữu hạn, nhận {gia_tri!r}")
        if gia_tri < 0:
            raise ValueError(f"Xác suất không được âm, nhận {gia_tri!r}")
    tong = sum(p)
    if abs(tong - 1.0) > DUNG_SAI_TONG:
        raise ValueError(f"Tổng xác suất phải bằng 1 (sai số {DUNG_SAI_TONG}), nhận {tong:.6f}")

    xac_suat_nhom = {nhom: 0.0 for nhom in sorted(bitss.BITSS_CLASSES)}
    for i, gia_tri in enumerate(p):
        xac_suat_nhom[BSFS_SANG_NHOM[i + 1]] += gia_tri
    nhom = max(xac_suat_nhom, key=lambda k: (xac_suat_nhom[k], -k))
    # Kẹp vào [0, 1]: tổng được phép lệch khỏi 1 một chút, nhưng endpoint ghi kết quả từ
    # chối độ tin cậy > 1.
    do_tin_cay = min(1.0, max(0.0, xac_suat_nhom[nhom]))
    return {"nhom": nhom, "do_tin_cay": do_tin_cay, "xac_suat_nhom": xac_suat_nhom}
