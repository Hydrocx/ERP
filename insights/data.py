"""Lấy dữ liệu từ ORM thành mảng numpy cho các mô hình (không phụ thuộc loại CSDL).

Ngày được tính theo múi giờ của dự án (Asia/Ho_Chi_Minh), giống các view analytics nên số liệu khớp
với dashboard Superset.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from catalog.models import Customer
from sales.models import SalesOrder, SalesOrderLine

MONEY = DecimalField(max_digits=20, decimal_places=2)
SHIPPED = SalesOrder.Status.SHIPPED

#: Số ngày gần nhất bị bỏ qua vì đơn chưa kịp giao xong (đơn mở, giao trễ) làm doanh thu bị thấp giả.
RECENT_LAG_DAYS = 2


def default_as_of(today=None, lag_days=RECENT_LAG_DAYS):
    """Chủ nhật gần nhất trước `today - lag_days`, để các tuần dự báo khớp tuần ISO (Thứ Hai – Chủ nhật)."""
    day = (today or timezone.localdate()) - timedelta(days=lag_days)
    return day - timedelta(days=(day.weekday() + 1) % 7)


@dataclass
class Series:
    scope_type: str            # TOTAL / WAREHOUSE / CATEGORY
    scope: str
    start: date
    y: np.ndarray              # doanh thu theo ngày, không có ngày thiếu

    @property
    def dates(self):
        return [self.start + timedelta(days=i) for i in range(len(self.y))]


def revenue_series(end):
    """Doanh thu đã giao theo ngày cho TOTAL, từng kho và từng danh mục, từ ngày đầu tiên đến `end`."""
    rows = (
        SalesOrderLine.objects.filter(order__status=SHIPPED, order__shipped_at__isnull=False)
        .annotate(d=TruncDate("order__shipped_at"))
        .filter(d__lte=end)
        .values("d", "order__warehouse__code", "product__category__name")
        .annotate(rev=Sum(ExpressionWrapper(F("quantity") * F("unit_price"), output_field=MONEY)))
    )
    cells = [(r["d"], r["order__warehouse__code"], r["product__category__name"], float(r["rev"])) for r in rows]
    if not cells:
        return []
    start = min(c[0] for c in cells)
    n = (end - start).days + 1

    scopes = {("TOTAL", "TOTAL"): np.zeros(n)}
    for day, warehouse, category, rev in cells:
        i = (day - start).days
        scopes[("TOTAL", "TOTAL")][i] += rev
        scopes.setdefault(("WAREHOUSE", warehouse), np.zeros(n))[i] += rev
        scopes.setdefault(("CATEGORY", category), np.zeros(n))[i] += rev
    order = {"TOTAL": 0, "WAREHOUSE": 1, "CATEGORY": 2}
    return [Series(t, s, start, y) for (t, s), y in sorted(scopes.items(), key=lambda kv: (order[kv[0][0]], kv[0][1]))]


def customer_orders(end):
    """{customer_id: (ordinal_days, revenue)} của các đơn đã giao đến `end`, sắp xếp theo ngày."""
    rows = (
        SalesOrder.objects.filter(status=SHIPPED, shipped_at__isnull=False)
        .annotate(d=TruncDate("shipped_at"))
        .filter(d__lte=end)
        .values_list("customer_id", "d", "total_amount")
    )
    grouped = defaultdict(list)
    for customer_id, day, total in rows:
        grouped[customer_id].append((day.toordinal(), float(total)))
    result = {}
    for customer_id, items in grouped.items():
        items.sort()
        result[customer_id] = (np.array([d for d, _ in items], dtype=np.int64),
                               np.array([r for _, r in items], dtype=float))
    return result


def order_baskets(end):
    """Danh sách tập mã sản phẩm của từng đơn đã giao đến `end`: [(sku, ...), ...]."""
    rows = (
        SalesOrderLine.objects.filter(order__status=SHIPPED, order__shipped_at__isnull=False)
        .annotate(d=TruncDate("order__shipped_at"))
        .filter(d__lte=end)
        .values_list("order_id", "product__sku", "product__name")
    )
    baskets, names = defaultdict(set), {}
    for order_id, sku, name in rows:
        baskets[order_id].add(sku)
        names[sku] = name
    return list(baskets.values()), names


def customer_directory():
    return {c.pk: c for c in Customer.objects.all()}
