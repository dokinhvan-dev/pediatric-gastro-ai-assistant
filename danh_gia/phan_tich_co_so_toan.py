"""Tính lại mọi con số của phần cơ sở toán học (Chương 2) và của thống kê ở Mục 5.1.3 trong báo cáo.

Chạy:  uv run python danh_gia/phan_tich_co_so_toan.py
Ghi:   outputs/ket_qua_co_so_toan.json

Không gọi Gemini, không cần khoá API, kết quả tất định (hạt giống ngẫu nhiên cố định). Script dùng
HÀM THẬT của hệ thống (agent_service._bo_dau, _khop_tu_khoa, bộ từ khoá) và tập 67 câu của
danh_gia_luoi_canh_bao.py, nên nếu mã thay đổi thì con số in ra thay đổi theo.

Các phần:
  A. Tiền đề của Bổ đề σ và Mệnh đề đơn điệu: |K|, điều kiện (C), |σ(c)| trên các khối Latin,
     σ(uv) = σ(u)σ(v) trên 20.000 cặp ngẫu nhiên, thứ tự nv ≤ lg ≤ bd trên từng câu.
  A2. Tin nhắn dạng NFD (Unicode tổ hợp): số từ khoá khớp ở dạng NFC và NFD của cùng một câu.
  B. Khoảng Wilson, bootstrap phân vị cho F1 (10.000 lần), kiểm định McNemar chính xác.
  C. Xác suất một trẻ có ảnh ở cả tập huấn luyện lẫn kiểm tra khi chia theo ảnh (số liệu Ludwig 2021).
  D. Báo động giả / khả năng phát hiện của quy tắc xu hướng và của permutation test, tính CHÍNH XÁC
     bằng duyệt toàn bộ 4^n dãy nhãn, n = 4..10 (Bảng 2.2). Phần này chạy lâu nhất (vài phút).
  E. Cận trùng mã HMAC; F. mô hình thử lại Bernoulli.
"""
import itertools
import json
import math
import os
import random
import sys
import unicodedata
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GOC)
sys.path.insert(0, os.path.join(GOC, "danh_gia"))
os.environ.setdefault("JWT_SECRET_KEY", "phan-tich-khong-dung-that-" + "0" * 32)

from app.services import agent_service as a  # noqa: E402
import danh_gia_luoi_canh_bao as dg  # noqa: E402

KQ = {}
sigma = a._bo_dau

# ================= A. Tiền đề của mệnh đề đơn điệu =================
K = [k for ks in a.TU_KHOA_THEO_DAU_HIEU.values() for k in ks]
K1 = [k for k in K if " " not in k]
assert all(k == k.lower() for k in K), "từ khoá phải viết thường"
dieu_kien_C = all(sigma(k) != k for k in K1)
print(f"|K| = {len(K)}, |K>=2| = {len(K) - len(K1)}, từ khoá đơn: {K1}; điều kiện (C): {dieu_kien_C}")

khoi = list(range(0x20, 0x250)) + list(range(0x1E00, 0x1F00))
in_duoc = [chr(c) for c in khoi if chr(c).isprintable()]
dai = Counter(len(sigma(c)) for c in in_duoc)
print(f"|σ(c)| trên {len(in_duoc)} ký tự in được của các khối Latin: {dict(dai)}")
chu_viet = [c for c in in_duoc if c.isalpha() and len(sigma(c)) == 1] + [" ", ",", "."]
random.seed(7)
ok_dong_cau = all(
    sigma(u + v) == sigma(u) + sigma(v)
    for u, v in (("".join(random.choices(chu_viet, k=random.randint(0, 12))),
                  "".join(random.choices(chu_viet, k=random.randint(0, 12)))) for _ in range(20000)))
print("σ(uv) = σ(u)σ(v) trên 20.000 cặp ngẫu nhiên:", ok_dong_cau)

du_doan = {ten: [int(f(c)) for c, _, _ in dg.TAP_CAU] for ten, f in dg.CHIEN_LUOC.items()}
nhan = [n for _, n, _ in dg.TAP_CAU]
don_dieu = all(x <= y <= z for x, y, z in zip(du_doan["nguyen_van"], du_doan["hien_tai"], du_doan["bo_dau_het"]))
print(f"thứ tự nv ≤ lg ≤ bd đúng trên từng câu của {len(nhan)} câu:", don_dieu)
KQ["tien_de"] = {"K": len(K), "K_nhieu_tu": len(K) - len(K1), "K_don": K1, "dieu_kien_C": dieu_kien_C,
                 "so_ky_tu_kiem": len(in_duoc), "phan_bo_do_dai_sigma": dict(dai),
                 "dong_cau_20000_cap": ok_dong_cau, "don_dieu_tung_cau": don_dieu}

