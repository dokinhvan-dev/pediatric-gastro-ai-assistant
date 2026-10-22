# Pediatric Gastro AI Assistant (BITSS) — Backend + Trợ lý hội thoại

Đồ án môn học *Đồ án cơ sở trong khoa học dữ liệu* (HK 261, 2026): backend và trợ lý hội thoại cho hệ thống hỗ trợ theo dõi tiêu hoá ở trẻ nhỏ.
- Phụ huynh tải ảnh phân của bé lên.
- Ảnh được phân loại theo thang **BITSS** (Brussels Infant and Toddler Stool Scale).
- Bác sĩ duyệt và chốt kết luận.
- Trợ lý hội thoại dùng Google Gemini giải thích kết quả, và chỉ dựa trên hồ sơ của chính gia đình đang hỏi.

Mô hình học sâu phân loại ảnh và XAI thuộc repo riêng của thành viên khác trong nhóm.

## Trạng thái hiện tại

Phạm vi nghiệm thu của đồ án là thiết kế phần mềm, kiểm thử và thực nghiệm có thể tái hiện trên dữ liệu tự tạo. Phần chức năng đã triển khai, kết quả đánh giá theo phiên bản và các đầu việc còn chờ được ghi riêng dưới đây.

- **Backend:** đã triển khai các chức năng trong phạm vi hiện tại. Gồm tài khoản và phân quyền, hồ sơ bé, tiếp nhận và làm sạch ảnh, máy trạng thái suy luận, bác sĩ duyệt, hai loại đồng thuận, thời hạn lưu trữ, xuất dữ liệu huấn luyện, nhật ký kiểm toán (ai xem ca và ảnh nào, ai chốt nhãn, ai đồng ý hay rút đồng thuận, ai bị từ chối khi thử hồ sơ của nhà khác; bảng chỉ ghi thêm, chỉ chứa mã định danh).
- **Trợ lý hội thoại:** đã triển khai luồng hội thoại và cơ chế ràng buộc; cần đánh giá lại trên phiên bản mã mới. Bộ công cụ được khoá vào danh tính người hỏi. Đầu ra đi qua 4 lớp bất biến:
  - lọc nhãn BITSS không có căn cứ;
  - chặn phán quyết phân luồng;
  - lưới cảnh báo dấu hiệu nguy hiểm;
  - chuẩn hoá về văn bản thường.
- **Kiểm thử:** 537 kiểm tra tự động, không cần mạng hay khoá API, trong đó có ma trận phân quyền: mọi route của API × bốn vai (phụ huynh chủ hồ sơ, phụ huynh khác, bác sĩ, ẩn danh), kèm kiểm tra route mới thêm mà chưa khai vào ma trận thì trượt. Có thêm 16 bài regression cho cổng đột biến và migration. Workflow Security chạy pip-audit, Ruff có luật S, gitleaks, kiểm thử và đủ 59 đột biến ở mỗi lần push; log/JSON đột biến được lưu thành artifact theo SHA.
- **Đường nối với mô hình phân loại ảnh đã có, chờ checkpoint.** Worker `scripts/worker_suy_luan.py` lấy ca đang chờ, tải ảnh, chạy mô hình và ghi kết quả qua các endpoint dành cho worker (xác thực bằng `X-Service-Token`). Mô hình của phần mô hình ảnh trả 7 xác suất Type_1..Type_7; `app/services/quy_doi_nhan.py` cộng chúng theo nhóm BITSS (BSFS 1–3, 4, 5–6, 7). Luồng đầy đủ đã được kiểm bằng mô hình giả; chạy với mô hình thật cần checkpoint huấn luyện bằng repo mô hình ảnh (chưa có).
- **Đánh giá trên Gemini thật đã đo xong** (24/09–04/10/2026, kéo dài nhiều ngày vì hạn mức của gói miễn phí): 4 mô hình × 8 kịch bản × 3 lượt. `gemini-3.6-flash`, `gemini-3.7-flash` và `gemini-3.8-flash` đạt cả 24/24 cặp (kịch bản, lượt); `gemini-3.5-flash-lite` đạt 21/24, cả ba lần trượt đều ở phép kiểm an toàn. Mô hình mặc định vẫn là `gemini-3.6-flash`, vì hai bản mới hơn bị từ chối do quá tải ở khoảng 57% số lần gọi so với 13%. Đợt đo chạy trên mã trước các bản sửa ngày 05/10 và 08/10, chưa đo lại. Kết quả thô nằm trong `outputs/ket_qua_llm/`; quy ước tên tệp và cách tổng hợp ghi trong [`outputs/ket_qua_llm/README.md`](outputs/ket_qua_llm/README.md).
- **Mọi dữ liệu trong repo là dữ liệu kiểm thử tự tạo.** Không có dữ liệu bệnh nhân thật.

