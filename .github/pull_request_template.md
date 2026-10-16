## Thay đổi

<!-- Sửa gì, vì sao. Với bản vá bảo mật: mô tả hành vi trước/sau, KHÔNG viết cách khai thác chi tiết. -->

## Checklist

- [ ] Workflow **Security** xanh cả bốn job (pip-audit, ruff + luật S, gitleaks, verify_task.py)
- [ ] Có kiểm tra mới trong `tests/verify_task.py` cho hành vi vừa sửa
- [ ] Có đột biến đảo ngược bản sửa trong `danh_gia/kiem_thu_dot_bien.py`, và đột biến đó bị bắt
- [ ] `noqa: Sxxx` mới (nếu có) đều ghi lý do ngay tại dòng
- [ ] README đã cập nhật (số kiểm tra, số đột biến, "Hạn chế đã biết")
- [ ] Không có `.env`, khoá, dữ liệu trong `data/` hay ảnh thật trong diff