# ================= A2. Tin nhắn dạng NFD =================
CAU_NFD = "Bé 3 tháng đi phân có lẫn máu tươi"
khop = {dang: sum(len(v) for v in a._khop_tu_khoa(unicodedata.normalize(dang, CAU_NFD)).values())
        for dang in ("NFC", "NFD")}
print(f"'{CAU_NFD}': số từ khoá khớp ở dạng NFC = {khop['NFC']}, dạng NFD = {khop['NFD']}")
KQ["nfd"] = {"cau": CAU_NFD, "khop_nfc": khop["NFC"], "khop_nfd": khop["NFD"]}


# ================= B. Thống kê cho lớp phát hiện =================
def wilson(k, n, z=1.959964):
    ph = k / n
    tam = (ph + z * z / (2 * n)) / (1 + z * z / n)
    nua = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(tam - nua, 3), round(tam + nua, 3)


def dem(dd, yy):
    tp = sum(1 for p, y in zip(dd, yy) if p and y)
    fp = sum(1 for p, y in zip(dd, yy) if p and not y)
    fn = sum(1 for p, y in zip(dd, yy) if not p and y)
    tn = sum(1 for p, y in zip(dd, yy) if not p and not y)
    return tp, fp, fn, tn


def f1(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


random.seed(2026)
B = 10000
chi_so = list(range(len(nhan)))
KQ["luoi"] = {}
for ten, dd in du_doan.items():
    tp, fp, fn, tn = dem(dd, nhan)
    boot = []
    for _ in range(B):
        mau = [random.choice(chi_so) for _ in chi_so]
        boot.append(f1(*dem([dd[i] for i in mau], [nhan[i] for i in mau])[:3]))
    boot.sort()
    muc = {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "recall": (round(tp / (tp + fn), 3), wilson(tp, tp + fn)),
           "precision": (round(tp / (tp + fp), 3), wilson(tp, tp + fp)),
           "specificity": (round(tn / (tn + fp), 3), wilson(tn, tn + fp)),
           "f1": (round(f1(tp, fp, fn), 3), (round(boot[int(0.025 * B)], 3), round(boot[int(0.975 * B) - 1], 3)))}
    KQ["luoi"][ten] = muc
    print(ten, muc)


def mcnemar(d_a, d_b):
    sai_a = {i for i, (p, y) in enumerate(zip(d_a, nhan)) if p != y}
    sai_b = {i for i, (p, y) in enumerate(zip(d_b, nhan)) if p != y}
    b01, b10 = len(sai_a - sai_b), len(sai_b - sai_a)
    n, k = b01 + b10, min(b01, b10)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    return b01, b10, p


KQ["mcnemar"] = {}
for goc in ("nguyen_van", "bo_dau_het"):
    b01, b10, p = mcnemar(du_doan[goc], du_doan["hien_tai"])
    KQ["mcnemar"][goc] = {"goc_sai_lai_ghep_dung": b01, "lai_ghep_sai_goc_dung": b10, "p": round(p, 5)}
    print(f"McNemar {goc} và lai ghép: {b01} / {b10}, p = {p:.5f}")

# ================= C. Rò rỉ khi chia theo ảnh (Ludwig 2021: 2.687 ảnh, 96 trẻ, 209 ảnh kiểm tra) =================
q = 209 / 2687
g = lambda m: 1 - (1 - q) ** m - q ** m  # noqa: E731
KQ["ro_ri"] = {"q": round(q, 4), "m_tb": round(2687 / 96, 2), "g_m_tb": round(g(2687 / 96), 3),
               "ky_vong_tren": round(96 * g(2687 / 96), 1),
               "m_nho_nhat_co_g_tren_nua": next(m for m in range(1, 100) if g(m) >= 0.5),
               "bang": {m: round(g(m), 3) for m in (1, 2, 5, 9, 10, 20, 28, 50)}}
print("rò rỉ:", KQ["ro_ri"])

# ================= D. Xu hướng: báo động giả, làm tròn, permutation test =================
P_DEU = [0.25] * 4
P_LECH = [0.10, 0.60, 0.20, 0.10]
P_CU, P_MOI = [0.10, 0.60, 0.20, 0.10], [0.05, 0.25, 0.45, 0.25]   # thay đổi thật: lỏng hơn
ALPHA = 0.05
KQ["xu_huong"] = {}
for n in range(4, 11):
    h = n // 2
    cache = {}
    bd_deu = bd_lech = kd_deu = kd_lech = phat_hien_quy_tac = phat_hien_kd = 0.0
    lech_lam_tron = 0
    p_min = 1.0
    for day in itertools.product(range(1, 5), repeat=n):
        s_cu, t = sum(day[:h]), sum(day)
        s_moi = t - s_cu
        # quy tắc hiện tại, số nguyên chính xác: |Δ| >= 1/2  <=>  2|h*S_moi - (n-h)*S_cu| >= h(n-h)
        quy_tac = 2 * abs(h * s_moi - (n - h) * s_cu) >= h * (n - h)
        tb_cu, tb_moi = round(s_cu / h, 2), round(s_moi / (n - h), 2)
        if (abs(round(tb_moi - tb_cu, 2)) >= 0.5) != quy_tac:
            lech_lam_tron += 1
        # permutation test: phân phối của |h*T - n*S| trên mọi tập con h vị trí của cùng đa tập
        khoa = tuple(sorted(day))
        if khoa not in cache:
            cache[khoa] = sorted(abs(h * t - n * sum(c)) for c in itertools.combinations(khoa, h))
        ds = cache[khoa]
        d_obs = abs(h * t - n * s_cu)
        p = sum(1 for d in ds if d >= d_obs) / len(ds)
        p_min = min(p, p_min)
        dem_c, dem_cu, dem_moi = Counter(day), Counter(day[:h]), Counter(day[h:])
        w_deu = math.prod(P_DEU[c - 1] ** dem_c[c] for c in dem_c)
        w_lech = math.prod(P_LECH[c - 1] ** dem_c[c] for c in dem_c)
        w_alt = (math.prod(P_CU[c - 1] ** dem_cu[c] for c in dem_cu)
                 * math.prod(P_MOI[c - 1] ** dem_moi[c] for c in dem_moi))
        bd_deu += w_deu * quy_tac
        bd_lech += w_lech * quy_tac
        kd_deu += w_deu * (p <= ALPHA)
        kd_lech += w_lech * (p <= ALPHA)
        phat_hien_quy_tac += w_alt * (quy_tac and s_moi * h > s_cu * (n - h))
        phat_hien_kd += w_alt * (p <= ALPHA and s_moi * h > s_cu * (n - h))
    KQ["xu_huong"][n] = {"bao_dong_gia_quy_tac_deu": round(bd_deu, 3), "bao_dong_gia_quy_tac_lech": round(bd_lech, 3),
                         "phat_hien_quy_tac": round(phat_hien_quy_tac, 3), "p_min_hoan_vi": round(p_min, 3),
                         "bao_dong_gia_hoan_vi_deu": round(kd_deu, 3), "bao_dong_gia_hoan_vi_lech": round(kd_lech, 3),
                         "phat_hien_hoan_vi": round(phat_hien_kd, 3), "lech_do_lam_tron": lech_lam_tron}
    print(f"n={n}: {KQ['xu_huong'][n]}")

# ================= E. HMAC, F. thử lại =================
KQ["hmac_can_trung_ma"] = {str(n): n * (n - 1) / 2 / 2 ** 80 for n in (10 ** 4, 10 ** 6)}
KQ["thu_lai"] = {str(p): {"thanh_cong": round(1 - p ** 3, 3), "E_N": round((1 - p ** 3) / (1 - p), 3),
                          "thanh_cong_tren_moi_loi_goi": round(1 - p, 3), "luot_thanh_cong_tren_20": round(20 * (1 - p), 1)}
                 for p in (0.1, 0.3, 0.5, 0.8)}
print("cận trùng mã HMAC:", KQ["hmac_can_trung_ma"])
print("thử lại:", KQ["thu_lai"])

ra = os.path.join(GOC, "outputs", "ket_qua_co_so_toan.json")
with open(ra, "w", encoding="utf-8") as f:
    json.dump(KQ, f, ensure_ascii=False, indent=1)
print("Đã ghi", os.path.relpath(ra, GOC))
