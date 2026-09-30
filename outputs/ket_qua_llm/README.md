# Kết quả đánh giá trợ lý trên mô hình Gemini thật

Thư mục này chứa kết quả thô của `danh_gia/eval_agent.py`: 8 kịch bản (S1–S8) chạy trên 4 mô hình Gemini, mỗi mô hình 3 lượt. Mỗi tệp JSON là một lần chạy.

## Vì sao một lượt đo nằm ở nhiều tệp

Gói miễn phí của Gemini cho mỗi mô hình khoảng 20 request một ngày, và cả lỗi quá tải 503 cũng bị trừ vào hạn mức. Vì vậy một lượt 8 kịch bản thường phải chia ra nhiều ngày mới đo xong. Mỗi ngày, bộ chạy tiếp tục từ kịch bản còn thiếu và ghi kết quả ra một tệp mới, không ghi đè tệp cũ.

Quy ước tên tệp:

| Tên tệp | Ý nghĩa |
|---|---|
| `lan{N}_{mô hình}.json` | Phần đầu của lượt N |
| `lan{N}_{mô hình}_tiep.json`, `_tiep1`, `_tiep2`, … | Các phần tiếp theo của cùng lượt N, chỉ chứa kịch bản chưa đo |

Một kịch bản có trường `"loi": "HetHanMucNgay: 429 ..."` nghĩa là mô hình hết hạn mức ngày trước khi trả lời. Kịch bản đó **chưa được đo**, không phải bị trượt, và sẽ được đo lại ở tệp tiếp theo. `"TroLyBanTam: 503 ..."` là quá tải phía máy chủ, cũng được xử lý như chưa đo. Kết quả của một lượt là hợp của các kịch bản đo được trong mọi tệp cùng lượt đó.

## Điều kiện đo giống nhau giữa các ngày

Mọi tệp đều ghi `sha256_huong_dan_he_thong = 6231314a…`, nghĩa là hướng dẫn hệ thống (prompt) không đổi trong suốt đợt đo.

Trường `git_commit` nhận các giá trị khác nhau nhưng mã được đo không đổi:

| `git_commit` | Ngày | Ghi chú |
|---|---|---|
| `fd5ec18`, `fb26e44`, `38a5d70` | 24/09 | Commit trong lịch sử phát triển trước khi công bố repo (không có trong lịch sử `main`). So với `9e86dfa`, `app/` chỉ khác một cách viết điều kiện lọc tương đương và một chuỗi thông báo lỗi. `eval_agent.py` chỉ khác ở phần điều khiển việc chạy (thử lại khi 503, thời điểm ghi commit, đường dẫn); luật chấm không đổi. |
| `9e86dfa` | 25/09 | Mã nguồn công bố. |
| `f9c6a5c` | 26/09 | So với `9e86dfa` chỉ thêm các tệp kết quả của ngày 25/09; không đổi dòng mã nào. |
| `666563f` | 27/09 | So với `f9c6a5c` chỉ thêm các tệp kết quả của ngày 26/09 và sửa README; không đổi dòng mã nào. |
| `668ea23` | 28/09 | So với `666563f` chỉ thêm các tệp kết quả của ngày 27/09 và sửa README; không đổi dòng mã nào. |
| `7742b9d` | 29/09 | So với `668ea23`: thêm tệp kết quả ngày 28/09, script phân tích `danh_gia/phan_tich_co_so_toan.py` và sửa README. `app/`, `danh_gia/eval_agent.py` và `tests/` không đổi. Commit này sau đó được đổi tên thành "Update README"; trên `main` nó mang mã `2dc2999` với nội dung giống hệt, còn mã gốc `7742b9d` được giữ ở tag `do-llm-2909`. |
| `ac44bb3` | 30/09 | So với `2dc2999` chỉ thêm các tệp kết quả của ngày 29/09 và sửa README; không đổi dòng mã nào. |

## Tiến độ (cập nhật 30/09/2026)

Số kịch bản đã đo được trên 8, theo từng lượt:

| Mô hình | Lượt 1 | Lượt 2 | Lượt 3 | Tổng |
|---|---|---|---|---|
| gemini-3.5-flash-lite | 8/8 | 8/8 | 8/8 | 24/24 |
| gemini-3.6-flash | 8/8 | 8/8 | 8/8 | 24/24 |
| gemini-3.7-flash | 8/8 | 8/8 | 8/8 | 24/24 |
| gemini-3.8-flash | 8/8 | 8/8 | 0/8 | 16/24 |

## Các tệp theo ngày đo