## Cấu trúc repo

```
pediatric-gastro-ai-assistant/
├── main.py                  # Điểm vào FastAPI: kiểm schema, CORS, dọn dữ liệu lúc khởi động
├── setup_database.py        # Mô hình dữ liệu (9 bảng) + lệnh khởi tạo / bổ sung cột thiếu
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
│       ├── lam_sach_anh.py  # Xoá siêu dữ liệu (EXIF/GPS, ghi chú JPEG...) trước khi ảnh chạm đĩa
│       ├── uncertainty.py   # Gắn cờ ca mô hình không chắc chắn (ngưỡng 0,70)
│       ├── quy_doi_nhan.py  # 7 xác suất Type_1..7 của mô hình ảnh -> nhóm BITSS 1-4
│       ├── xuat_du_lieu.py  # Chọn ca đủ điều kiện xuất, mã ẩn danh HMAC
│       └── ...              # rate_limit, records_query, security, token_store, yeu_to_lam_sang
├── scripts/                 # Công cụ chạy tay trên máy chủ
│   ├── create_clinician.py  # Đường DUY NHẤT tạo tài khoản bác sĩ
│   ├── don_du_lieu_qua_han.py       # Dọn tin nhắn quá hạn, token hết hạn (cron)
│   ├── xuat_du_lieu_huan_luyen.py   # Xuất dữ liệu huấn luyện ẩn danh cho phần mô hình
│   ├── token_worker.py      # Cấp / thu hồi / liệt kê token riêng của từng worker suy luận
│   └── worker_suy_luan.py   # Worker suy luận: hàng chờ -> ảnh -> mô hình -> ghi kết quả
├── tests/
│   ├── verify_task.py       # 537 kiểm tra tự động (46 mục), chạy trên DB và thư mục tạm
│   ├── test_cong_dot_bien.py # Regression: crash, thiếu test, mã thoát, baseline hỏng, JSON/log
│   └── test_migration.py    # DB mới/cũ, giữ dữ liệu, chạy lặp, rollback và CLI lỗi
├── docs/
│   └── NANG_CAP_VA_PHUC_HOI_SQLITE.md # Sao lưu, nâng cấp và phục hồi SQLite
├── danh_gia/                # Đánh giá
│   ├── eval_agent.py        # 8 kịch bản trên Gemini thật (tốn hạn mức)
│   ├── danh_gia_luoi_canh_bao.py    # Lưới cảnh báo trên 67 câu có nhãn, so với 2 chiến lược khác
│   ├── phan_tich_co_so_toan.py      # Số liệu phần cơ sở toán học: Wilson, bootstrap, McNemar, Bảng 2.2...
│   └── kiem_thu_dot_bien.py         # Kiểm thử đột biến: 59 lỗi cố ý chèn vào bản sao của mã
├── outputs/
│   ├── ket_qua_luoi_canh_bao.json   # Kết quả đánh giá lưới cảnh báo (tất định)
│   ├── ket_qua_co_so_toan.json      # Kết quả phan_tich_co_so_toan.py (tất định)
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
| `INFERENCE_SERVICE_TOKEN` | không | Chỉ đặt ở máy chạy worker: token riêng của worker đó, cấp bằng `scripts/token_worker.py` trên máy chủ. Máy chủ không đọc biến này; chưa cấp token nào thì mọi endpoint dành cho worker (`inference-*`, `worker/hang-cho`, `worker/anh`) từ chối |
| `KHOA_MA_AN_DANH_DU_LIEU` | khi xuất dữ liệu | Khoá HMAC tạo mã bệnh nhân ẩn danh; giữ cố định suốt dự án |
| `GEMINI_MODEL` | không | Model phục vụ người dùng; để trống thì dùng mặc định đã ghim trong mã (`gemini-3.6-flash`) |
| `GEMINI_EVAL_MODEL` | không | Model mặc định của `danh_gia/eval_agent.py`; nên khác model phục vụ vì hạn mức tính riêng theo từng model |
| `CORS_ALLOW_ORIGINS` | không | Các origin được phép gọi API từ trình duyệt |
| `CHAT_LUU_TRU_NGAY` | không | Số ngày giữ lịch sử hội thoại (mặc định 90, cho phép 1–3650) |

Các biến còn lại (hạn mức gọi, số vòng và số lần thử lại khi gọi Gemini, đường dẫn database và thư mục ảnh...) đều có giá trị mặc định và được giải thích trong `.env.example`.

## Cách chạy

Các lệnh dưới đây viết cho thư mục gốc repo; với pip, bỏ tiền tố `uv run`. Database, thư mục ảnh và nhật ký xuất luôn được tính từ gốc dự án, kể cả khi đặt đường dẫn tương đối trong `.env`. Vì vậy chạy script từ thư mục khác, hay bằng nút Run của IDE, vẫn dùng đúng dữ liệu.

Khởi tạo database (tạo `data/bitss_clinic.db`). Chạy lại mỗi khi cập nhật phiên bản. Với database tạo trước 16/10/2026, lệnh này còn chuyển yếu tố lâm sàng từ cột chuỗi cũ sang bảng `child_yeu_to` rồi xoá cột cũ (máy chủ từ chối khởi động khi chưa chuyển), nên sao lưu `data/` trước khi chạy:

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

Tài liệu API tương tác (`/docs`, `/redoc`, `/openapi.json`) tắt theo mặc định để không phát bản đồ toàn bộ endpoint cho người lạ; khi phát triển, đặt `BAT_TAI_LIEU_API=1` trong `.env` rồi mở <http://127.0.0.1:8000/docs>. Nếu database chưa khớp phiên bản mã, ứng dụng từ chối khởi động và in ra đúng lệnh cần chạy.

Chạy worker suy luận (ở cửa sổ khác, máy chủ đang chạy). Mỗi worker cần token riêng, có hạn dùng và thu hồi được; cấp trên máy chủ rồi đặt token in ra vào `INFERENCE_SERVICE_TOKEN` của worker:

```bash
uv run python scripts/token_worker.py cap --ten gpu-01 --so-ngay 90
```

Thu hồi bằng `thu-hoi --id <id>`, xem danh sách bằng `liet-ke`. Mô hình giả để thử luồng:

```bash
uv run python scripts/worker_suy_luan.py --mo-hinh gia --mot-lan
```

Mô hình thật của phần mô hình ảnh (cần cài PyTorch, torchvision, PyYAML, pandas theo `requirements.txt` của repo đó; worker chỉ đọc repo này, kiểm `class_names` của checkpoint là đúng Type_1..Type_7):

```bash
uv run python scripts/worker_suy_luan.py --mo-hinh tien --repo-mo-hinh <đường dẫn repo mô hình ảnh> --checkpoint <repo>/outputs/checkpoints/best.pt
```

## Kiểm thử

```bash
uv run python tests/verify_task.py
```

Bộ kiểm tra tự dựng database tạm và mô hình giả. Nó không cần mạng, không cần khoá API, và không đụng vào `data/`. Kết quả mong đợi là `537/537 PASS`.

Để đo chất lượng của chính bộ kiểm tra, chạy kiểm thử đột biến. Script đóng băng bản sao nguồn rồi chèn từng lỗi trong 59 lỗi nhỏ vào các bản sao tạm; chạy bản gốc trước, chỉ tiếp tục nếu bản gốc đạt. Mỗi lượt phải chạy đủ cùng danh sách kiểm tra mới được phân loại. Thời gian phụ thuộc máy và số tiến trình.

```bash
uv run --frozen python danh_gia/kiem_thu_dot_bien.py --workers 4 --timeout 600
```

Kết quả có bốn trạng thái: `killed` (kiểm tra chức năng trượt, chạy đủ), `survived`,
`equivalent` và `error` (crash/timeout/thiếu kiểm tra hoặc đột biến không áp dụng được).
Mã thoát là 0 nếu tất cả killed/equivalent, 1 nếu còn survived, 2 nếu lỗi thí nghiệm
hoặc bản gốc không đạt. D07 chỉ tương đương với kiểm tra chức năng, vẫn đổi thời gian
so sánh token; D12 tương đương trên dữ liệu hợp lệ do child_id có NOT NULL.
Đây không phải phép đo rằng hệ thống đã phát hiện mọi lỗi có thể có.

JSON và stdout/stderr từng lượt nằm trong thư mục mới dưới `outputs/logs/`.
Dùng `--output-dir <thư mục mới>` để chọn nơi lưu; `--chi D01 D15` để kiểm nhanh
một nhóm khi phát triển. Nghiệm thu phải chạy đủ 59 mã, không dùng kết quả nhóm nhỏ thay thế.

Chạy regression của cổng và migration:

~~~bash
uv run --frozen python -m unittest discover -s tests -p "test_*.py" -v
~~~

Trước khi nâng cấp DB có dữ liệu, làm theo [runbook sao lưu và phục hồi](docs/NANG_CAP_VA_PHUC_HOI_SQLITE.md).
Lệnh `setup_database.py` trả lỗi nếu schema còn lệch; không coi cảnh báo là nâng cấp thành công.

Để kiểm tra tĩnh mã nguồn (pyflakes, các lỗi cơ bản của pycodestyle và luật bảo mật S chuyển từ bandit), chạy:

```bash
uvx ruff check
```

## Đánh giá

Đánh giá lớp phát hiện dấu hiệu nguy hiểm. Kết quả tất định, ghi vào `outputs/ket_qua_luoi_canh_bao.json`:

```bash
uv run python danh_gia/danh_gia_luoi_canh_bao.py
```

Tính lại các số liệu của phần cơ sở toán học trong báo cáo: tiền đề của hàm bỏ dấu σ, khoảng tin cậy Wilson, bootstrap F1, kiểm định McNemar, xác suất rò rỉ khi chia theo ảnh, báo động giả của đặc trưng xu hướng và của permutation test. Tất định, không cần mạng, mất khoảng một phút; ghi vào `outputs/ket_qua_co_so_toan.json`:

```bash
uv run python danh_gia/phan_tich_co_so_toan.py
```

Chạy 8 kịch bản trên Gemini thật. Lệnh này cần khoá và tốn hạn mức ngày; nên đặt `GEMINI_EVAL_MODEL` khác model phục vụ người dùng:

```bash
uv run python danh_gia/eval_agent.py --model gemini-3.6-flash --ghi-ket-qua outputs/ket_qua_llm/lan1_gemini-3.6-flash.json
```

Mỗi tệp kết quả JSON ghi kèm model, thời điểm chạy, mã băm SHA-256 của hướng dẫn hệ thống và commit mã nguồn lúc chạy. Chỉ các lượt đo ngày 24/09 ghi commit của kho phát triển (lưu trữ riêng, không có trong lịch sử repo này); từ ngày 25/09, commit ghi trong kết quả đều có trong lịch sử `main`. Đợt đo 3 lượt × 4 mô hình được chạy qua nhiều ngày vì hạn mức; cách đọc các tệp, tiến độ và cách tổng hợp nằm trong [`outputs/ket_qua_llm/README.md`](outputs/ket_qua_llm/README.md).

## Hạn chế đã biết

- Endpoint đăng ký trả 409 cho email đã có tài khoản và 201 cho email mới, nên dò được một email đã đăng ký hay chưa; hạn mức 5 lần/10 phút/IP chỉ làm chậm việc dò. Chưa có xác minh email, đổi/quên mật khẩu và xoá tài khoản.
- Tên bé là trường văn bản tự do duy nhất của hồ sơ còn đi tới mô hình (tối đa 100 ký tự), nên vẫn là một kênh chèn chỉ thị gián tiếp. Ghi chú y tế và chế độ ăn đã là từ vựng có kiểm soát.
- Lớp cảnh báo dấu hiệu nguy hiểm khớp theo chuỗi: không hiểu phủ định, thành ngữ hay điều kiện tuổi. Danh sách dấu hiệu và ngưỡng độ tự tin 0,70 chưa được bác sĩ thẩm định.
- Nếu worker suy luận dừng đột ngột khi đang xử lý, ca nằm lại ở `processing`: chưa có hạn giờ tự đưa về `queued`, phải gọi `inference-failed` rồi `inference-retry`.
- Nhật ký kiểm toán chưa có giao diện đọc (chỉ truy vấn trực tiếp database) và chưa có thời hạn lưu trữ; trigger chặn sửa/xoá chỉ viết cho SQLite. Danh sách (`GET /api/v1/records`, `GET /api/v1/children`) chưa được ghi từng lượt xem.
- Gói Gemini miễn phí chỉ phục vụ được vài lượt chat mỗi ngày và không phù hợp với dữ liệu y tế thật; hạn mức gọi API chỉ lưu trong bộ nhớ của từng tiến trình.

## Khai báo sử dụng AI

AI có hai vai trò khác nhau trong dự án:
- **Thành phần của sản phẩm:** Gemini (Google) là mô hình của trợ lý hội thoại. Cách ràng buộc và đánh giá nó chính là nội dung nghiên cứu.
- **Công cụ phát triển:** mô hình Claude (Anthropic), dùng qua Claude Code. Nó hỗ trợ viết và rà soát mã, viết kiểm tra, và soạn bản nháp báo cáo.

Nhóm tự xác định yêu cầu và chọn phương án. Mọi kết quả được kiểm chứng bằng kiểm tra tự động và các phép đo chạy lại được. Nhóm chịu trách nhiệm về toàn bộ nội dung. Chi tiết ở phụ lục "Khai báo sử dụng công cụ trí tuệ nhân tạo" của báo cáo môn học (nộp riêng).

## Tác giả

- **Đỗ Kinh Văn** (MSSV 2413917): backend, trợ lý hội thoại, kiểm thử, đánh giá.
- **Nguyễn Gia Tiến**: mô hình phân loại ảnh theo BITSS và XAI (repo riêng).

Giảng viên hướng dẫn: Lê Xuân Đại.
