# Pediatric Gastro AI Assistant (BITSS) — Backend + Trợ lý hội thoại

Đồ án môn học *Đồ án cơ sở trong khoa học dữ liệu* (HK 261, 2026): backend và trợ lý hội thoại cho hệ thống hỗ trợ theo dõi tiêu hoá ở trẻ nhỏ.
- Phụ huynh tải ảnh phân của bé lên.
- Ảnh được phân loại theo thang **BITSS** (Brussels Infant and Toddler Stool Scale).
- Bác sĩ duyệt và chốt kết luận.
- Trợ lý hội thoại dùng Google Gemini giải thích kết quả, và chỉ dựa trên hồ sơ của chính gia đình đang hỏi.

Mô hình học sâu phân loại ảnh và XAI thuộc repo riêng của thành viên khác trong nhóm.

## Trạng thái hiện tại

- **Backend:** đã hoàn chỉnh. Gồm tài khoản và phân quyền, hồ sơ bé, tiếp nhận và làm sạch ảnh, máy trạng thái suy luận, bác sĩ duyệt, hai loại đồng thuận, thời hạn lưu trữ, xuất dữ liệu huấn luyện.
- **Trợ lý hội thoại:** đã hoàn chỉnh. Bộ công cụ được khoá vào danh tính người hỏi. Đầu ra đi qua 4 lớp bất biến:
  - lọc nhãn BITSS không có căn cứ;
  - chặn phán quyết phân luồng;
  - lưới cảnh báo dấu hiệu nguy hiểm;
  - chuẩn hoá về văn bản thường.
- **Kiểm thử:** 450 kiểm tra tự động, không cần mạng hay khoá API.
- **Chưa nối mô hình phân loại ảnh.** Backend đã có sẵn các endpoint `inference-*` cho worker suy luận, xác thực bằng `X-Service-Token`. Khi mô hình sẵn sàng, worker chỉ cần gọi các endpoint này.
- **Đánh giá trên Gemini thật mới đo được một phần**, do hạn mức ngày của gói miễn phí. Kết quả đã đo nằm trong `outputs/ket_qua_llm/`.
- **Mọi dữ liệu trong repo là dữ liệu kiểm thử tự tạo.** Không có dữ liệu bệnh nhân thật.

## Cấu trúc repo

```
pediatric-gastro-ai-assistant/
├── main.py                  # Điểm vào FastAPI: kiểm schema, CORS, dọn dữ liệu lúc khởi động
├── setup_database.py        # Mô hình dữ liệu (6 bảng) + lệnh khởi tạo / bổ sung cột thiếu
├── app/
│   ├── api/                 # Tầng HTTP: nhận request, kiểm quyền, trả đúng mã lỗi
│   │   ├── auth.py          # Đăng ký, đăng nhập, đăng xuất, đồng thuận
│   │   ├── children.py      # Hồ sơ bé, luôn gắn với người sở hữu
│   │   ├── records.py       # Tải ảnh, xem ca / ảnh, endpoint cho worker suy luận
│   │   ├── doctor.py        # Bác sĩ chốt kết luận lâm sàng
│   │   ├── chat.py          # Điểm vào của trợ lý AI
│   │   └── deps.py          # Xác thực token, hạn mức, cảnh báo proxy
│   └── services/            # Tầng nghiệp vụ, không phụ thuộc HTTP
│       ├── agent_service.py # Vòng lặp gọi công cụ Gemini + các lớp kiểm đầu ra
│       ├── authz.py         # Quy tắc "ai được xem gì" (hàm thuần)
│       ├── bitss.py         # Nguồn chuẩn về thang BITSS và dấu hiệu nguy hiểm
│       ├── chat_history.py  # Lịch sử hội thoại, thời hạn lưu 90 ngày
│       ├── dong_y.py, dong_y_nghien_cuu.py  # Hai loại đồng thuận
│       ├── lam_sach_anh.py  # Xoá EXIF/GPS trước khi ảnh chạm đĩa
│       ├── uncertainty.py   # Gắn cờ ca mô hình không chắc chắn (ngưỡng 0,70)
│       ├── xuat_du_lieu.py  # Chọn ca đủ điều kiện xuất, mã ẩn danh HMAC
│       └── ...              # rate_limit, records_query, security, token_store, yeu_to_lam_sang
├── scripts/                 # Công cụ chạy tay trên máy chủ
│   ├── create_clinician.py  # Đường DUY NHẤT tạo tài khoản bác sĩ
│   ├── don_du_lieu_qua_han.py       # Dọn tin nhắn quá hạn, token hết hạn (cron)
│   └── xuat_du_lieu_huan_luyen.py   # Xuất dữ liệu huấn luyện ẩn danh cho phần mô hình
├── tests/
│   └── verify_task.py       # 450 kiểm tra tự động (38 mục), chạy trên DB và thư mục tạm
├── danh_gia/                # Đánh giá
│   ├── eval_agent.py        # 8 kịch bản trên Gemini thật (tốn hạn mức)
│   └── danh_gia_luoi_canh_bao.py    # Lưới cảnh báo trên 67 câu có nhãn, so với 2 chiến lược khác
├── outputs/
│   ├── ket_qua_luoi_canh_bao.json   # Kết quả đánh giá lưới cảnh báo (tất định)
│   ├── ket_qua_llm/         # Kết quả các lượt chạy eval_agent.py (JSON)
│   └── logs/                # Nhật ký console các lần chạy (không commit)
├── data/                    # Database, ảnh tải lên, dữ liệu xuất (KHÔNG commit, xem .gitignore)
├── .env.example             # Mẫu cấu hình, giải thích từng biến
├── requirements.txt         # Thư viện (xuất từ uv.lock)
├── pyproject.toml, uv.lock
└── README.md
```

