"""Dựng nhanh lịch sử đơn bán đã giao (bulk_create) để test pipeline AI/ML trên CSDL."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import numpy as np
from django.utils import timezone

from sales.models import SalesOrder, SalesOrderLine

from .factories import CategoryFactory, CustomerFactory, ProductFactory, WarehouseFactory

WEEKDAY = [1.1, 1.0, 1.0, 1.05, 1.1, 0.8, 0.5]


def make_history(days=400, customers=60, seed=0):
    """Một số khách rời bỏ giữa chừng; sản phẩm P0 và P1 hay được mua cùng nhau."""
    rng = np.random.default_rng(seed)
    warehouses = [WarehouseFactory(code="HN"), WarehouseFactory(code="HCM")]
    categories = [CategoryFactory(name="Đồ uống"), CategoryFactory(name="Bánh kẹo")]
    products = [ProductFactory(sku=f"P{i}", name=f"Sản phẩm P{i}", category=categories[i % 2],
                               sale_price=Decimal(10000 * (i + 1)), cost_price=Decimal(8000 * (i + 1)))
                for i in range(4)]
    people = [CustomerFactory() for _ in range(customers)]
    end = timezone.localdate()
    start = end - timedelta(days=days)
    rates = rng.uniform(0.03, 0.18, customers)
    low = min(120, days // 3)
    high = max(low + 1, days - 40)
    churn_day = {i: start + timedelta(days=int(rng.integers(low, high))) for i in range(0, customers, 4)}

    orders, line_specs, number = [], [], 0
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        for i, customer in enumerate(people):
            if i in churn_day and day >= churn_day[i]:
                continue
            for _ in range(rng.poisson(rates[i] * WEEKDAY[day.weekday()])):
                number += 1
                chosen = {int(rng.integers(0, 4))}
                if 0 in chosen and rng.random() < 0.6:
                    chosen.add(1)
                lines = [(products[p], int(rng.integers(1, 6))) for p in sorted(chosen)]
                total = sum(q * p.sale_price for p, q in lines)
                orders.append(SalesOrder(
                    code=f"SO-T-{number:06d}", customer=customer, warehouse=warehouses[number % 2],
                    status=SalesOrder.Status.SHIPPED, order_date=day, total_amount=total,
                    shipped_at=timezone.make_aware(datetime.combine(day, time(10, 0)))))
                line_specs.append(lines)
    SalesOrder.objects.bulk_create(orders)
    SalesOrderLine.objects.bulk_create([
        SalesOrderLine(order=order, product=product, quantity=quantity, unit_price=product.sale_price)
        for order, lines in zip(orders, line_specs) for product, quantity in lines])
    return {"warehouses": warehouses, "products": products, "customers": people, "orders": len(orders), "end": end}
