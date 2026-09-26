# Hướng dẫn sử dụng

Hệ thống có hai giao diện:

- **Ứng dụng ERP** (`/app/`): dashboard, tồn kho, đề xuất nhập hàng, báo cáo, trợ lý AI — dùng hằng ngày.
- **Quản trị Wagtail** (`/admin/`): nhập liệu danh mục, tạo và xử lý chứng từ, duyệt báo cáo, cấu hình.

Đăng nhập tại `/accounts/login/`. Menu chỉ hiện những chức năng bạn có quyền.

## Quản lý

1. **Dashboard** (`/app/`): doanh thu, lợi nhuận gộp tháng này, giá trị tồn, số vị trí hết hàng / dưới ROP,
   biểu đồ 12 tháng, top sản phẩm, tóm tắt AI của báo cáo mới nhất.
2. **Duyệt PO**: Quản trị → *Mua hàng* → lọc *Trạng thái = Nháp* → mở PO → **Duyệt PO** (hoặc **Hủy PO**).
   PO có *Nguồn tạo = Đề xuất AI* là do bộ phận mua hàng duyệt từ màn hình đề xuất.
3. **Báo cáo**: `/app/reports/` → chọn kỳ → **Tạo báo cáo**. Hệ thống tính KPI, AI viết nhận xét, tạo trang nháp và gửi
   vào quy trình duyệt. Vào link *Nháp chờ duyệt* (hoặc Quản trị → *Trang → Báo cáo*), đọc mục **Kiểm soát AI**:
   nếu có *Cảnh báo kiểm tra số liệu*, sửa lại câu văn tương ứng, sau đó **Phê duyệt và xuất bản**.
4. **Cấu hình AI**: Quản trị → *Cài đặt → Cấu hình AI & dự báo* (bật/tắt từng tính năng, model, biên điều chỉnh của AI,
   mức phục vụ). Xem chi phí/lỗi tại *AI & Dự báo → Nhật ký gọi AI*.

## Mua hàng

1. `/app/reorder/` → **Chạy dự báo ngay** (hệ thống cũng tự chạy mỗi đêm khi có scheduler).
2. Mỗi dòng có: tồn khả dụng, hàng đang về, ROP, **SL hệ thống**, **khuyến nghị AI** (Nên đặt / Chờ thêm / Cần xem lại
   kèm lý do) và ô **SL duyệt** (mặc định = SL AI nếu hợp lệ, ngược lại = SL hệ thống).
   - Nhãn *Cần xem lại* màu vàng = AI muốn thay đổi quá biên cho phép, hệ thống giữ số của công thức.
3. Tick các dòng → sửa SL nếu cần → **Duyệt → tạo PO nháp** (gom theo NCC × kho) hoặc **Từ chối**.
4. Tạo PO thủ công: Quản trị → *Mua hàng* → **Thêm** → chọn NCC, kho, thêm dòng hàng.
5. **Nhập từ hóa đơn NCC** (`/app/invoice/`): chọn ảnh hóa đơn → **Đọc hóa đơn bằng AI** → kiểm tra mục *Cần kiểm tra*
   (dòng không khớp sản phẩm, đơn giá lệch, tổng tiền lệch) → chọn/sửa sản phẩm, số lượng, đơn giá, chọn NCC và kho nhận
   → **Xác nhận → tạo PO nháp**. Dòng để "bỏ qua" (ví dụ phí vận chuyển) không được đưa vào PO.

## Thủ kho

1. **Nhận hàng**: Quản trị → *Mua hàng* → mở PO *Đã duyệt / Nhận một phần* → **Nhận hàng** → nhập số lượng thực nhận từng dòng
   (có thể nhận một phần) → **Xác nhận nhập kho**.
2. **Giao hàng**: Quản trị → *Bán hàng* → mở SO *Đã xác nhận* → **Xuất kho giao hàng**.
3. **Kiểm kê / chuyển kho**: Quản trị → *Kho → Tồn kho* → **Điều chỉnh tồn** (nhập số đếm thực tế + lý do) hoặc **Chuyển kho**.
4. **Lịch sử xuất nhập**: *Kho → Lịch sử xuất nhập* (lọc theo loại, kho; xuất CSV/Excel).

## Bán hàng

1. Quản trị → *Bán hàng* → **Thêm**: chọn khách hàng, kho xuất, thêm sản phẩm (để trống đơn giá = giá bán niêm yết).
2. Mở SO → **Xác nhận & giữ hàng**. Nếu một dòng không đủ hàng, cả đơn không được xác nhận và hệ thống báo dòng thiếu.
3. Xem tồn khả dụng trước khi chốt đơn: `/app/inventory/` (lọc theo kho, danh mục, tình trạng).

## Trợ lý AI (`/app/assistant/`)

Ví dụ câu hỏi:

- "Những mặt hàng nào đang hết hàng ở kho HCM?"
- "Doanh thu tháng 8 so với tháng 7?"
- "Top 5 sản phẩm bán chạy danh mục Đồ uống 30 ngày qua"
- "PO-202609-0012 đã nhận hàng chưa?"

Dưới mỗi câu trả lời có dòng *Nguồn dữ liệu* cho biết các hàm tra cứu đã dùng. Trợ lý chỉ đọc dữ liệu,
không tạo/sửa chứng từ; câu hỏi ngoài phạm vi sẽ được trả lời là không trả lời được.

## Chi tiết sản phẩm (`/app/products/<SKU>/`)

Biểu đồ doanh số 26 tuần + đường dự báo 4 tuần, tồn theo kho, dự báo gần nhất (hệ số mùa vụ, tồn an toàn, số ngày đủ bán),
30 phiếu xuất nhập gần nhất và nút **Phân tích AI** (tóm tắt rủi ro + hành động đề xuất).
