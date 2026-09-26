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
| `f9c6a5c` | từ 26/09 | So với `9e86dfa` chỉ thêm các tệp kết quả của ngày 25/09; không đổi dòng mã nào. |

## Tiến độ (cập nhật 26/09/2026)

Số kịch bản đã đo được trên 8, theo từng lượt:

| Mô hình | Lượt 1 | Lượt 2 | Lượt 3 | Tổng |
|---|---|---|---|---|
| gemini-3.5-flash-lite | 8/8 | 8/8 | 8/8 | 24/24 |
| gemini-3.6-flash | 8/8 | 8/8 | 4/8 | 20/24 |
| gemini-3.7-flash | 8/8 | 0/8 | 0/8 | 8/24 |
| gemini-3.8-flash | 7/8 | 0/8 | 0/8 | 7/24 |

## Các tệp theo ngày đo

| Ngày | Tệp |
|---|---|
| 24/09 | `lan1_gemini-3.5-flash-lite`, `lan1_gemini-3.6-flash`, `lan1_gemini-3.7-flash`, `lan1_gemini-3.8-flash`, `lan1_gemini-3.8-flash_tiep` |
| 25/09 | `lan1_gemini-3.6-flash_tiep1`, `lan1_gemini-3.7-flash_tiep1`, `lan1_gemini-3.8-flash_tiep2`, `lan2_gemini-3.5-flash-lite`, `lan2_gemini-3.6-flash`, `lan3_gemini-3.5-flash-lite` |
| 26/09 | `lan1_gemini-3.7-flash_tiep2`, `lan1_gemini-3.8-flash_tiep3`, `lan2_gemini-3.6-flash_tiep1`, `lan2_gemini-3.7-flash` (hết hạn mức ngay kịch bản đầu, chưa đo được gì), `lan3_gemini-3.6-flash` |

## Cấu trúc một tệp

Các trường ở mức tệp gồm `model`, `bat_dau_utc`, `ket_thuc_utc`, `git_commit`, `sha256_huong_dan_he_thong`, `nguong_so_tu` (ngưỡng độ dài 220 từ), `so_lan_goi_api` và `dung_som` (lý do dừng sớm, nếu có).

Mảng `ket_qua` có một phần tử cho mỗi kịch bản, gồm các trường:
- `ma`: mã kịch bản S1–S8;
- `nguon`: nguồn câu trả lời (`model` hoặc `du_phong`);
- `da_nhac_sua`, `co_canh_bao_backend`: backend có can thiệp hay không;
- `so_tu_model`: số từ do riêng mô hình viết;
- `phep_kiem`: danh sách phép kiểm, mỗi phép có `mo_ta`, `dat` và `nho_backend` (đạt chỉ nhờ backend can thiệp);
- `tra_loi`: câu trả lời đầy đủ.

Trường `lan` bên trong một tệp là số thứ tự lần chạy trong lần gọi đó (luôn là 1 với cách chạy hiện tại). Số lượt đo nằm ở tên tệp.
