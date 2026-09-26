import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Min, Sum
from django.utils import timezone

from ai.client import AIError, AIUnavailable, get_settings
from catalog.models import Product, Warehouse
from inventory.models import StockLevel, StockMovement
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales.models import SalesOrder, SalesOrderLine

from . import engine
from .models import DemandForecast, ReorderSuggestion

logger = logging.getLogger(__name__)

DEMAND_STATUSES = (SalesOrder.Status.CONFIRMED, SalesOrder.Status.SHIPPED)


def _dec(value, places=3):
    return Decimal(str(round(value, places)))


def daily_sales(start, end, product=None):
    """{(product_id, warehouse_id): {date: qty}} for confirmed/shipped orders in [start, end)."""
    qs = SalesOrderLine.objects.filter(
        order__status__in=DEMAND_STATUSES, order__order_date__gte=start, order__order_date__lt=end
    )
    if product is not None:
        qs = qs.filter(product=product)
    rows = qs.values("product_id", "order__warehouse_id", "order__order_date").annotate(q=Sum("quantity"))
    result = defaultdict(dict)
    for r in rows:
        result[(r["product_id"], r["order__warehouse_id"])][r["order__order_date"]] = r["q"]
    return result


def on_order_map():
    rows = (
        PurchaseOrderLine.objects.filter(order__status__in=PurchaseOrder.OPEN_STATUSES)
        .values("product_id", "order__warehouse_id")
        .annotate(q=Sum(F("quantity") - F("received_quantity")))
    )
    return {(r["product_id"], r["order__warehouse_id"]): r["q"] for r in rows}


def stockout_map(start, end):
    movements = defaultdict(list)
    since = timezone.make_aware(datetime.combine(start, time.min))
    until = timezone.make_aware(datetime.combine(end, time.min))
    qs = (
        StockMovement.objects.filter(occurred_at__gte=since, occurred_at__lt=until)
        .order_by("occurred_at", "id")
        .values_list("product_id", "warehouse_id", "occurred_at", "quantity", "balance_after")
    )
    for pid, wid, at, qty, bal in qs:
        movements[(pid, wid)].append((timezone.localtime(at).date(), qty, bal))
    return {key: engine.stockout_days(rows, start, end) for key, rows in movements.items()}


def history_start():
    return SalesOrder.objects.aggregate(d=Min("order_date"))["d"]


def compute_forecasts(as_of=None, products=None):
    """Compute (without saving) forecast rows for every active product x warehouse."""
    cfg = get_settings()
    as_of = as_of or timezone.localdate()
    window = cfg.forecast_window_days
    start_hist = history_start()
    # Need last year's window too when history allows it
    load_from = as_of - timedelta(days=max(window, 365 + engine.RECENT_DAYS))
    products = list(products if products is not None
                    else Product.objects.filter(is_active=True).select_related("default_supplier"))
    warehouses = list(Warehouse.objects.filter(is_active=True))
    sales = daily_sales(load_from, as_of, product=products[0] if len(products) == 1 else None)
    levels = {(lv.product_id, lv.warehouse_id): lv for lv in StockLevel.objects.all()}
    on_order = on_order_map()
    stockouts = stockout_map(as_of - timedelta(days=window), as_of)

    rows = []
    for product in products:
        lead = product.effective_lead_time
        for wh in warehouses:
            key = (product.pk, wh.pk)
            stats = engine.demand_stats(sales.get(key, {}), as_of, history_start=start_hist, window=window,
                                        horizon=lead + cfg.review_period_days)
            policy = engine.inventory_policy(stats, lead, cfg.review_period_days, cfg.service_level)
            level = levels.get(key)
            rows.append(DemandForecast(
                run_date=as_of, product=product, warehouse=wh, window_days=window,
                avg_daily=_dec(stats.avg_daily), recent_daily=_dec(stats.recent_daily),
                std_daily=_dec(stats.std_daily), seasonal_factor=_dec(stats.seasonal_factor),
                forecast_daily=_dec(stats.forecast_daily), lead_time_days=lead,
                safety_stock=policy.safety_stock, reorder_point=policy.reorder_point,
                order_up_to=policy.order_up_to,
                available=level.available if level else 0, on_order=on_order.get(key, 0),
                stockout_days=stockouts.get(key, 0), weekly_sales=stats.weekly,
            ))
    return rows


