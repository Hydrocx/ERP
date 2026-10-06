"""Kiểm thử lớp analytics mà Apache Superset đọc.

Các test này chỉ chạy khi DATABASE_URL trỏ tới PostgreSQL (view dùng cú pháp
riêng của Postgres). Trên SQLite chúng được bỏ qua để đồng đội vẫn chạy được
`pytest` mà không cần dựng Docker.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from inventory import services as stock
from reports.kpis import inventory_snapshot, sales_summary
from sales import services as sales
from sales.models import SalesOrder, SalesOrderLine

from .factories import CustomerFactory, ProductFactory, WarehouseFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(connection.vendor != "postgresql",
                       reason="Lớp analytics chỉ chạy trên PostgreSQL"),
]

DAY = date(2026, 9, 10)


@pytest.fixture
def views(db):
    call_command("create_analytics_views", no_grant=True, verbosity=0)


@pytest.fixture
def sold(db):
    """Một đơn bán đã giao: 10 sản phẩm, giá vốn 1.000, giá bán 1.500."""
    wh = WarehouseFactory(code="HN")
    product = ProductFactory(cost_price=Decimal("1000"), sale_price=Decimal("1500"))
    customer = CustomerFactory(email="khach@example.com", phone="0901234567")
    stock.stock_in(product, wh, 100, 1000)
    so = SalesOrder(customer=customer, warehouse=wh, order_date=DAY)
    so.lines = [SalesOrderLine(product=product, quantity=10, unit_price=Decimal("1500"))]
    so.save()
    at = timezone.make_aware(datetime.combine(DAY, time(10)))
    sales.ship_so(sales.confirm_so(so, at=at), at=at)
    return product, wh, customer


def _rows(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _one(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchone()[0]


# --------------------------------------------------------------------- cấu trúc

def test_all_views_created(views):
    names = {r["table_name"] for r in _rows(
        "SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'")}
    expected = {
        "dim_customer", "dim_product", "dim_supplier", "dim_warehouse",
        "fct_sales_line", "fct_purchase_line", "fct_stock_movement",
        "mart_daily_sales", "mart_customer_rfm", "mart_inventory_status",
        "mart_supplier_performance", "mart_reorder_suggestion", "mart_ai_usage",
    }
    assert expected <= names


def test_creating_views_twice_is_safe(views):
    call_command("create_analytics_views", no_grant=True, verbosity=0)
    assert _one("SELECT count(*) FROM information_schema.views WHERE table_schema='analytics'") >= 13


# ------------------------------------------------------- số liệu khớp với ORM

def test_daily_sales_matches_orm(views, sold):
    orm = sales_summary(DAY, DAY)
    row = _rows("SELECT * FROM analytics.mart_daily_sales WHERE sales_date = %s", [DAY])[0]
    assert row["revenue"] == Decimal(str(orm["revenue"])) == Decimal("15000")
    assert row["cogs"] == Decimal(str(orm["cogs"])) == Decimal("10000")
    assert row["gross_profit"] == Decimal(str(orm["gross_profit"])) == Decimal("5000")
    assert row["orders"] == orm["orders_shipped"] == 1


def test_sales_line_only_counts_shipped(views, sold):
    product, wh, customer = sold
    draft = SalesOrder(customer=customer, warehouse=wh, order_date=DAY)
    draft.lines = [SalesOrderLine(product=product, quantity=5, unit_price=Decimal("1500"))]
    draft.save()  # ở trạng thái Nháp

    shipped = _one("""SELECT COALESCE(sum(revenue), 0) FROM analytics.fct_sales_line
                      WHERE status = 'SHIPPED'""")
    everything = _one("SELECT COALESCE(sum(revenue), 0) FROM analytics.fct_sales_line")
    assert shipped == Decimal("15000")
    assert everything == Decimal("22500")   # gồm cả đơn nháp


def test_inventory_status_matches_orm(views, sold):
    orm = inventory_snapshot()
    row = _rows("SELECT * FROM analytics.mart_inventory_status")[0]
    assert row["on_hand"] == 90
    assert row["available"] == 90
    assert row["stock_value"] == Decimal(str(orm["inventory_value"])) == Decimal("90000")
    assert row["stock_status"] == "Đủ hàng"


def test_inventory_status_labels(views, sold):
    product, wh, _ = sold
    stock.adjust_stock(product, wh, 0, reason="Kiểm kê")
    assert _one("SELECT stock_status FROM analytics.mart_inventory_status") == "Hết hàng"


def test_rfm_segments_every_buyer(views, sold):
    _, _, customer = sold
    row = _rows("SELECT * FROM analytics.mart_customer_rfm")[0]
    assert row["customer_code"] == customer.code
    assert row["frequency"] == 1
    assert row["monetary"] == Decimal("15000")
    assert row["segment"]            # luôn có phân khúc
    assert 1 <= row["r_score"] <= 5


# -------------------------------------------------------------------- bảo mật

def test_customer_pii_is_masked(views, sold):
    row = _rows("SELECT * FROM analytics.dim_customer")[0]
    assert row["email_masked"] == "k***@example.com"
    assert row["phone_masked"] == "***567"
    # Email và số điện thoại gốc không được xuất hiện trong view
    assert "khach@example.com" not in str(row.values())
    assert "0901234567" not in str(row.values())


def test_ai_usage_view_has_no_prompt_content(views):
    columns = {r["column_name"] for r in _rows("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'analytics' AND table_name = 'mart_ai_usage'
    """)}
    assert "request_excerpt" not in columns
    assert "response_excerpt" not in columns
    assert {"feature", "model", "input_tokens", "output_tokens"} <= columns


def test_no_view_exposes_password_or_prompt_columns(views):
    leaks = _rows("""
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'analytics'
          AND column_name IN ('password', 'request_excerpt', 'response_excerpt',
                              'email', 'phone', 'session_data')
    """)
    assert leaks == []


# ------------------------------------------------------ lệnh verify_analytics

def test_verify_analytics_passes(views, sold):
    call_command("verify_analytics", start=DAY - timedelta(days=7), end=DAY, verbosity=0)
