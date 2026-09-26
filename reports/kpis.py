"""KPI calculations shared by reports, the dashboard and the AI assistant.

All values are plain int/float/str so the result can be stored as JSON and sent to the model.
"""

from datetime import datetime, time, timedelta

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from inventory.models import StockLevel, StockMovement
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales.models import SalesOrder, SalesOrderLine

SLOW_MOVER_DAYS = 60
MONEY = DecimalField(max_digits=20, decimal_places=2)


def _num(value):
    return round(float(value or 0), 2)


def _pct(part, whole):
    return round(float(part) / float(whole) * 100, 1) if whole else None


def _dt_range(start, end):
    """Aware datetimes [start 00:00, end+1 00:00) so the DB can use indexes (no __date casting)."""
    return (timezone.make_aware(datetime.combine(start, time.min)),
            timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))


def _line_revenue():
    return ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY)


def shipped_lines(start, end, warehouse=None):
    """Lines of orders shipped in [start, end] (dates, inclusive)."""
    since, until = _dt_range(start, end)
    qs = SalesOrderLine.objects.filter(
        order__status=SalesOrder.Status.SHIPPED, order__shipped_at__gte=since, order__shipped_at__lt=until,
    )
    if warehouse is not None:
        qs = qs.filter(order__warehouse=warehouse)
    return qs


def sales_summary(start, end, warehouse=None):
    lines = shipped_lines(start, end, warehouse)
    revenue = lines.aggregate(v=Sum(_line_revenue()))["v"] or 0
    orders = lines.values("order_id").distinct().count()
    since, until = _dt_range(start, end)
    outs = StockMovement.objects.filter(
        movement_type=StockMovement.Type.OUT, occurred_at__gte=since, occurred_at__lt=until,
    )
    if warehouse is not None:
        outs = outs.filter(warehouse=warehouse)
    cogs = -(outs.aggregate(v=Sum(ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=MONEY)))["v"] or 0)
    return {
        "revenue": _num(revenue),
        "cogs": _num(cogs),
        "gross_profit": _num(revenue - cogs),
        "gross_margin_pct": _pct(revenue - cogs, revenue),
        "orders_shipped": orders,
        "avg_order_value": _num(revenue / orders) if orders else 0,
    }


def top_products(start, end, limit=10, warehouse=None, category=None):
    qs = shipped_lines(start, end, warehouse)
    if category:
        qs = qs.filter(product__category__name__icontains=category)
    rows = (
        qs.values("product__sku", "product__name")
        .annotate(qty=Sum("quantity"), revenue=Sum(_line_revenue()))
        .order_by("-revenue")[:limit]
    )
    return [{"sku": r["product__sku"], "name": r["product__name"], "qty": r["qty"],
             "revenue": _num(r["revenue"])} for r in rows]


def revenue_by(start, end, field, label):
    rows = (
        shipped_lines(start, end).values(field).annotate(revenue=Sum(_line_revenue())).order_by("-revenue")
    )
    return [{label: r[field], "revenue": _num(r["revenue"])} for r in rows]


