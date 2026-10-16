"""Kiểm thử đột biến cho bộ kiểm tra tests/verify_task.py.

Chạy:  uv run python danh_gia/kiem_thu_dot_bien.py          (mất vài phút, chạy song song 4 tiến trình)

Mỗi đột biến là một lỗi nhỏ cố ý chèn vào MỘT BẢN SAO của mã (thư mục tạm, xoá khi xong); repo không bị
sửa. Với mỗi đột biến, script chạy lại toàn bộ bộ kiểm tra trên bản sao. Đột biến "bị bắt" khi nó làm trượt
thêm ít nhất một kiểm tra so với bản sao chưa đột biến, hoặc làm bộ kiểm tra dừng giữa chừng. Không gọi Gemini
(bộ kiểm tra tự đặt khoá API rỗng), không đụng data/ thật.

Danh sách gồm 50 đột biến: D01–D19 dựng ngày 2026-10-01, rải trên các thành phần chính, gồm bốn đột biến nhắm
thẳng vào bất biến B1–B4; D20–D25 dựng ngày 2026-10-05, mỗi đột biến đảo ngược một bản sửa lỗi sau đợt đo;
D26 dựng ngày 2026-10-08 cho bản sửa ghi chú JPEG còn sót sau bước làm sạch ảnh; D27–D30 dựng ngày 2026-10-10
cho phần nối mô hình ảnh (quy đổi nhãn 7 loại sang 4 nhóm, hàng chờ và ảnh cho worker); D31–D34 dựng ngày
2026-10-16 cho đợt tăng cường bảo mật 1 (log nhánh lỗi của chat, tắt tài liệu API, header bảo mật, timeout Gemini);
D35–D43 dựng cùng ngày, mỗi đột biến gỡ một chốt phân quyền theo đối tượng mà ma trận route × vai (mục 43) phải bắt;
D44–D50 dựng cùng ngày cho nhật ký kiểm toán (mục 44): bỏ ghi ở từng loại sự kiện, bỏ trigger chỉ-ghi-thêm, trợ lý
vẫn trả dữ liệu khi không ghi được nhật ký, không ghi IP.
D07 và D12 là đột biến tương đương (không đổi hành vi quan sát được), nên không kiểm tra chức năng nào bắt
được chúng; chúng được giữ lại để thấy giới hạn của phương pháp.
"""
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GOC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHEP = ["app", "tests", "scripts", "danh_gia", "main.py", "setup_database.py", "pyproject.toml"]

