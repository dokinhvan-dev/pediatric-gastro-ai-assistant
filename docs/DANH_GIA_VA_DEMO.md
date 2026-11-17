# Đánh giá và trình diễn đồ án

## Tập câu độc lập cho lớp cảnh báo

Mục tiêu là kiểm tra xem việc giảm báo nhầm khi dùng cách xử lý dấu hiện tại có lặp lại trên một tập câu khác hay không. Tập gốc có 67 câu do tác giả tạo sau khi viết luật. Tập mới cần người soạn và gán nhãn chưa tham gia viết luật, làm việc trước khi xem từ khóa, câu gốc và dự đoán của ba phương án.

1. Cho người soạn biết tiêu chí gán nhãn dựa trên danh sách tín hiệu của hệ thống. Dùng các tình huống tự tạo, gồm câu khẳng định, phủ định, không dấu, cách diễn đạt khác nhau và câu có từ dễ gây nhầm. Giữ câu hỏi độc lập và bỏ thông tin nhận diện.
2. Gán `1` khi câu khẳng định có tín hiệu theo tiêu chí đã chốt, `0` cho câu còn lại. Nếu có hai người gán nhãn, giữ kết quả ban đầu của từng người và thống nhất cách xử lý bất đồng trước khi đo. Ghi người soạn, người gán nhãn, ngày chốt và tiêu chí vào biên bản.
3. Tạo tệp JSON là mảng đối tượng. Mỗi dòng có `tin_nhan` và `nhan`. Có thể thêm `ghi_chu` để giải thích nhãn. Chạy cùng mã cho cả ba phương án, lưu đầu ra riêng và giữ mã SHA-256 của tập câu.

```powershell
uv run --frozen python danh_gia/danh_gia_luoi_canh_bao.py --tap-cau tap_cau_doc_lap.json --ghi-ket-qua outputs/logs/ket_qua_tap_doc_lap.json
```

Báo cáo số bỏ sót (FN) và báo nhầm (FP) theo từng phương án. Đọc vài lỗi đại diện trước khi giải thích kết quả. Một tập câu giả lập độc lập có thể kiểm tra độ nhạy với cách viết của tác giả. Nó vẫn đo một bài thử trên dữ liệu giả lập.

## Đợt thăm dò Gemini sau thay đổi bí danh

Ba kịch bản kiểm tra liệt kê hai bé bằng bí danh (S4), hỏi lại khi chưa rõ bé nào (S9) và lấy đúng hồ sơ khi hỏi nối tiếp (S10). S10 có hai lượt chat. Bộ đo dùng SQLite tạm và hồ sơ tự tạo.

```powershell
uv run --frozen python danh_gia/eval_agent.py --chi-bai 4,9,10 --tran-request 16 --khong-thu-lai-ca --ghi-ket-qua outputs/logs/gemini_bi_danh_lan_moi.json
```

`so_request_gui` đếm tại lớp gửi HTTP, gồm cả lần thử lại. Số vòng xử lý được ghi riêng ở `so_goi_api`. Ghi nhận ca bị chặn bởi quota hoặc lỗi kết nối dưới dạng chưa hoàn thành phép đo. Soạn kết luận dựa trên từng tình huống và ghi rõ số lượt đã chạy.

Đợt 17/11/2026 chạy một lượt mỗi kịch bản với `gemini-3.6-flash` trên mã ứng dụng `dad4ceb`. S4 đạt 7/7 phép kiểm với 2 request, S9 đạt 3/3 với 2 request, S10 đạt 4/4 với 6 request. S10 gồm một lần retry sau phản hồi 429 tạm thời. Kết quả ghi lại ở [JSON của đợt thăm dò](../outputs/ket_qua_llm/w02_2026-11-17.json). SHA-256 của script tại thời điểm chạy là `458e8594977a8dc724b2a5be99cc2966fffb243cb446d5d945e3e117b5b95c9a`.

## Trình tự demo

1. Dùng nhánh `security` và một database demo với hồ sơ tự tạo. Tạo ca từ ảnh mẫu, chạy worker với `--mo-hinh gia` rồi mở kết quả bằng Swagger. Tài khoản bác sĩ duyệt ca sau khi có dự đoán.
2. Trình bày cách giới hạn dữ liệu mà Gemini được đọc và phân biệt bí danh với tên lưu trong hồ sơ. Minh họa bằng kết quả thăm dò đã lưu hoặc bằng một lượt live khi có quota.
3. Mở log kiểm thử mục 48a để minh họa việc ca được phục hồi sau khi worker dừng. Chỉ rõ hai thao tác do người vận hành thực hiện và chỗ kiểm tra nhật ký theo mã ca.
4. Trình bày bảng bỏ sót và báo nhầm của ba cách nhận diện tín hiệu. Nêu rõ nguồn của nhãn và việc tập 67 câu được tạo sau khi thiết kế luật.

[Lệnh chạy backend và worker](../README.md) · [Hướng dẫn phục hồi ca](PHUC_HOI_WORKER.md)