## Cài đặt

Yêu cầu Python 3.14. Cài thư viện bằng [uv](https://docs.astral.sh/uv/) (dùng `uv.lock`):

```bash
uv sync
```

Hoặc bằng pip:

```bash
pip install -r requirements.txt
```

Sao chép `.env.example` thành `.env` rồi điền giá trị:

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `JWT_SECRET_KEY` | có | Khoá ký token đăng nhập, tối thiểu 32 ký tự |
| `GEMINI_API_KEY` | không | Để trống thì trợ lý tắt (`POST /api/v1/chat` trả 503); phần còn lại vẫn chạy |
| `INFERENCE_SERVICE_TOKEN` | không | Bí mật dùng chung với worker suy luận. Để trống thì mọi endpoint `inference-*` từ chối |
| `KHOA_MA_AN_DANH_DU_LIEU` | khi xuất dữ liệu | Khoá HMAC tạo mã bệnh nhân ẩn danh; giữ cố định suốt dự án |

## Cách chạy

Mọi lệnh chạy từ thư mục gốc repo. Với pip, bỏ tiền tố `uv run`.

Khởi tạo database (tạo `data/bitss_clinic.db`). Chạy lại mỗi khi cập nhật phiên bản:

```bash
uv run python setup_database.py
```

Tạo tài khoản bác sĩ:

```bash
uv run python scripts/create_clinician.py --email bs@benhvien.vn --name "BS. Nguyễn Văn A"
```

Chạy máy chủ:

```bash
uv run uvicorn main:app --reload
```

Sau đó mở tài liệu API tương tác tại <http://127.0.0.1:8000/docs>. Nếu database chưa khớp phiên bản mã, ứng dụng từ chối khởi động và in ra đúng lệnh cần chạy.

## Kiểm thử

```bash
uv run python tests/verify_task.py
```

Bộ kiểm tra tự dựng database tạm và mô hình giả. Nó không cần mạng, không cần khoá API, và không đụng vào `data/`. Kết quả mong đợi là `450/450 PASS`.

Để kiểm tra tĩnh mã nguồn (pyflakes cùng các lỗi cơ bản của pycodestyle), chạy:

```bash
uvx ruff check
```

## Đánh giá

Đánh giá lớp phát hiện dấu hiệu nguy hiểm. Kết quả tất định, ghi vào `outputs/ket_qua_luoi_canh_bao.json`:

```bash
uv run python danh_gia/danh_gia_luoi_canh_bao.py
```

Chạy 8 kịch bản trên Gemini thật. Lệnh này cần khoá và tốn hạn mức ngày; nên đặt `GEMINI_EVAL_MODEL` khác model phục vụ người dùng:

```bash
uv run python danh_gia/eval_agent.py --model gemini-3.6-flash --ghi-ket-qua outputs/ket_qua_llm/lan1_gemini-3.6-flash.json
```

Mỗi tệp kết quả JSON ghi kèm model, thời điểm chạy, mã băm SHA-256 của hướng dẫn hệ thống và commit mã nguồn lúc chạy. Các commit của những lượt đo hiện có thuộc kho phát triển (lưu trữ riêng), không có trong lịch sử của repo này.

## Khai báo sử dụng AI

AI có hai vai trò khác nhau trong dự án:
- **Thành phần của sản phẩm:** Gemini (Google) là mô hình của trợ lý hội thoại. Cách ràng buộc và đánh giá nó chính là nội dung nghiên cứu.
- **Công cụ phát triển:** mô hình Claude (Anthropic), dùng qua Claude Code. Nó hỗ trợ viết và rà soát mã, viết kiểm tra, và soạn bản nháp báo cáo.

Nhóm tự xác định yêu cầu và chọn phương án. Mọi kết quả được kiểm chứng bằng kiểm tra tự động và các phép đo chạy lại được. Nhóm chịu trách nhiệm về toàn bộ nội dung. Chi tiết ở phụ lục "Khai báo sử dụng công cụ trí tuệ nhân tạo" của báo cáo môn học (nộp riêng).

## Tác giả

- **Đỗ Kinh Văn** (MSSV 2413917): backend, trợ lý hội thoại, kiểm thử, đánh giá.
- **Nguyễn Gia Tiến**: mô hình phân loại ảnh theo BITSS và XAI (repo riêng).

Giảng viên hướng dẫn: Lê Xuân Đại.
