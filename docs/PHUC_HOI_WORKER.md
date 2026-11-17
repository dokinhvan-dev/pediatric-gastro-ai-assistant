# Phục hồi ca khi worker dừng giữa chừng

Nếu worker thoát sau khi nhận ca, ca nằm ở `processing` và worker kế tiếp chỉ lấy các ca `queued`. Người vận hành có thể đưa riêng ca đó về hàng chờ qua hai endpoint hiện có. Quy trình này dùng trên nhánh `security`, với dữ liệu tự tạo và worker mô hình giả khi demo.

## Thao tác phục hồi

1. Lấy `record_id` từ ca bị kẹt và kiểm tra ca đang ở `processing`. Trong SQLite, có thể đối chiếu sự kiện `worker_bat_dau` trong `audit_log` để xác định worker đã nhận ca. Xác nhận tiến trình cũ đã dừng và các request còn dở của nó đã kết thúc trước khi đưa ca về hàng chờ. Nếu chưa xác nhận được, dừng thao tác tại đây.
2. Dùng một token dịch vụ hợp lệ của người vận hành gọi `POST /api/v1/records/{record_id}/inference-failed` với lý do ngắn gọn. Kiểm tra HTTP `200` và `data.inference_status` là `failed`.
3. Gọi `POST /api/v1/records/{record_id}/inference-retry`. Kiểm tra HTTP `200` và trạng thái `queued`. Lỗi cũ được xoá khi đưa ca về hàng chờ.
4. Chạy worker mới. Khi tới lượt ca, kiểm tra trạng thái `completed` và kết quả tại màn hình chi tiết ca. Trong môi trường demo có đúng một ca đang chờ, dùng `uv run python scripts/worker_suy_luan.py --mo-hinh gia --mot-lan --so-ca 1` với token riêng của worker mới.

Ví dụ PowerShell dưới đây dùng token phục hồi đã đặt trong biến `INFERENCE_SERVICE_TOKEN`. Chọn mã ca từ môi trường demo cục bộ trước khi chạy.

```powershell
$recordId = Read-Host 'Ma ca dang processing'
$baseUrl = 'http://127.0.0.1:8000'
$recordUrl = "$baseUrl/api/v1/records/$([int]$recordId)"
$serviceHeaders = @{ 'X-Service-Token' = $env:INFERENCE_SERVICE_TOKEN }
$body = @{ error = 'Worker da dung truoc khi ghi ket qua' } | ConvertTo-Json -Compress

$failure = Invoke-RestMethod -Method Post -Uri "$recordUrl/inference-failed" -Headers $serviceHeaders -ContentType 'application/json' -Body $body -ErrorAction Stop
if ($failure.data.inference_status -ne 'failed') { throw 'Can kiem tra lai trang thai ca' }
Invoke-RestMethod -Method Post -Uri "$recordUrl/inference-retry" -Headers $serviceHeaders -ErrorAction Stop
```

`401` báo token dịch vụ thiếu hoặc sai. `409` yêu cầu đọc lại trạng thái trước khi thao tác tiếp. Nếu ca đã `completed`, cả hai endpoint đều trả `409`.

## Đối chiếu trên dữ liệu thử

Dùng `uv run python tests/verify_task.py` và xem mục **48a**. Bài kiểm cho một tiến trình worker thoát sau khi tải ảnh và trước khi ghi dự đoán. Sau đó, token phục hồi đưa ca qua `processing → failed → queued`, rồi một worker khác xử lý lại bằng mô hình giả. Bài kiểm đối chiếu các mã HTTP, kết quả ca và danh tính worker trong audit.

Khi cần xem nhật ký trên SQLite, dùng truy vấn đọc dưới đây và truyền mã ca vào tham số `:record_id`.

```sql
SELECT actor_worker_id, action, detail, outcome
FROM audit_log
WHERE object_type = 'record' AND object_id = :record_id
  AND action IN ('worker_bat_dau', 'worker_bao_loi', 'worker_thu_lai', 'worker_ghi_ket_qua')
ORDER BY id;
```

Khi báo cáo, mô tả đây là **phục hồi thủ công có kiểm tra quyền và nhật ký**. Hiện hệ thống cần người vận hành xác nhận lần chạy cũ đã dừng. API dựa trên trạng thái ca và chưa có mã định danh cho từng lần chạy.
