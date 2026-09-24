"""Đánh giá lớp phát hiện dấu hiệu nguy hiểm trong tin nhắn phụ huynh (lưới an toàn).

Chạy:  .venv/Scripts/python.exe danh_gia/danh_gia_luoi_canh_bao.py

So sánh ba chiến lược khớp từ khoá trên CÙNG một tập câu có gán nhãn:

  1. nguyen_van   Khớp nguyên văn, có dấu (baseline: cách làm đơn giản nhất).
  2. bo_dau_het   Bỏ dấu cả tin nhắn lẫn MỌI từ khoá rồi khớp.
  3. hien_tai     Cách hệ thống đang dùng (agent_service._khop_tu_khoa): tin nhắn có dấu thì
                  khớp chính xác; tin nhắn không dấu thì chỉ khớp CỤM NHIỀU TỪ đã bỏ dấu.

Nhãn đúng (ground truth) của một câu = 1 khi câu KHẲNG ĐỊNH bé đang có ít nhất một dấu hiệu
nằm trong danh sách chuẩn bitss.RED_FLAGS, theo đúng điều kiện của dấu hiệu đó (ví dụ sốt
chỉ tính khi bé dưới 3 tháng tuổi). Câu phủ định ("không có máu") có nhãn 0.

GIỚI HẠN CẦN NÓI RÕ KHI TRÍCH SỐ LIỆU TỪ ĐÂY:
  - Tập câu do chính người phát triển tự xây dựng, SAU khi đã thiết kế luật khớp. Kết quả
    vì vậy là ước lượng lạc quan cho chiến lược 3, không phải hiệu năng trên tin nhắn thật.
  - Tập nhỏ (vài chục câu), không có tin nhắn của phụ huynh thật.
  - Các câu dễ gây nhầm được CỐ Ý đưa vào nhiều hơn tỉ lệ tự nhiên, để làm lộ điểm yếu.
"""
import json
import os
import sys

GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GOC)
os.environ.setdefault("GEMINI_API_KEY", "")

from app.services import agent_service as a  # noqa: E402

# (tin nhắn, nhãn đúng, ghi chú ngắn cho phần phân tích lỗi)
TAP_CAU = [
    # ---- Dương tính, CÓ dấu
    ("Bé nhà tôi 2 tháng, phân có lẫn máu tươi", 1, "máu"),
    ("Phân của bé đen như hắc ín từ sáng", 1, "phân đen"),
    ("Phân bé trắng như đất sét, da hơi vàng", 1, "phân bạc màu"),
    ("Bé nôn ra dịch xanh", 1, "nôn dịch mật"),
    ("Bé bị chướng bụng, khóc thét từng cơn", 1, "chướng bụng"),
    ("Môi bé khô, khóc không ra nước mắt", 1, "mất nước"),
    ("Bé li bì, gọi mãi mới dậy", 1, "li bì"),
    ("Bé 6 tuần tuổi sốt 38,5 độ", 1, "sốt dưới 3 tháng"),
    ("Bé bị tiêu chảy hơn hai tuần rồi", 1, "tiêu chảy kéo dài"),
    ("Bé không tăng cân mấy tháng nay", 1, "chậm tăng cân"),
    ("Bé co giật lúc nãy", 1, "co giật"),
    ("Bé đi ngoài ra máu", 1, "máu"),
    ("Bé tiểu ít, cả buổi chỉ một lần", 1, "mất nước"),
    ("Bé ói liên tục từ tối qua", 1, "nôn liên tục"),
    ("Bụng bé căng cứng", 1, "bụng căng"),
    ("Phân có nhầy lẫn máu", 1, "máu"),
    ("Bé sụt cân so với tháng trước", 1, "sụt cân"),
    ("Bé không đi tiểu từ sáng đến giờ", 1, "mất nước"),
    ("Thóp của bé bị trũng xuống", 1, "mất nước (diễn đạt khác từ khoá)"),
    ("Mắt bé trũng sâu, bé mệt", 1, "mất nước (diễn đạt khác từ khoá)"),
    # ---- Dương tính, KHÔNG dấu
    ("be 2 thang phan co lan mau tuoi", 1, "máu"),
    ("phan cua be den nhu hac in", 1, "phân đen"),
    ("phan be trang nhu dat set", 1, "phân bạc màu"),
    ("be non ra dich xanh", 1, "nôn dịch mật"),
    ("be bi chuong bung", 1, "chướng bụng"),
    ("be khoc khong ra nuoc mat", 1, "mất nước"),
    ("be li bi kho danh thuc", 1, "li bì"),
    ("be 6 tuan tuoi sot cao", 1, "sốt dưới 3 tháng"),
    ("be bi sot tu hom qua, be moi 1 thang", 1, "sốt dưới 3 tháng"),
    ("be tieu chay 3 tuan nay", 1, "tiêu chảy kéo dài"),
    ("be co giat", 1, "co giật"),
    ("be sut can", 1, "sụt cân"),
    ("be oi lien tuc", 1, "nôn liên tục"),
    ("be di ngoai ra mau", 1, "máu (chỉ có từ đơn)"),
    ("be bi non", 1, "nôn (chỉ có từ đơn)"),
    ("be sot 39 do, be moi 7 tuan", 1, "sốt (chỉ có từ đơn)"),
    # ---- Âm tính, CÓ dấu
    ("Phân bé màu vàng hoa cà hoa cải", 0, "màu, không phải máu"),
    ("Bé sinh non 2 tuần, giờ ăn ngủ tốt", 0, "sinh non"),
    ("Tôi sốt ruột quá, bé đi ngoài 3 lần", 0, "thành ngữ 'sốt ruột'"),
    ("Bé ăn dặm được một tháng, phân thành khuôn", 0, "bình thường"),
    ("Tôi nôn nóng muốn biết kết quả", 0, "thành ngữ 'nôn nóng'"),
    ("Phân bé không có máu, chỉ hơi lỏng", 0, "phủ định"),
    ("Bé 2 tuổi bị sốt nhẹ", 0, "sốt nhưng không dưới 3 tháng"),
    ("Bé đang bổ sung sắt nên phân sẫm màu", 0, "sắt làm phân sẫm"),
    ("Làm sao để đọc kết quả phân tích?", 0, "câu hỏi chung"),
    ("Phân bé có màu xanh rêu", 0, "màu"),
    ("Bé đi ngoài 5 lần một ngày, phân lỏng", 0, "phân lỏng, chưa là dấu hiệu"),
    ("Bé hay trớ một chút sau bú", 0, "trớ sinh lý"),
    ("Bé khóc nhiều về đêm", 0, "bình thường"),
    ("Tôi muốn hỏi về chế độ ăn dặm", 0, "câu hỏi chung"),
    ("Bé bị táo bón, phân cứng", 0, "không thuộc danh sách"),
    ("Da bé hơi vàng", 0, "vàng da đơn thuần"),
    # ---- Âm tính, KHÔNG dấu
    ("phan be mau vang", 0, "màu, không phải máu"),
    ("be sinh non 2 tuan", 0, "sinh non"),
    ("toi muon hoi ve an dam", 0, "câu hỏi chung"),
    ("phan co mau xanh reu", 0, "màu"),
    ("be an ngon, ngu tot", 0, "bình thường"),
    ("toi sot ruot qua", 0, "thành ngữ 'sốt ruột'"),
    ("be di ngoai 3 lan mot ngay", 0, "bình thường"),
    ("phan be khong co mau", 0, "phủ định"),
    ("cach doc ket qua phan tich", 0, "câu hỏi chung"),
    ("be hay tro sau bu", 0, "trớ sinh lý"),
    ("be bi tao bon", 0, "không thuộc danh sách"),
    ("be 2 tuoi bi sot nhe", 0, "sốt nhưng không dưới 3 tháng"),
    ("noi dung ket qua the nao", 0, "câu hỏi chung"),
    ("chuong trinh tiem chung cho be", 0, "chướng/chương"),
    ("be moi tap an dam, phan mau cam", 0, "màu"),
]

