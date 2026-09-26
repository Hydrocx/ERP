"""Data assistant (F3) using OpenAI function calling over predefined, read-only tools.

The model never writes SQL: it can only call the functions below, which return
numbers computed by the ORM.
"""

import json
from datetime import date

from django.db.models import Q
from django.utils import timezone

from . import prompts
from .client import AIError, chat
from .models import AICallLog

MAX_TOOL_ROUNDS = 5
MAX_HISTORY = 10


def _date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _warehouse(code):
    from catalog.models import Warehouse

    return Warehouse.objects.filter(code__iexact=code).first() if code else None


# ---------------------------------------------------------------- tools

def tool_get_stock(product_query, warehouse_code=None):
    from inventory.models import StockLevel

    levels = StockLevel.objects.select_related("product", "warehouse").filter(
        Q(product__sku__icontains=product_query) | Q(product__name__icontains=product_query)
    )
    if warehouse_code:
        levels = levels.filter(warehouse__code__iexact=warehouse_code)
    return [
        {"sku": lv.product.sku, "name": lv.product.name, "warehouse": lv.warehouse.code,
         "on_hand": lv.on_hand, "reserved": lv.reserved, "available": lv.available,
         "reorder_point": lv.effective_reorder_point, "status": lv.status}
        for lv in levels[:30]
    ]


def tool_sales_summary(start_date=None, end_date=None, warehouse_code=None):
    from reports.kpis import sales_summary

    today = timezone.localdate()
    start = _date(start_date, today.replace(day=1))
    end = _date(end_date, today)
    return {"start": start.isoformat(), "end": end.isoformat(), "warehouse": warehouse_code or "ALL",
            **sales_summary(start, end, _warehouse(warehouse_code))}


def tool_top_products(start_date=None, end_date=None, limit=10, warehouse_code=None, category=None):
    from reports.kpis import top_products

    today = timezone.localdate()
    start = _date(start_date, today.replace(day=1))
    end = _date(end_date, today)
    return {"start": start.isoformat(), "end": end.isoformat(),
            "items": top_products(start, end, min(int(limit or 10), 50), _warehouse(warehouse_code), category)}


def tool_low_stock_items(warehouse_code=None, limit=20):
    from inventory.models import StockLevel

    levels = StockLevel.objects.select_related("product", "warehouse").filter(product__is_active=True)
    if warehouse_code:
        levels = levels.filter(warehouse__code__iexact=warehouse_code)
    rows = [lv for lv in levels if lv.available <= lv.effective_reorder_point]
    rows.sort(key=lambda lv: lv.available - lv.effective_reorder_point)
    return [{"sku": lv.product.sku, "name": lv.product.name, "warehouse": lv.warehouse.code,
             "available": lv.available, "reorder_point": lv.effective_reorder_point, "status": lv.status}
            for lv in rows[: min(int(limit or 20), 100)]]


def tool_purchase_orders(code=None, status=None, supplier=None, limit=20):
    from purchasing.models import PurchaseOrder

    qs = PurchaseOrder.objects.select_related("supplier", "warehouse").prefetch_related("lines__product")
    if code:
        qs = qs.filter(code__iexact=code)
    elif status:
        qs = qs.filter(status=status.upper())
    else:
        qs = qs.filter(status__in=PurchaseOrder.OPEN_STATUSES)
    if supplier:
        qs = qs.filter(Q(supplier__code__iexact=supplier) | Q(supplier__name__icontains=supplier))
    return [
        {"code": po.code, "supplier": str(po.supplier), "warehouse": po.warehouse.code,
         "status": po.get_status_display(), "order_date": po.order_date.isoformat(),
         "expected_date": po.expected_date.isoformat() if po.expected_date else None,
         "is_late": po.is_late, "total": float(po.total_amount),
         "lines": [{"sku": ln.product.sku, "qty": ln.quantity, "received": ln.received_quantity}
                   for ln in po.lines.all()]}
        for po in qs[: min(int(limit or 20), 50)]
    ]


