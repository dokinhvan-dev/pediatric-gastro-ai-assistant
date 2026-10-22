# Nâng cấp security và phục hồi SQLite

Phạm vi: database SQLite của BITSS, từ bản có sáu bảng/cột
children.yeu_to_lam_sang, main bảy bảng, hoặc security trước actor_worker_id,
sang model security chín bảng. Không áp dụng hướng dẫn này cho PostgreSQL.
Các kiểm thử dùng fixture tự tạo, không phải bản sao dữ liệu bệnh nhân.

## 1. Điều kiện trước nâng cấp

- Ghi SHA đang vận hành và SHA ứng viên; chỉ triển khai ứng viên đã qua đủ bốn job
  Security trên chính SHA đó. Job kiểm thử gồm regression, verify_task và đột biến.
- Xác định đường dẫn tuyệt đối của DATABASE_URL và UPLOAD_DIR từ cấu hình vận hành.
  Đường dẫn tương đối được ứng dụng tính từ thư mục chứa setup_database.py.
  Không dùng database mặc định nếu cấu hình vận hành trỏ nơi khác.
- Kiểm tra dung lượng trống cho bản sao đầy đủ, quyền truy cập và phiên bản SQLite.
  Đợt W01 thử trên SQLite 3.50.4. DROP COLUMN có từ 3.35.0 nhưng có lỗi sửa ở 3.35.5;
  không dùng bản dưới 3.35.5 cho migration này. Lưu Python/SQLite và uv.lock cùng bản phát hành.
- Dừng API, worker và mọi script có thể ghi DB/ảnh. Giữ dừng cho tới khi kiểm tra
  xong. Không chạy migration trên DB đang phục vụ request.

## 2. Sao lưu và kiểm tra bản sao

Tạo thư mục backup mới, có giới hạn truy cập. Sao lưu toàn bộ UPLOAD_DIR, kể cả ảnh
và heatmap. Sao lưu cấu hình/bí mật riêng trong nơi bảo mật; không đưa vào Git/log.
Ghi SHA cũ, thời điểm, số lượng tệp và SHA-256 các tệp backup trong manifest.

Database dùng SQLite backup API để lấy cả thay đổi đã commit trong WAL. Không chỉ
chép một file .db rồi bỏ qua -wal. Ví dụ dưới đây chạy trong PowerShell tại repo,
truyền đường dẫn thật đã kiểm tra (thay hai đường dẫn ví dụ):

~~~powershell
$taskDb = 'D:\BITSS_DATA\bitss_clinic.db'
$taskBackup = 'D:\BITSS_BACKUP\2026-10-22\clinic.db'
@'
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

source, target = map(Path, sys.argv[1:])
if not source.is_file() or target.exists():
    raise SystemExit("Can DB nguon ton tai va dich backup chua ton tai")
target.parent.mkdir(parents=True, exist_ok=True)
with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
with closing(sqlite3.connect(target)) as check:
    if check.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SystemExit("Backup khong qua integrity_check")
    if check.execute("PRAGMA foreign_key_check").fetchall():
        raise SystemExit("Backup co loi khoa ngoai")
print("Backup da kiem tra")
'@ | uv run --frozen python - $taskDb $taskBackup
if ($LASTEXITCODE -ne 0) { throw 'Sao luu that bai: dung nang cap' }
Get-FileHash -Algorithm SHA256 -LiteralPath $taskBackup
~~~

Chỉ tiếp tục sau khi đã thử mở bản backup và phục hồi vào thư mục thử riêng.
Giữ API/worker dùng dữ liệu thật ở trạng thái dừng.

## 3. Diễn tập trên bản sao

1. Sao chép backup DB và UPLOAD_DIR vào thư mục thử mới; dùng cấu hình riêng,
   DATABASE_URL/UPLOAD_DIR tuyệt đối. Không khởi chạy worker/Gemini với dữ liệu thật.
2. Ghi số dòng từng bảng và kiểm tra một số ID tham chiếu trước nâng cấp.
3. Chạy ứng viên:

~~~powershell
$env:DATABASE_URL = 'sqlite:///D:/BITSS_REHEARSAL/clinic.db'
$env:UPLOAD_DIR = 'D:\BITSS_REHEARSAL\uploads'
uv run --frozen python setup_database.py
if ($LASTEXITCODE -ne 0) { throw 'Migration that bai' }
uv run --frozen python setup_database.py
if ($LASTEXITCODE -ne 0) { throw 'Migration lap that bai' }
uv run --frozen python -c "from setup_database import kiem_tra_schema; x=kiem_tra_schema(); print(x); raise SystemExit(bool(x))"
if ($LASTEXITCODE -ne 0) { throw 'Schema chua khop' }
~~~