TU_KHOA = [tu for cac_tu in a.TU_KHOA_THEO_DAU_HIEU.values() for tu in cac_tu]


def nguyen_van(tin_nhan: str) -> bool:
    thap = tin_nhan.lower()
    return any(tu in thap for tu in TU_KHOA)


def bo_dau_het(tin_nhan: str) -> bool:
    khong_dau = a._bo_dau(tin_nhan)
    return any(a._bo_dau(tu) in khong_dau for tu in TU_KHOA)


def hien_tai(tin_nhan: str) -> bool:
    return bool(a._khop_tu_khoa(tin_nhan))


CHIEN_LUOC = {"nguyen_van": nguyen_van, "bo_dau_het": bo_dau_het, "hien_tai": hien_tai}


def co_dau(tin_nhan: str) -> bool:
    return tin_nhan.lower() != a._bo_dau(tin_nhan)


def chi_so(du_doan, nhan) -> dict:
    tp = sum(1 for d, n in zip(du_doan, nhan) if d and n)
    fp = sum(1 for d, n in zip(du_doan, nhan) if d and not n)
    fn = sum(1 for d, n in zip(du_doan, nhan) if not d and n)
    tn = sum(1 for d, n in zip(du_doan, nhan) if not d and not n)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r,
            "f1": f1, "accuracy": (tp + tn) / len(nhan)}


def danh_gia() -> dict:
    ket = {"so_cau": len(TAP_CAU), "so_duong_tinh": sum(n for _, n, _ in TAP_CAU),
           "so_co_dau": sum(1 for t, _, _ in TAP_CAU if co_dau(t)), "chien_luoc": {}}
    for ten, ham in CHIEN_LUOC.items():
        muc = {}
        for nhom, loc in (("tat_ca", lambda t: True), ("co_dau", co_dau),
                          ("khong_dau", lambda t: not co_dau(t))):
            tap = [(t, n) for t, n, _ in TAP_CAU if loc(t)]
            muc[nhom] = chi_so([ham(t) for t, _ in tap], [n for _, n in tap])
        muc["loi"] = [
            {"tin_nhan": t, "nhan": n, "du_doan": int(ham(t)), "ghi_chu": g}
            for t, n, g in TAP_CAU if bool(ham(t)) != bool(n)
        ]
        ket["chien_luoc"][ten] = muc
    return ket


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    kq = danh_gia()
    print(f"Tap cau: {kq['so_cau']} cau, {kq['so_duong_tinh']} duong tinh, {kq['so_co_dau']} cau co dau")
    for ten, muc in kq["chien_luoc"].items():
        print(f"\n== {ten}")
        for nhom in ("tat_ca", "co_dau", "khong_dau"):
            m = muc[nhom]
            print(f"  {nhom:9} P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f} "
                  f"Acc={m['accuracy']:.3f}  (TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']})")
        for ca_sai in muc["loi"]:
            loai = "FP" if ca_sai["du_doan"] else "FN"
            print(f"    {loai}: {ca_sai['tin_nhan']}  [{ca_sai['ghi_chu']}]")
    goc_du_an = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    duong_dan = os.path.join(goc_du_an, "outputs", "ket_qua_luoi_canh_bao.json")
    with open(duong_dan, "w", encoding="utf-8") as f:
        json.dump(kq, f, ensure_ascii=False, indent=1)
    print(f"\nDa ghi {duong_dan}")
