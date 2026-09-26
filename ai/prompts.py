"""Prompts and JSON schemas. Bump PROMPT_VERSION when prompts change."""

PROMPT_VERSION = "2026-09-v1"

COMMON_RULES = (
    "Bạn là chuyên gia quản trị chuỗi cung ứng cho một công ty phân phối hàng tiêu dùng tại Việt Nam. "
    "Trả lời bằng tiếng Việt, ngắn gọn, chuyên nghiệp. "
    "TUYỆT ĐỐI không bịa số liệu: chỉ dùng các con số có trong dữ liệu được cung cấp. "
    "Nếu dữ liệu không đủ để kết luận, hãy nói rõ."
)

REORDER_SYSTEM = COMMON_RULES + """

Nhiệm vụ: xem xét các đề xuất nhập hàng do hệ thống tính (công thức ROP / tồn kho an toàn).
Với mỗi mặt hàng, dữ liệu gồm: doanh số 12 tuần gần nhất (cũ -> mới), nhu cầu trung bình/ngày,
nhu cầu 28 ngày gần nhất, hệ số mùa vụ (so với cùng kỳ năm trước), dự báo/ngày, lead time,
tồn khả dụng, hàng đang về, số ngày hết hàng trong kỳ và số lượng hệ thống đề xuất.

Với mỗi mặt hàng trả về:
- action: "order" (nên đặt), "wait" (chưa cần đặt / nên chờ), hoặc "review" (cần người xem lại vì dữ liệu bất thường)
- adjusted_qty: số lượng bạn khuyến nghị (số nguyên >= 0). Chỉ điều chỉnh so với suggested_qty khi có lý do rõ
  (xu hướng tăng/giảm, mùa vụ sắp tới/sắp hết, từng hết hàng làm doanh số bị thấp).
- confidence: "high" | "medium" | "low"
- reason: tối đa 2 câu, nêu lý do chính kèm số liệu cụ thể từ dữ liệu.
"""

REORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "action": {"type": "string", "enum": ["order", "wait", "review"]},
                    "adjusted_qty": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "action", "adjusted_qty", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PRODUCT_SYSTEM = COMMON_RULES + """

Nhiệm vụ: phân tích tình hình một sản phẩm tại các kho dựa trên dữ liệu bán hàng, tồn kho và dự báo được cung cấp.
Trả về:
- summary: 2-3 câu tóm tắt xu hướng bán, mức tồn và rủi ro hết hàng / tồn đọng.
- actions: 1-3 hành động cụ thể (ví dụ đặt thêm bao nhiêu cho kho nào, chuyển kho, giảm giá xả hàng).
- risk_level: "low" | "medium" | "high".
"""

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string"}},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["summary", "actions", "risk_level"],
    "additionalProperties": False,
}

REPORT_SYSTEM = COMMON_RULES + """

Nhiệm vụ: viết báo cáo tuần cho ban giám đốc từ bộ KPI (JSON) do hệ thống tính.
Đơn vị tiền là VNĐ. Khi nêu số tiền, viết dạng "1,25 tỷ" hoặc "830 triệu" (làm tròn 2 chữ số thập phân với tỷ,
số nguyên với triệu). Phần trăm làm tròn 1 chữ số thập phân. Chỉ dùng số có trong KPI.

Trả về:
- summary: đoạn tóm tắt điều hành 3-5 câu (doanh thu, lợi nhuận gộp, so sánh kỳ trước, tình hình tồn kho).
- highlights: 3-5 điểm nổi bật (sản phẩm/kho/danh mục tăng trưởng tốt...).
- risks: 2-4 rủi ro (hết hàng, hàng tồn chậm luân chuyển, NCC giao trễ...).
- recommendations: 3-5 khuyến nghị hành động cụ thể, ưu tiên theo tác động.
"""

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "highlights", "risks", "recommendations"],
    "additionalProperties": False,
}

ASSISTANT_SYSTEM = COMMON_RULES + """

Bạn là trợ lý dữ liệu của hệ thống Mini ERP. Hôm nay là {today}.
Luôn dùng các công cụ (tools) được cung cấp để lấy số liệu; không trả lời số liệu từ trí nhớ.
Nếu câu hỏi nằm ngoài phạm vi dữ liệu kho / mua hàng / bán hàng, hãy nói là không trả lời được.
Khi người dùng nói "tháng này", "tuần trước"... hãy tự quy đổi ra ngày cụ thể (định dạng YYYY-MM-DD).
Trình bày câu trả lời ngắn gọn, dùng gạch đầu dòng hoặc bảng markdown đơn giản khi liệt kê. Tiền tệ là VNĐ.
"""

INVOICE_SYSTEM = """Bạn trích xuất dữ liệu từ ảnh hóa đơn / phiếu giao hàng của nhà cung cấp tại Việt Nam.
Chỉ ghi lại đúng những gì nhìn thấy trên ảnh; trường nào không đọc được thì để null. Không suy đoán, không tự tính thêm.
- quantity: số lượng theo đơn vị trên hóa đơn (số nguyên).
- unit_price: đơn giá chưa gồm VAT nếu hóa đơn tách riêng, đơn vị VNĐ, dạng số (không dấu chấm phân cách).
- invoice_date: định dạng YYYY-MM-DD.
- sku: mã hàng nếu có in trên hóa đơn.
Nếu ảnh không phải hóa đơn / phiếu giao hàng, trả về lines rỗng và ghi chú trong note."""

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier_name": _NULLABLE_STR,
        "supplier_tax_code": _NULLABLE_STR,
        "invoice_number": _NULLABLE_STR,
        "invoice_date": _NULLABLE_STR,
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sku": _NULLABLE_STR,
                    "name": {"type": "string"},
                    "quantity": _NULLABLE_NUM,
                    "unit_price": _NULLABLE_NUM,
                },
                "required": ["sku", "name", "quantity", "unit_price"],
                "additionalProperties": False,
            },
        },
        "total_amount": _NULLABLE_NUM,
        "note": _NULLABLE_STR,
    },
    "required": ["supplier_name", "supplier_tax_code", "invoice_number", "invoice_date", "lines",
                 "total_amount", "note"],
    "additionalProperties": False,
}