4. Kiểm tra integrity_check, foreign_key_check và số dòng; users/children/
   stool_records/audit_log/worker_tokens có sẵn phải giữ dữ liệu.
   child_yeu_to nhận mỗi mã hợp lệ một lần; cột chuỗi cũ được bỏ. Mã lạ bị bỏ qua
   có cảnh báo: phải xem cảnh báo trước khi chấp nhận dữ liệu chuyển đổi.
5. Kiểm tra hai trigger trg_audit_log_cam_sua và trg_audit_log_cam_xoa tồn tại,
   không chỉ kiểm bảng/cột. Trên bản thử, UPDATE/DELETE một audit fixture phải bị chặn.
6. Kiểm tra ảnh/heatmap tham chiếu vẫn mở được. Với bản thử dùng dữ liệu thật, tránh
   gọi API làm phát sinh truyền dữ liệu ra ngoài.

Các bài kiểm tra tự động chạy bằng:

~~~powershell
uv run --frozen python -m unittest discover -s tests -p "test_*.py" -v
~~~

Chúng kiểm DB mới, DB sáu/bảy bảng, bổ sung actor_worker_id, giữ token/audit,
chạy lặp, lỗi trước DROP COLUMN và schema thiếu cột bắt buộc. Đây là các dạng
schema cụ thể được hỗ trợ; kiem_tra_schema chỉ so bảng/cột, không chứng minh mọi
index, kiểu dữ liệu hoặc CHECK của một DB cũ bất kỳ đều đúng.

## 4. Nâng cấp dữ liệu vận hành

Sau khi diễn tập đạt và được duyệt triển khai, dùng đúng SHA/uv.lock đã kiểm.
Trong phiên shell triển khai mới, đặt DATABASE_URL/UPLOAD_DIR về đường dẫn vận hành
đã xác định; tránh tái sử dụng biến của thư mục diễn tập.
Chạy lại các bước migration/kiểm tra ở mục 3 trên DB vận hành khi các dịch vụ vẫn dừng.

init_db() tạo bảng mới, chuyển yếu tố lâm sàng và thêm cột nullable còn thiếu.
Thiếu cột NOT NULL không có server default thì dừng bằng SchemaChuaSanSang;
CLI trả mã 1. Script gọi init_db() cũng nhận exception và dừng.
Không tự xoá dữ liệu hoặc thêm giá trị giả để ép schema đạt.

Chỉ khởi động API rồi worker sau khi schema, dữ liệu và đường dẫn ảnh đạt kiểm tra.
Kiểm đăng nhập, quyền xem ca, tạo audit và xác thực token worker bằng tài khoản
thử được phép dùng. Giữ bản backup cho tới khi nghiệm thu hoàn tất.

## 5. Khi thất bại và phục hồi

- Nếu chuyển yếu tố lâm sàng lỗi trước DROP COLUMN, giao dịch chuyển dữ liệu rollback.
  Các bước create_all/thêm cột nằm ngoài giao dịch đó có thể đã hoàn tất.
  Không tuyên bố toàn bộ init_db là một giao dịch nguyên tử.
- Giữ dịch vụ dừng. Lưu stderr và mã lỗi, SHA ứng viên; không lưu token/PII vào log.
  Không sửa trực tiếp DB bằng câu SQL thử nghiệm.
- Khôi phục vào thư mục mới từ cặp backup DB + ảnh cùng thời điểm; không ghi đè bản
  DB lỗi. Dùng SQLite backup API hoặc bản DB backup đã đóng kết nối. Tránh mang
  -wal/-shm của DB lỗi sang thư mục phục hồi.
- Kiểm integrity_check, foreign_key_check, số dòng và ảnh; chạy lại SHA cũ với
  cấu hình trỏ bản phục hồi. Kiểm quyền và nghiệp vụ rồi mới mở dịch vụ.
- Migration hiện chỉ tiến lên; quay về mã cũ cùng DB đã nâng cấp chưa được bảo đảm.
  Rollback triển khai dùng cả mã cũ lẫn dữ liệu backup.

Nếu hệ thống đã nhận dữ liệu mới sau nâng cấp, phục hồi backup sẽ mất các thay đổi
sau thời điểm sao lưu. Dừng ghi, giữ nguyên DB lỗi và đối soát dữ liệu phát sinh
trước khi quyết định phục hồi; không tự bỏ qua phần chênh lệch.

Nguồn đối chiếu: [SQLite DROP COLUMN](https://sqlite.org/lang_altertable.html#alter_table_drop_column),
[bản sửa 3.35.5](https://sqlite.org/releaselog/3_35_5.html) và
[Python SQLite backup](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup).
Ví dụ đóng kết nối bằng closing vì context manager của Connection chỉ quản lý
giao dịch, không tự đóng kết nối; xem [tài liệu Python](https://docs.python.org/3.14/library/sqlite3.html#how-to-use-the-connection-context-manager).