def tool_reorder_suggestions(warehouse_code=None):
    from forecast.models import ReorderSuggestion

    qs = ReorderSuggestion.objects.filter(status=ReorderSuggestion.Status.PENDING).select_related(
        "product", "warehouse", "supplier")
    if warehouse_code:
        qs = qs.filter(warehouse__code__iexact=warehouse_code)
    return [{"sku": s.product.sku, "name": s.product.name, "warehouse": s.warehouse.code,
             "supplier": s.supplier.code, "recommended_qty": s.recommended_qty, "reason": s.ai_reason}
            for s in qs[:50]]


def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required)},
    }}


DATE = {"type": "string", "description": "YYYY-MM-DD"}
WH = {"type": "string", "description": "Mã kho: HN, HCM, DN"}

TOOLS = [
    _fn("get_stock", "Tồn kho của sản phẩm (tìm theo mã SKU hoặc tên, không phân biệt hoa thường).",
        {"product_query": {"type": "string"}, "warehouse_code": WH}, ["product_query"]),
    _fn("sales_summary", "Doanh thu, giá vốn, lợi nhuận gộp, số đơn đã giao trong khoảng ngày.",
        {"start_date": DATE, "end_date": DATE, "warehouse_code": WH}),
    _fn("top_products", "Sản phẩm bán chạy nhất theo doanh thu trong khoảng ngày.",
        {"start_date": DATE, "end_date": DATE, "limit": {"type": "integer"}, "warehouse_code": WH,
         "category": {"type": "string", "description": "Tên danh mục, ví dụ 'Đồ uống'"}}),
    _fn("low_stock_items", "Các sản phẩm hết hàng hoặc dưới điểm đặt hàng lại (ROP).",
        {"warehouse_code": WH, "limit": {"type": "integer"}}),
    _fn("purchase_orders", "Tra cứu đơn mua hàng: theo mã PO, trạng thái (DRAFT, APPROVED, PARTIAL, RECEIVED, "
        "CANCELLED) hoặc NCC. Mặc định trả về các PO đang chờ nhận hàng.",
        {"code": {"type": "string"}, "status": {"type": "string"}, "supplier": {"type": "string"},
         "limit": {"type": "integer"}}),
    _fn("reorder_suggestions", "Các đề xuất nhập hàng đang chờ duyệt.", {"warehouse_code": WH}),
]

TOOL_FUNCS = {
    "get_stock": tool_get_stock, "sales_summary": tool_sales_summary, "top_products": tool_top_products,
    "low_stock_items": tool_low_stock_items, "purchase_orders": tool_purchase_orders,
    "reorder_suggestions": tool_reorder_suggestions,
}


def run_tool(name, arguments):
    func = TOOL_FUNCS.get(name)
    if func is None:
        return {"error": f"Không có công cụ {name}"}
    try:
        args = json.loads(arguments or "{}")
        return func(**{k: v for k, v in args.items() if v not in (None, "")})
    except (TypeError, ValueError) as exc:
        return {"error": f"Tham số không hợp lệ: {exc}"}


def ask(question, history=(), user=None):
    """Answer a question. Returns (answer_text, tools_used)."""
    messages = [{"role": "system", "content": prompts.ASSISTANT_SYSTEM.format(today=timezone.localdate())}]
    messages += list(history)[-MAX_HISTORY:]
    messages.append({"role": "user", "content": question})
    used = []
    for _ in range(MAX_TOOL_ROUNDS):
        message = chat(AICallLog.Feature.ASSISTANT, messages, tools=TOOLS, user=user,
                       feature_flag="enable_assistant")
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            return (message.content or "").strip(), used
        messages.append({
            "role": "assistant", "content": message.content or "",
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in calls],
        })
        for c in calls:
            used.append(c.function.name)
            result = run_tool(c.function.name, c.function.arguments)
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": json.dumps(result, ensure_ascii=False, default=str)})
    raise AIError("Trợ lý gọi công cụ quá nhiều lần mà chưa trả lời được.")