@transaction.atomic
def run_forecast(as_of=None, use_ai=True, user=None):
    """Recompute forecasts, update per-warehouse ROP and create reorder suggestions."""
    as_of = as_of or timezone.localdate()
    rows = compute_forecasts(as_of)
    DemandForecast.objects.filter(run_date=as_of).delete()
    forecasts = DemandForecast.objects.bulk_create(rows)

    levels = {(lv.product_id, lv.warehouse_id): lv for lv in StockLevel.objects.all()}
    changed = []
    for fc in forecasts:
        level = levels.get((fc.product_id, fc.warehouse_id))
        if level and (level.reorder_point, level.safety_stock) != (fc.reorder_point, fc.safety_stock):
            level.reorder_point, level.safety_stock = fc.reorder_point, fc.safety_stock
            changed.append(level)
    StockLevel.objects.bulk_update(changed, ["reorder_point", "safety_stock"])

    expired = ReorderSuggestion.objects.filter(status=ReorderSuggestion.Status.PENDING).update(
        status=ReorderSuggestion.Status.EXPIRED
    )
    suggestions = []
    for fc in forecasts:
        qty = engine.suggested_quantity(fc.available, fc.on_order, engine.Policy(
            fc.safety_stock, fc.reorder_point, fc.order_up_to))
        if qty <= 0 or not fc.product.default_supplier_id:
            continue
        suggestions.append(ReorderSuggestion(
            forecast=fc, product=fc.product, warehouse=fc.warehouse, supplier=fc.product.default_supplier,
            available=fc.available, on_order=fc.on_order, reorder_point=fc.reorder_point, suggested_qty=qty,
            ai_reason=rule_based_reason(fc, qty),
        ))
    suggestions = ReorderSuggestion.objects.bulk_create(suggestions)

    explained, ai_error = 0, ""
    if use_ai and suggestions:
        from ai.services import explain_reorder
        try:
            explained = explain_reorder(suggestions, user=user)
        except (AIUnavailable, AIError) as exc:
            ai_error = str(exc)
            logger.info("Reorder AI skipped: %s", exc)
    return {
        "run_date": as_of, "forecasts": len(forecasts), "suggestions": len(suggestions),
        "expired": expired, "ai_explained": explained, "ai_error": ai_error,
    }


def rule_based_reason(fc, qty):
    reason = (
        f"Tồn khả dụng {fc.available} + đang về {fc.on_order} ≤ ROP {fc.reorder_point}. "
        f"Dự báo {float(fc.forecast_daily):.1f}/ngày, lead time {fc.lead_time_days} ngày; "
        f"đặt {qty} để về mức {fc.order_up_to}."
    )
    if fc.stockout_days:
        reason += f" Đã hết hàng {fc.stockout_days} ngày trong kỳ nên nhu cầu thực có thể cao hơn."
    return reason


@transaction.atomic
def approve_suggestions(decisions, user=None):
    """decisions: list of (suggestion, qty). Creates one draft PO per supplier x warehouse."""
    groups = defaultdict(list)
    for suggestion, qty in decisions:
        if suggestion.status != ReorderSuggestion.Status.PENDING or qty <= 0:
            continue
        groups[(suggestion.supplier, suggestion.warehouse)].append((suggestion, qty))

    today = timezone.localdate()
    orders = []
    for (supplier, warehouse), items in groups.items():
        po = PurchaseOrder(
            supplier=supplier, warehouse=warehouse, order_date=today,
            expected_date=today + timedelta(days=supplier.lead_time_days),
            source=PurchaseOrder.Source.AI, created_by=user,
            note="Tạo từ đề xuất nhập hàng: " + ", ".join(s.product.sku for s, _ in items),
        )
        po.lines = [PurchaseOrderLine(product=s.product, quantity=q, unit_price=s.product.cost_price)
                    for s, q in items]
        po.save()
        now = timezone.now()
        for s, q in items:
            s.status, s.final_qty, s.purchase_order = ReorderSuggestion.Status.APPROVED, q, po
            s.decided_by, s.decided_at = user, now
            s.save(update_fields=["status", "final_qty", "purchase_order", "decided_by", "decided_at",
                                  "updated_at"])
        orders.append(po)
    return orders


def reject_suggestions(suggestions, user=None):
    ids = [s.pk for s in suggestions if s.status == ReorderSuggestion.Status.PENDING]
    return ReorderSuggestion.objects.filter(pk__in=ids).update(
        status=ReorderSuggestion.Status.REJECTED, decided_by=user, decided_at=timezone.now()
    )


def latest_forecasts(product):
    last = DemandForecast.objects.filter(product=product).order_by("-run_date").values_list(
        "run_date", flat=True).first()
    if not last:
        return []
    return list(DemandForecast.objects.filter(product=product, run_date=last).select_related("warehouse"))