# (nhãn, tệp, đoạn gốc, đoạn thay)
DOT_BIEN = [
    ("D01 máy trạng thái: bỏ kiểm tra chuyển hợp lệ", "app/api/records.py",
     "    if target not in allowed:\n", "    if False and target not in allowed:\n"),
    ("D02 ai_confidence: cận trên 1,0 thành 2,0", "app/api/records.py",
     "if not (0.0 <= payload.ai_confidence <= 1.0):", "if not (0.0 <= payload.ai_confidence <= 2.0):"),
    ("D03 ngưỡng độ tự tin: < thành <=", "app/services/uncertainty.py",
     "    return confidence < CONFIDENCE_THRESHOLD", "    return confidence <= CONFIDENCE_THRESHOLD"),
    ("D04 cửa sổ trượt: t > mốc cũ thành >=", "app/services/rate_limit.py",
     "if t > moc_cu]", "if t >= moc_cu]"),
    ("D05 cửa sổ trượt: >= hạn mức thành >", "app/services/rate_limit.py",
     "if len(moc) >= han_muc:", "if len(moc) > han_muc:"),
    ("D06 cửa sổ trượt: ghi cả lượt bị từ chối", "app/services/rate_limit.py",
     "            cho = moc[0] + cua_so - bay_gio", "            moc.append(bay_gio)\n            cho = moc[0] + cua_so - bay_gio"),
    ("D07 so sánh token: compare_digest thành == (tương đương)", "app/services/security.py",
     "return hmac.compare_digest(presented, expected)", "return presented == expected"),
    ("D08 thử lại mọi ClientError", "app/services/agent_service.py",
     "        return exc.code == 429", "        return True"),
    ("D09 giờ mở lại hạn mức ngày: +1 ngày thành +2", "app/services/agent_service.py",
     "(dia_phuong + timedelta(days=1))", "(dia_phuong + timedelta(days=2))"),
    ("D10 bỏ dấu: không đổi đ thành d", "app/services/agent_service.py",
     'unicodedata.category(c) != "Mn").replace("đ", "d")', 'unicodedata.category(c) != "Mn")'),
    ("D11 mã ẩn danh: 20 ký tự thành 10", "app/services/xuat_du_lieu.py",
     ".hexdigest()[:20]", ".hexdigest()[:10]"),
    ("D12 authz: bỏ nhánh child_id None (tương đương)", "app/services/authz.py",
     "        if record.child_id is None:\n            return False\n", "        if False:\n            return False\n"),
    ("D13 xu hướng: mức ý nghĩa 0,05 thành 0,5", "app/services/records_query.py",
     "MUC_Y_NGHIA = 0.05", "MUC_Y_NGHIA = 0.5"),
    ("D14 dọn sổ đếm: cửa sổ dài nhất thành ngắn nhất", "app/services/rate_limit.py",
     "cua_so_dai_nhat = max((c for _, _, c in HAN_MUC.values()), default=CUA_SO_MAC_DINH)",
     "cua_so_dai_nhat = min((c for _, _, c in HAN_MUC.values()), default=CUA_SO_MAC_DINH)"),
    ("D15 token dịch vụ: bỏ kiểm 'không có token'", "app/services/security.py",
     "    if not presented:\n        return False\n    return hmac.compare_digest", "    return hmac.compare_digest"),
    ("D16 B1: phụ huynh xem được hồ sơ mọi bé", "app/services/authz.py",
     "return child.owner_user_id == principal.id", "return True"),
    ("D17 B2: bộ lọc nhãn không căn cứ luôn rỗng", "app/services/agent_service.py",
     "    return nhan_bitss_trong(cau_tra_loi) - set(nhan_da_thay)", "    return set()"),
    ("D18 B3: bộ dò phán quyết phân luồng luôn rỗng", "app/services/agent_service.py",
     "    return _chua_cum_nao(cau_tra_loi, CUM_TU_PHAN_LUONG)", "    return set()"),
    ("D19 B4: lưới an toàn không thêm lời dặn đi khám", "app/services/agent_service.py",
     "    return cau_tra_loi + _dung_luoi_an_toan(khop)", "    return cau_tra_loi"),
    ("D20 khớp từ khoá: bỏ chuẩn hoá NFC", "app/services/agent_service.py",
     'thap = unicodedata.normalize("NFC", tin_nhan or "").lower()', 'thap = (tin_nhan or "").lower()'),
    ("D21 xu hướng: tối thiểu 7 ca thành 4", "app/services/records_query.py",
     "TOI_THIEU_CA_CO_NHAN = 7", "TOI_THIEU_CA_CO_NHAN = 4"),
    ("D22 ghi kết quả suy luận: bỏ điều kiện trạng thái trong UPDATE", "app/api/records.py",
     ".filter(StoolRecord.id == record.id, StoolRecord.inference_status == da_doc)",
     ".filter(StoolRecord.id == record.id)"),
    ("D23 chế độ ăn: bỏ kiểm từ vựng", "app/api/children.py",
     "    if che_do_an is None:\n", "    if False:\n"),
    ("D24 Gemini lỗi: không kèm cảnh báo dấu hiệu nguy hiểm", "app/api/chat.py",
     "return thong_bao + agent_service._dung_luoi_an_toan(khop) if khop else thong_bao", "return thong_bao"),
    ("D25 điều phối công cụ: bỏ kiểm chữ ký trước khi gọi", "app/services/agent_service.py",
     "        inspect.signature(ham).bind(**args)\n", "        pass\n"),
    ("D26 làm sạch ảnh: không bỏ info (ghi chú JPEG còn sót)", "app/services/lam_sach_anh.py",
     "    anh.info = {}\n", "    pass\n"),
    ("D27 quy đổi nhãn: lấy loại cao nhất thay vì cộng theo nhóm", "app/services/quy_doi_nhan.py",
     "        xac_suat_nhom[BSFS_SANG_NHOM[i + 1]] += gia_tri\n",
     "        xac_suat_nhom[BSFS_SANG_NHOM[i + 1]] = max(xac_suat_nhom[BSFS_SANG_NHOM[i + 1]], gia_tri)\n"),
    ("D28 quy đổi nhãn: bỏ kiểm tổng xác suất bằng 1", "app/services/quy_doi_nhan.py",
     "    if abs(tong - 1.0) > DUNG_SAI_TONG:\n", "    if False:\n"),
    ("D29 hàng chờ worker: không loại ca của bé đã xoá mềm", "app/api/records.py",
     '.filter(StoolRecord.inference_status == "queued", Child.deleted_at.is_(None))',
     '.filter(StoolRecord.inference_status == "queued")'),
    ("D30 ảnh cho worker: bỏ giới hạn trạng thái queued/processing", "app/api/records.py",
     "        if record.inference_status not in TRANG_THAI_WORKER_DOC_ANH:\n", "        if False:\n"),
    ("D31 chat lỗi không lường trước: hạ log exception xuống debug", "app/api/chat.py",
     'logging.getLogger("bitss.chat").exception(\n            "[%s] loi khong luong',
     'logging.getLogger("bitss.chat").debug(\n            "[%s] loi khong luong'),
    ("D32 tài liệu API: thiếu cấu hình thì bật", "main.py",
     '_GIA_TRI_BAT = {"1", "true", "yes", "on"}', '_GIA_TRI_BAT = {"", "1", "true", "yes", "on"}'),
    ("D33 header bảo mật: bỏ Cache-Control no-store cho /api/", "main.py",
     '    "Cache-Control": "no-store",\n', ""),
    ("D34 Gemini: gọi không kèm timeout", "app/services/agent_service.py",
     "config=_gan_timeout(config, han_chot)", "config=config"),
    ("D35 đồng thuận nghiên cứu: bỏ kiểm chủ hồ sơ", "app/api/records.py",
     "if not child or da_xoa_mem(child) or child.owner_user_id != principal.id:",
     "if not child or da_xoa_mem(child):"),
    ("D36 xoá hồ sơ bé: bỏ kiểm chủ hồ sơ", "app/api/children.py",
     "if not child or child.owner_user_id != user.id or da_xoa_mem(child):", "if not child or da_xoa_mem(child):"),
    ("D37 tải ảnh: bỏ kiểm chủ hồ sơ", "app/api/records.py",
     "        if child.owner_user_id != principal.id:\n", "        if False:\n"),
    ("D38 duyệt ca: không đòi vai bác sĩ", "app/api/doctor.py",
     "Depends(require_doctor)", "Depends(get_current_user)"),
    ("D39 danh sách duyệt của ca: bỏ kiểm quyền xem", "app/api/doctor.py",
     '{record_id}")\n\n        assert_can_view_record(db, record, principal)\n', '{record_id}")\n\n'),
    ("D40 ảnh của ca: bỏ kiểm quyền xem", "app/api/records.py",
     '{record_id}")\n        assert_can_view_record(db, record, principal)\n', '{record_id}")\n'),
    ("D41 danh sách bé: bỏ lọc theo chủ hồ sơ", "app/api/children.py",
     ".filter(Child.owner_user_id == user.id, Child.deleted_at.is_(None))", ".filter(Child.deleted_at.is_(None))"),
    ("D42 phạm vi danh sách ca: phụ huynh thấy bé của mọi nhà", "app/services/authz.py",
     ".filter(Child.owner_user_id == principal.id, Child.deleted_at.is_(None))", ".filter(Child.deleted_at.is_(None))"),
    ("D43 không được xem ca: 404 thành 403 (lộ ca có tồn tại)", "app/api/deps.py",
     '            status_code=404,\n            detail=f"Không tìm thấy ca phân tích có ID: {record.id}",',
     '            status_code=403,\n            detail=f"Không tìm thấy ca phân tích có ID: {record.id}",'),
    ("D44 nhật ký: xem ca không ghi", "app/api/records.py",
     '        kiem_toan.ghi(db, principal, kiem_toan.XEM_CA, "record", record.id)\n', ""),
    ("D45 nhật ký: chốt nhãn không ghi", "app/api/doctor.py",
     '            kiem_toan.ghi(db, doctor, kiem_toan.DUYET_CA, "record", record.id, chi_tiet=payload.decision)\n', ""),
    ("D46 nhật ký: lượt bị từ chối không ghi", "app/api/deps.py",
     '        kiem_toan.ghi_rieng(principal, kiem_toan.TRUY_CAP_BI_TU_CHOI, "record", record.id,\n'
     '                            ket_qua=kiem_toan.BI_TU_CHOI)\n', ""),
    ("D47 nhật ký: không tạo trigger chỉ-ghi-thêm", "setup_database.py",
     '    if connection.dialect.name == "sqlite":\n        for cau_lenh in TRIGGER_AUDIT_CHI_GHI_THEM:',
     '    if False:\n        for cau_lenh in TRIGGER_AUDIT_CHI_GHI_THEM:'),
    ("D48 nhật ký: trợ lý ghi hỏng vẫn trả kết quả", "app/services/agent_service.py",
     '        logger.exception("[%s] khong ghi duoc nhat ky kiem toan cho %s - khong tra ket qua", nhan, ten_cong_cu)\n'
     '        return {"error": f"Không thực hiện được \'{ten_cong_cu}\' lúc này."}\n', "        pass\n"),
    ("D49 nhật ký: bước chuyển của worker không ghi", "app/api/records.py",
     '    kiem_toan.ghi(db, kiem_toan.WORKER, _HANH_DONG_THEO_DICH[target], "record", record.id,\n'
     '                  chi_tiet=f"{da_doc}->{target}")\n', ""),
    ("D50 nhật ký: không ghi IP", "app/services/nhat_ky_kiem_toan.py",
     "ip=_ip_hien_tai.get(),", "ip=None,"),
]