| Ngày | Tệp |
|---|---|
| 24/09 | `lan1_gemini-3.5-flash-lite`, `lan1_gemini-3.6-flash`, `lan1_gemini-3.7-flash`, `lan1_gemini-3.8-flash`, `lan1_gemini-3.8-flash_tiep` |
| 25/09 | `lan1_gemini-3.6-flash_tiep1`, `lan1_gemini-3.7-flash_tiep1`, `lan1_gemini-3.8-flash_tiep2`, `lan2_gemini-3.5-flash-lite`, `lan2_gemini-3.6-flash`, `lan3_gemini-3.5-flash-lite` |
| 26/09 | `lan1_gemini-3.7-flash_tiep2`, `lan1_gemini-3.8-flash_tiep3`, `lan2_gemini-3.6-flash_tiep1`, `lan2_gemini-3.7-flash` (hết hạn mức ngay kịch bản đầu, chưa đo được gì), `lan3_gemini-3.6-flash` |
| 27/09 | `lan1_gemini-3.8-flash_tiep4`, `lan2_gemini-3.7-flash_tiep1`, `lan2_gemini-3.8-flash`, `lan3_gemini-3.6-flash_tiep1` |
| 28/09 | `lan2_gemini-3.7-flash_tiep2`, `lan2_gemini-3.8-flash_tiep1`, `lan3_gemini-3.7-flash` |
| 29/09 | `lan2_gemini-3.8-flash_tiep2`, `lan3_gemini-3.7-flash_tiep1`, `lan3_gemini-3.8-flash` (hết hạn mức ngay kịch bản đầu, chưa đo được gì). Đây là đợt đo của ngày 29/09 nhưng tệp được ghi sáng 30/09 (khoảng 09:50), vẫn trong chu kỳ hạn mức ngày bắt đầu lúc 14:00 ngày 29/09. |
| 30/09 | `lan3_gemini-3.8-flash_tiep1` (chưa đo được gì: S1 bị quá tải 503 cả ba lần thử, S2 hết hạn mức ngày). Hôm đó hai tiến trình đo bị chạy cùng lúc và cùng ghi vào tệp này; tệp giữ lại là của lần ghi sau. Cả hai lần đều không đo xong kịch bản nào nên không mất kết quả. Bộ chạy sau đó được thêm khoá chống chạy song song. |

## Cấu trúc một tệp

Các trường ở mức tệp gồm `model`, `bat_dau_utc`, `ket_thuc_utc`, `git_commit`, `sha256_huong_dan_he_thong`, `nguong_so_tu` (ngưỡng độ dài 220 từ), `so_lan_goi_api` và `dung_som` (lý do dừng sớm, nếu có).

Mảng `ket_qua` có một phần tử cho mỗi kịch bản, gồm các trường:
- `ma`, `ten`: mã kịch bản S1–S8 và tên ngắn;
- `loi`: lỗi khiến kịch bản không đo được (`null` nếu đo xong);
- `so_goi_api`: số lời gọi Gemini của kịch bản; `so_lan_thu`: số lần phải chạy lại cả kịch bản vì quá tải;
- `ly_do`: mã lý do khi câu trả lời bị backend chặn hoặc thay;
- `nguon`: nguồn câu trả lời (`model` hoặc `du_phong`);
- `da_nhac_sua`, `co_canh_bao_backend`: backend có can thiệp hay không;
- `so_tu_model`: số từ do riêng mô hình viết;
- `phep_kiem`: danh sách phép kiểm, mỗi phép có `mo_ta`, `dat` và `nho_backend` (đạt chỉ nhờ backend can thiệp);
- `tra_loi`: câu trả lời đầy đủ.

Trường `lan` bên trong một tệp là số thứ tự lần chạy trong lần gọi đó (luôn là 1 với cách chạy hiện tại). Số lượt đo nằm ở tên tệp.

## Cách tổng hợp

Mỗi ô (mô hình, lượt, kịch bản) lấy từ phần tử có `loi` bằng `null`; không ô nào được đo hai lần. Một kịch bản "đạt trọn" khi mọi phép kiểm có `dat` là `true`. Một phép kiểm "chỉ đạt nhờ backend" khi `dat` và `nho_backend` cùng là `true`. Một kịch bản "không ổn định" khi có lượt đạt trọn và có lượt không. Số lời gọi API được cộng trên các kịch bản đo được.

Số lỗi 503 (số lời gọi bị Gemini từ chối vì quá tải) được đếm từ nhật ký console trong `outputs/logs/`. Nhật ký không được commit, nên con số này không tính lại được chỉ từ repo; trường `so_lan_thu` ở trên là một đại lượng khác (số lần chạy lại cả kịch bản).

Việc chạy nối tiếp qua nhiều ngày và tổng hợp thành bảng cho báo cáo dùng hai script hỗ trợ nằm ngoài repo; chúng chỉ gọi `danh_gia/eval_agent.py` với `--model`, `--chi-bai` và `--ghi-ket-qua`, và đọc lại các tệp trong thư mục này theo đúng quy tắc trên.
