# W02 — Giảm tên hồ sơ trong ngữ cảnh LLM

Phạm vi đồ án: kiểm chứng luồng dữ liệu bằng fixture, không tuyên bố ẩn danh hoàn toàn
hoặc đã đánh giá hành vi Gemini thật sau thay đổi này.

## Hành vi mới

Bốn công cụ tra hồ sơ, danh sách con, kết quả ca và lịch sử ca xuất bi_danh_be,
dạng Bé #<child_id>, thay cho trường ten_be lấy từ children.name.
Công cụ xu hướng chỉ trả thống kê; payload của cả năm công cụ được kiểm.
Bí danh ổn định theo ID, không đổi khi sửa tên và phân biệt được hai hồ sơ cùng nhà.
ID vốn đã có trong payload: bí danh không che ID, không dùng làm chốt phân quyền.

API nghiệp vụ /children, /records vẫn trả tên theo quyền như trước.
Quyền xem và đồng thuận tiếp tục kiểm bằng principal cùng ID, không dùng tên/bí danh.
Prompt yêu cầu hỏi rõ ID/bí danh khi chưa biết đang nói về bé nào. Việc tự giải quyết
một tên người dùng gõ thành hồ sơ nào chưa thuộc phạm vi W02.

## Lịch sử trước W02

Chỉ sửa công cụ là chưa đủ: câu trả lời cũ có thể đã chứa tên lấy từ hồ sơ.
Bảng chat_messages có thêm cột nullable phien_ban_ngu_canh, không có default.
Migration thêm cột này để bản ghi cũ giữ NULL, không gán nhãn mới cho dữ liệu cũ.

Đường ghi ghi_luot gán phiên bản w02-2026-10-23 cho cả câu hỏi và câu trả lời mới.
Đường đọc chỉ nạp đúng phiên bản hiện tại, trước khi cắt số lượt. Biên gửi agent
cũng bỏ lịch sử không có đúng phiên bản, kể cả người gọi truyền thẳng lịch sử cũ.
Sau nâng cấp, lượt đầu có thể có so_luot_nho=0; các lượt mới sau đó được nhớ lại.

Bản ghi cũ không bị sửa/xoá bởi việc lọc này. Chúng vẫn được xoá theo thời hạn lưu
hoặc khi chủ tài khoản gọi DELETE /api/v1/chat/history. Không chạy xoá hàng loạt
trên dữ liệu đang có. Bác sĩ/admin vẫn không được lưu/nạp lịch sử nhiều gia đình.

## Nâng cấp và rollback

Theo [runbook SQLite](NANG_CAP_VA_PHUC_HOI_SQLITE.md), sao lưu DB/ảnh và diễn tập trên bản sao
trước khi nâng cấp DB có dữ liệu. Chạy:

~~~powershell
uv run --frozen python setup_database.py
if ($LASTEXITCODE -ne 0) { throw 'Schema chua san sang' }
~~~

init_db thêm cột nullable và chạy lặp được. Ứng dụng từ chối khởi động khi schema
chưa khớp; không tự đánh dấu lịch sử cũ là mới để vượt qua bước nâng cấp.
Rollback về mã trước W02 sẽ mất chốt lọc lịch sử mới và lại có thể gửi tên hồ sơ.
Khôi phục mã/dữ liệu theo runbook không đồng nghĩa vẫn giữ chính sách giảm tên W02.

## Giới hạn và kiểm chứng

- Đây là loại trường tên hồ sơ khỏi dữ liệu công cụ, không phải bộ dò mọi PII.
  Tin nhắn hiện tại, lịch sử mới do người dùng tự gõ và lời dặn tự do của bác sĩ
  có thể chứa tên hoặc dữ liệu cá nhân. Thông báo đồng thuận nêu rõ giới hạn này.
- Giữ phiên bản đồng thuận hiện tại vì thay đổi giảm dữ liệu, không thêm loại dữ liệu
  chia sẻ mới. Chính sách ngữ cảnh có phiên bản riêng; không coi nó là chứng nhận
  văn bản đã sạch PII.
- Kiểm thử bắt payload thực đưa vào client giả: tên chứa chỉ thị giả, năm công cụ,
  bí danh sau đổi tên, API nghiệp vụ, vượt quyền, lịch sử cũ/mới, nhiều bé, truyền
  thẳng lịch sử cũ, tên tự gõ và rút đồng thuận.
- Migration kiểm dữ liệu cũ vẫn còn và cột phiên bản vẫn NULL sau hai lần nâng cấp.
  D60–D66 đưa tên trở lại hoặc phá chốt lịch sử để đo chất lượng các kiểm tra.
- Các lượt hỏi nối tiếp ở đây chứng minh vận chuyển ngữ cảnh. Đánh giá xem mô hình thật
  có chọn đúng bé, hỏi lại khi mơ hồ và trả lời đúng hay không thuộc W03/W09.