def monthly_revenue(months=12, end=None):
    end = end or timezone.localdate()
    start = (end.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    rows = (
        shipped_lines(start, end)
        .annotate(m=TruncMonth("order__shipped_at"))
        .values("m").annotate(revenue=Sum(_line_revenue())).order_by("m")
    )
    return [{"month": r["m"].strftime("%Y-%m"), "revenue": _num(r["revenue"])} for r in rows]


def inventory_snapshot(warehouse=None):
    levels = StockLevel.objects.select_related("product", "warehouse")
    if warehouse is not None:
        levels = levels.filter(warehouse=warehouse)
    levels = list(levels)
    by_wh = {}
    for lv in levels:
        by_wh[lv.warehouse.code] = by_wh.get(lv.warehouse.code, 0) + float(lv.stock_value)
    below = [lv for lv in levels if 0 < lv.available <= lv.effective_reorder_point]
    out = [lv for lv in levels if lv.available <= 0 and lv.product.is_active]
    return {
        "inventory_value": _num(sum(float(lv.stock_value) for lv in levels)),
        "inventory_value_by_warehouse": {k: _num(v) for k, v in sorted(by_wh.items())},
        "sku_locations": len(levels),
        "below_reorder_point": len(below),
        "out_of_stock": len(out),
        "below_reorder_point_items": [
            {"sku": lv.product.sku, "name": lv.product.name, "warehouse": lv.warehouse.code,
             "available": lv.available, "reorder_point": lv.effective_reorder_point}
            for lv in sorted(below + out, key=lambda lv: lv.available - lv.effective_reorder_point)[:10]
        ],
    }


def slow_movers(as_of, days=SLOW_MOVER_DAYS, limit=10):
    since = as_of - timedelta(days=days)
    last_out = {
        (p, w): last for p, w, last in StockMovement.objects.filter(movement_type=StockMovement.Type.OUT)
        .values("product_id", "warehouse_id").annotate(last=Max("occurred_at"))
        .values_list("product_id", "warehouse_id", "last")
    }
    rows = []
    for lv in StockLevel.objects.filter(on_hand__gt=0).select_related("product", "warehouse"):
        last = last_out.get((lv.product_id, lv.warehouse_id))
        if last is None or timezone.localtime(last).date() < since:
            rows.append({
                "sku": lv.product.sku, "name": lv.product.name, "warehouse": lv.warehouse.code,
                "on_hand": lv.on_hand, "stock_value": _num(lv.stock_value),
                "last_sale": timezone.localtime(last).date().isoformat() if last else None,
            })
    rows.sort(key=lambda r: -r["stock_value"])
    return rows[:limit]


def purchasing_summary(start, end):
    received = PurchaseOrder.objects.filter(
        status=PurchaseOrder.Status.RECEIVED, received_date__gte=start, received_date__lte=end
    )
    late = received.filter(received_date__gt=F("expected_date")).count()
    total = received.count()
    open_pos = PurchaseOrder.objects.filter(status__in=PurchaseOrder.OPEN_STATUSES)
    open_value = PurchaseOrderLine.objects.filter(order__in=open_pos).aggregate(
        v=Sum(ExpressionWrapper((F("quantity") - F("received_quantity")) * F("unit_price"), output_field=MONEY))
    )["v"]
    late_by_supplier = (
        received.filter(received_date__gt=F("expected_date"))
        .values("supplier__code", "supplier__name").annotate(n=Count("id")).order_by("-n")[:5]
    )
    return {
        "po_received": total,
        "po_received_late": late,
        "po_late_rate_pct": _pct(late, total),
        "po_open": open_pos.count(),
        "po_open_value": _num(open_value),
        "po_overdue": open_pos.filter(expected_date__lt=end).count(),
        "po_draft": PurchaseOrder.objects.filter(status=PurchaseOrder.Status.DRAFT).count(),
        "late_suppliers": [{"supplier": f'{r["supplier__code"]} - {r["supplier__name"]}', "late_po": r["n"]}
                           for r in late_by_supplier],
    }


def compute_kpis(start, end):
    """Full KPI set for a period [start, end] (inclusive dates), compared with the previous period."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    current = sales_summary(start, end)
    previous = sales_summary(prev_start, prev_end)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": length},
        "previous_period": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "sales": current,
        "sales_previous": previous,
        "revenue_change_pct": _pct(current["revenue"] - previous["revenue"], previous["revenue"]),
        "revenue_by_warehouse": revenue_by(start, end, "order__warehouse__code", "warehouse"),
        "revenue_by_category": revenue_by(start, end, "product__category__name", "category"),
        "top_products": top_products(start, end),
        "inventory": inventory_snapshot(),
        "slow_movers": slow_movers(end),
        "purchasing": purchasing_summary(start, end),
        "open_sales_orders": SalesOrder.objects.filter(
            status__in=[SalesOrder.Status.DRAFT, SalesOrder.Status.CONFIRMED]
        ).count(),
        "cancelled_sales_orders": SalesOrder.objects.filter(
            Q(status=SalesOrder.Status.CANCELLED), order_date__gte=start, order_date__lte=end
        ).count(),
    }