def ban_sao(dich):
    os.makedirs(dich)
    for x in CHEP:
        nguon = os.path.join(GOC, x)
        if os.path.isdir(nguon):
            shutil.copytree(nguon, os.path.join(dich, x), ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(nguon, dich)
    os.makedirs(os.path.join(dich, "data"))


def chay_kiem_tra(thu_muc):
    p = subprocess.run([sys.executable, "-B", os.path.join(thu_muc, "tests", "verify_task.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                       cwd=thu_muc, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    truot = re.findall(r"^\[FAIL\] (\S+)\)", p.stdout, re.M)
    tong = re.search(r"^(\d+)/(\d+) PASS", p.stdout, re.M)
    return truot, (tong.group(0) if tong else "khong co dong tong ket")


def mot_dot_bien(args):
    i, (nhan, tep, goc, moi), thu_muc_tam = args
    d = os.path.join(thu_muc_tam, f"db{i:02d}")
    ban_sao(d)
    duong = os.path.join(d, tep)
    with open(duong, encoding="utf-8") as f:
        ma = f.read()
    if ma.count(goc) != 1:
        return nhan, None, f"đoạn gốc xuất hiện {ma.count(goc)} lần (mã đã đổi, cần cập nhật đột biến)"
    with open(duong, "w", encoding="utf-8", newline="") as f:
        f.write(ma.replace(goc, moi))
    return nhan, chay_kiem_tra(d), None


def main():
    thu_muc_tam = tempfile.mkdtemp(prefix="kiem_thu_dot_bien_")
    try:
        ban_sao(os.path.join(thu_muc_tam, "goc"))
        truot_goc, tong_goc = chay_kiem_tra(os.path.join(thu_muc_tam, "goc"))
        print(f"Bản sao chưa đột biến: {tong_goc}" + (f" (trượt sẵn: {truot_goc})" if truot_goc else ""))
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            ket_qua = list(ex.map(mot_dot_bien, [(i + 1, d, thu_muc_tam) for i, d in enumerate(DOT_BIEN)]))
        bi_bat = 0
        for nhan, r, loi in ket_qua:
            if loi:
                print(f"LỖI  {nhan}: {loi}")
                continue
            truot, tong = r
            moi = [t for t in truot if t not in truot_goc]
            bat = bool(moi) or "khong co" in tong
            bi_bat += bat
            print(f"{'BẮT ' if bat else 'SỐNG'} {nhan[:56]:56} | {tong:28} | {', '.join(moi[:6])}")
        print(f"\nTổng: {bi_bat}/{len(DOT_BIEN)} đột biến bị bắt (D07, D12 là đột biến tương đương).")
    finally:
        shutil.rmtree(thu_muc_tam, ignore_errors=True)


if __name__ == "__main__":
    main()
