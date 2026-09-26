import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from forecast import engine, services
from forecast.models import DemandForecast, ReorderSuggestion
from inventory import services as stock
from inventory.models import StockLevel
from purchasing.models import PurchaseOrder
from sales import services as sales
from sales.models import SalesOrder, SalesOrderLine

from .factories import CustomerFactory, ProductFactory, WarehouseFactory

AS_OF = date(2026, 9, 1)


# ---------------------------------------------------------------- engine (pure)

def test_constant_demand_statistics():
    daily = {AS_OF - timedelta(days=i): 4 for i in range(1, 91)}
    stats = engine.demand_stats(daily, AS_OF, window=90)
    assert stats.avg_daily == stats.recent_daily == stats.forecast_daily == 4
    assert stats.std_daily == 0
    assert stats.seasonal_factor == 1.0 and not stats.has_seasonality
    assert stats.weekly == [28] * 12


def test_recent_demand_weighs_more():
    # 62 days at 2/day, last 28 days at 6/day
    daily = {AS_OF - timedelta(days=i): (6 if i <= 28 else 2) for i in range(1, 91)}
    stats = engine.demand_stats(daily, AS_OF, window=90)
    avg90 = (28 * 6 + 62 * 2) / 90
    assert math.isclose(stats.forecast_daily, 0.6 * 6 + 0.4 * avg90)


def test_seasonal_factor_from_last_year():
    daily = {}
    for i in range(1, 500):
        day = AS_OF - timedelta(days=i)
        daily[day] = 3
    ly = AS_OF - timedelta(days=365)
    for i in range(14):  # last year the next two weeks sold double
        daily[ly + timedelta(days=i)] = 6
    stats = engine.demand_stats(daily, AS_OF, history_start=AS_OF - timedelta(days=499), window=90, horizon=14)
    assert stats.has_seasonality
    assert math.isclose(stats.seasonal_factor, 2.0)
    assert math.isclose(stats.forecast_daily, 6.0)


def test_seasonal_item_starting_from_zero_uses_last_year_level():
    daily = {}
    ly = AS_OF - timedelta(days=365)
    for i in range(14):
        daily[ly + timedelta(days=i)] = 5
    stats = engine.demand_stats(daily, AS_OF, history_start=AS_OF - timedelta(days=450), horizon=14)
    assert stats.forecast_daily == 5


def test_policy_and_suggested_quantity():
    stats = engine.DemandStats(avg_daily=10, recent_daily=10, std_daily=4, seasonal_factor=1,
                               forecast_daily=10)
    policy = engine.inventory_policy(stats, lead_time_days=9, review_days=7, service_level=Decimal("0.95"))
    z = engine.z_score(0.95)
    assert math.isclose(z, 1.6449, rel_tol=1e-3)
    assert policy.safety_stock == math.ceil(z * 4 * 3)            # 20
    assert policy.reorder_point == math.ceil(10 * 9 + z * 4 * 3)  # 110
    assert policy.order_up_to == math.ceil(10 * 16 + z * 4 * 3)   # 180
    assert engine.suggested_quantity(available=200, on_order=0, policy=policy) == 0
    assert engine.suggested_quantity(available=60, on_order=40, policy=policy) == policy.order_up_to - 100


def test_stockout_days():
    start = date(2026, 1, 1)
    movements = [(date(2026, 1, 2), -5, 0), (date(2026, 1, 5), 10, 10)]
    # opening balance 5; zero from Jan 2 to Jan 4 inclusive
    assert engine.stockout_days(movements, start, date(2026, 1, 8)) == 3
    assert engine.stockout_days([], start, date(2026, 1, 8)) == 0


# -------------------------------------------------------------- services (DB)

def _sell(product, warehouse, customer, day, qty):
    so = SalesOrder(customer=customer, warehouse=warehouse, order_date=day)
    so.lines = [SalesOrderLine(product=product, quantity=qty, unit_price=product.sale_price)]
    so.save()
    at = timezone.make_aware(datetime.combine(day, time(10)))
    sales.ship_so(sales.confirm_so(so, at=at), at=at)


@pytest.fixture
def history(db):
    """A product selling 5/day for 30 days at one warehouse."""
    warehouse = WarehouseFactory()
    product = ProductFactory(lead_time_days=5, cost_price=Decimal("1000"), sale_price=Decimal("1500"))
    customer = CustomerFactory()
    today = timezone.localdate()
    stock.stock_in(product, warehouse, 500, 1000,
                   occurred_at=timezone.make_aware(datetime.combine(today - timedelta(days=40), time(8))))
    for i in range(1, 31):
        _sell(product, warehouse, customer, today - timedelta(days=i), 5)
    stock.adjust_stock(product, warehouse, 20, reason="Kiểm kê")  # force a low position
    return product, warehouse


@pytest.mark.django_db
def test_run_forecast_creates_forecast_rop_and_suggestion(history, no_openai):
    product, warehouse = history
    result = services.run_forecast(use_ai=True)
    assert result["forecasts"] >= 1
    assert "OPENAI_API_KEY" in result["ai_error"]

    fc = DemandForecast.objects.get(product=product, warehouse=warehouse)
    # 30 days * 5 over a 90-day window: avg 1.667; last 28 days: 5 -> 0.6*5 + 0.4*1.667 = 3.667
    assert float(fc.forecast_daily) == pytest.approx(3.667, abs=0.001)
    assert fc.available == 20
    level = StockLevel.objects.get(product=product, warehouse=warehouse)
    assert level.reorder_point == fc.reorder_point > 0

    suggestion = ReorderSuggestion.objects.get(product=product)
    assert suggestion.suggested_qty == fc.order_up_to - 20
    assert suggestion.ai_action == "" and "ROP" in suggestion.ai_reason


@pytest.mark.django_db
def test_rerun_expires_previous_pending_suggestions(history, no_openai):
    services.run_forecast(use_ai=False)
    first = ReorderSuggestion.objects.get()
    services.run_forecast(use_ai=False)
    first.refresh_from_db()
    assert first.status == ReorderSuggestion.Status.EXPIRED
    assert ReorderSuggestion.objects.filter(status=ReorderSuggestion.Status.PENDING).count() == 1


@pytest.mark.django_db
def test_approve_groups_suggestions_into_draft_po(history, no_openai):
    product, warehouse = history
    other = ProductFactory(default_supplier=product.default_supplier)
    services.run_forecast(use_ai=False)
    s1 = ReorderSuggestion.objects.get(product=product)
    s2 = ReorderSuggestion.objects.create(product=other, warehouse=warehouse, supplier=other.default_supplier,
                                          available=0, on_order=0, reorder_point=5, suggested_qty=12)
    orders = services.approve_suggestions([(s1, 40), (s2, 12)])
    assert len(orders) == 1
    po = orders[0]
    assert po.status == PurchaseOrder.Status.DRAFT and po.source == PurchaseOrder.Source.AI
    assert sorted(po.lines.values_list("quantity", flat=True)) == [12, 40]
    s1.refresh_from_db()
    assert (s1.status, s1.final_qty, s1.purchase_order_id) == (ReorderSuggestion.Status.APPROVED, 40, po.pk)
    # already approved -> ignored the second time
    assert services.approve_suggestions([(s1, 40)]) == []


@pytest.mark.django_db
def test_reject(history, no_openai):
    services.run_forecast(use_ai=False)
    s = ReorderSuggestion.objects.get()
    assert services.reject_suggestions([s]) == 1
    s.refresh_from_db()
    assert s.status == ReorderSuggestion.Status.REJECTED
