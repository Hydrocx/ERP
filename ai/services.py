"""AI features. Numbers always come from the system; the model only explains and recommends."""

from django.utils import timezone

from . import prompts
from .client import AIError, chat_json, get_settings
from .models import AICallLog

REORDER_BATCH = 15


# ------------------------------------------------------------ F1: reorder

def reorder_payload(s):
    fc = s.forecast
    return {
        "id": s.pk,
        "sku": s.product.sku,
        "name": s.product.name,
        "category": s.product.category.name,
        "warehouse": s.warehouse.code,
        "month": timezone.localdate().month,
        "weekly_sales_12w": fc.weekly_sales if fc else [],
        "avg_daily_90d": float(fc.avg_daily) if fc else None,
        "avg_daily_28d": float(fc.recent_daily) if fc else None,
        "seasonal_factor": float(fc.seasonal_factor) if fc else None,
        "forecast_daily": float(fc.forecast_daily) if fc else None,
        "lead_time_days": fc.lead_time_days if fc else None,
        "safety_stock": fc.safety_stock if fc else None,
        "reorder_point": s.reorder_point,
        "available": s.available,
        "on_order": s.on_order,
        "stockout_days_in_window": fc.stockout_days if fc else 0,
        "suggested_qty": s.suggested_qty,
    }


def apply_guardrail(suggestion, item, max_adjust_pct):
    """Copy an AI answer onto a suggestion, refusing adjustments outside the allowed band."""
    action = item.get("action", "review")
    qty = max(0, int(item.get("adjusted_qty", suggestion.suggested_qty)))
    band = suggestion.suggested_qty * max_adjust_pct / 100
    suggestion.ai_action = action
    suggestion.ai_confidence = item.get("confidence", "low")
    suggestion.ai_reason = (item.get("reason") or "").strip()[:1000]
    suggestion.ai_adjusted_qty = qty
    suggestion.ai_flagged = False
    if action == "order" and abs(qty - suggestion.suggested_qty) > band:
        suggestion.ai_flagged = True
        suggestion.ai_action = "review"
        suggestion.ai_reason += (
            f" [Guardrail: AI đề xuất {qty}, lệch quá {max_adjust_pct}% so với {suggestion.suggested_qty}"
            " của hệ thống; giữ số của hệ thống, cần người duyệt xem lại.]"
        )
    return suggestion


def explain_reorder(suggestions, user=None):
    cfg = get_settings()
    fields = ["ai_action", "ai_confidence", "ai_reason", "ai_adjusted_qty", "ai_flagged", "updated_at"]
    suggestions = [s for s in suggestions]
    done = 0
    for i in range(0, len(suggestions), REORDER_BATCH):
        batch = suggestions[i:i + REORDER_BATCH]
        result = chat_json(AICallLog.Feature.REORDER, prompts.REORDER_SYSTEM,
                           {"items": [reorder_payload(s) for s in batch]}, prompts.REORDER_SCHEMA,
                           schema_name="reorder_review", user=user, feature_flag="enable_reorder")
        answers = {item["id"]: item for item in result.get("items", []) if isinstance(item, dict)}
        for s in batch:
            if s.pk in answers:
                apply_guardrail(s, answers[s.pk], cfg.max_adjust_pct)
                s.save(update_fields=fields)
                done += 1
    return done


# ------------------------------------------------------ product analysis

def analyze_product(product, forecasts, user=None):
    payload = {
        "sku": product.sku, "name": product.name, "category": product.category.name,
        "unit": product.get_unit_display(), "today": timezone.localdate().isoformat(),
        "warehouses": [
            {
                "warehouse": fc.warehouse.code,
                "weekly_sales_12w": fc.weekly_sales,
                "avg_daily_90d": float(fc.avg_daily),
                "avg_daily_28d": float(fc.recent_daily),
                "seasonal_factor": float(fc.seasonal_factor),
                "forecast_daily": float(fc.forecast_daily),
                "available": fc.available, "on_order": fc.on_order,
                "reorder_point": fc.reorder_point, "safety_stock": fc.safety_stock,
                "days_of_cover": fc.days_of_cover, "stockout_days_in_window": fc.stockout_days,
            }
            for fc in forecasts
        ],
    }
    return chat_json(AICallLog.Feature.PRODUCT, prompts.PRODUCT_SYSTEM, payload, prompts.PRODUCT_SCHEMA,
                     schema_name="product_analysis", user=user, feature_flag="enable_reorder")


# ------------------------------------------------------------- F2: report

def write_report(kpis, user=None):
    result = chat_json(AICallLog.Feature.REPORT, prompts.REPORT_SYSTEM, kpis, prompts.REPORT_SCHEMA,
                       schema_name="weekly_report", user=user, feature_flag="enable_report")
    if not result.get("summary"):
        raise AIError("Báo cáo AI thiếu phần tóm tắt")
    return result


# ----------------------------------------------------- F4: supplier invoices

INVOICE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
INVOICE_MAX_BYTES = 5 * 1024 * 1024


def extract_invoice(image_bytes, mime_type, user=None):
    """Read a supplier invoice image with a vision-capable model. Returns the INVOICE_SCHEMA dict."""
    import base64

    from .client import chat_json_messages

    if mime_type not in INVOICE_MIME_TYPES:
        raise AIError("Chỉ hỗ trợ ảnh JPG, PNG, WebP hoặc GIF.")
    if len(image_bytes) > INVOICE_MAX_BYTES:
        raise AIError("Ảnh vượt quá 5 MB.")
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
    messages = [
        {"role": "system", "content": prompts.INVOICE_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "Trích xuất dữ liệu hóa đơn trong ảnh."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]},
    ]
    return chat_json_messages(AICallLog.Feature.INVOICE, messages, prompts.INVOICE_SCHEMA,
                              schema_name="supplier_invoice", user=user, feature_flag="enable_invoice")
