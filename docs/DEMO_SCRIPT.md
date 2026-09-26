# Kịch bản demo (5 phút)

Bản quay tự động (không lời, có phụ đề): [demo/demo.mp4](demo/demo.mp4), tạo bằng `scripts/record_demo.py`.
Kịch bản dưới đây dùng khi trình bày trực tiếp có thuyết minh.

Chuẩn bị: đã chạy `seed_data`, `setup_roles --demo-users`, `run_forecast`; có `OPENAI_API_KEY` trong `.env`
(nếu không có, hệ thống vẫn chạy với phương án dự phòng — nên nói rõ khi demo).

| Thời gian | Màn hình | Nội dung nói / thao tác |
|---|---|---|
| 0:00 | `/admin/` (admin) | Giới thiệu Wagtail admin: menu Danh mục, Kho, Mua hàng, Bán hàng, AI & Dự báo; panel cảnh báo tồn kho ở trang chủ |
| 0:40 | Danh mục → Sản phẩm | Model tùy chỉnh (Snippet), bộ lọc, tìm kiếm, xuất CSV. Mở 1 sản phẩm: chính sách tồn kho |
| 1:10 | Bán hàng → Thêm SO | Tạo SO 2 dòng → Lưu → **Xác nhận & giữ hàng** → **Xuất kho giao hàng**; mở *Kho → Lịch sử xuất nhập* thấy phiếu OUT |
| 1:50 | `/app/` (quanly) | Dashboard KPI, biểu đồ, top sản phẩm, sản phẩm cần chú ý |
| 2:20 | `/app/reorder/` | **Chạy dự báo ngay** → giải thích cột Hệ thống vs AI, nhãn guardrail, lý do AI → chọn 3 dòng → **Duyệt → tạo PO nháp** |
| 3:00 | Admin → Mua hàng | Mở PO nguồn *Đề xuất AI* → **Duyệt PO** → **Nhận hàng** một phần → tồn kho tăng |
| 3:30 | `/app/products/MUT-TET/` | Biểu đồ mùa vụ Tết + **Phân tích AI** |
| 3:50 | `/app/reports/` | **Tạo báo cáo** tuần trước → mở nháp trong admin → mục *Kiểm soát AI* → Phê duyệt & xuất bản → xem trang báo cáo |
| 4:20 | `/app/invoice/` | Tải ảnh hóa đơn NCC → AI đọc → xem cảnh báo khớp dữ liệu → xác nhận → PO nháp |
| 4:30 | `/app/assistant/` | Hỏi "Những mặt hàng nào đang hết hàng ở kho HCM?" → chỉ ra *Nguồn dữ liệu* (function calling) |
| 4:50 | Admin → Nhật ký gọi AI | Token, thời gian, lỗi của từng lần gọi; Cài đặt → Cấu hình AI & dự báo |
