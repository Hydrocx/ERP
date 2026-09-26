from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from wagtail.models import WorkflowState

from inventory import services as stock
from reports.kpis import compute_kpis
from reports.models import AIReportPage, ReportIndexPage
from reports.services import check_numbers, generate_report, previous_week
from sales import services as sales
from sales.models import SalesOrder, SalesOrderLine

from .factories import CustomerFactory, ProductFactory, WarehouseFactory

pytestmark = pytest.mark.django_db
DAY = date(2026, 9, 10)


@pytest.fixture
def sold():
    wh = WarehouseFactory(code="HN")
    p = ProductFactory(cost_price=Decimal("1000"), sale_price=Decimal("1500"))
    stock.stock_in(p, wh, 100, 1000)
    so = SalesOrder(customer=CustomerFactory(), warehouse=wh, order_date=DAY)
    so.lines = [SalesOrderLine(product=p, quantity=10, unit_price=Decimal("1500"))]
    so.save()
    at = timezone.make_aware(datetime.combine(DAY, time(10)))
    sales.ship_so(sales.confirm_so(so, at=at), at=at)
    return p, wh


def test_kpis(sold):
    p, _ = sold
    k = compute_kpis(DAY - timedelta(days=6), DAY)
    assert k["sales"]["revenue"] == 15000
    assert k["sales"]["cogs"] == 10000
    assert k["sales"]["gross_profit"] == 5000
    assert k["sales"]["gross_margin_pct"] == 33.3
    assert k["sales"]["orders_shipped"] == 1
    assert k["top_products"][0] == {"sku": p.sku, "name": p.name, "qty": 10, "revenue": 15000}
    assert k["inventory"]["inventory_value"] == 90 * 1000
    assert k["revenue_by_warehouse"] == [{"warehouse": "HN", "revenue": 15000}]
    assert k["revenue_change_pct"] is None  # nothing in previous period


def test_previous_week():
    assert previous_week(date(2026, 9, 27)) == (date(2026, 9, 14), date(2026, 9, 20))  # Sunday
    assert previous_week(date(2026, 9, 21)) == (date(2026, 9, 14), date(2026, 9, 20))  # Monday


def test_check_numbers_flags_invented_values():
    kpis = {"period": {"start": "2026-09-01", "end": "2026-09-07"},
            "previous_period": {"start": "2026-08-25", "end": "2026-08-31"},
            "sales": {"revenue": 1_254_000_000, "gross_margin_pct": 21.4, "orders_shipped": 420}}
    ok = ["Doanh thu đạt 1,25 tỷ, biên 21,4%, 420 đơn trong kỳ 2026-09-01."]
    assert check_numbers(ok, kpis) == []
    bad = ["Doanh thu đạt 1,9 tỷ, biên 35%, 3 kho."]
    warnings = check_numbers(bad, kpis)
    assert len(warnings) == 2 and any("1,9" in w for w in warnings) and any("35" in w for w in warnings)


def test_generate_report_fallback_creates_draft_in_workflow(sold, no_openai):
    user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw")
    page = generate_report(DAY - timedelta(days=6), DAY, user=user)
    page.refresh_from_db()
    assert isinstance(page.get_parent().specific, ReportIndexPage)
    assert not page.live and not page.generated_by_ai
    assert "Không dùng được AI" in page.ai_warnings[0]
    assert [b.block_type for b in page.body][:3] == ["heading", "paragraph", "kpi_summary"]
    assert page.kpi_json["sales"]["revenue"] == 15000
    assert WorkflowState.objects.for_instance(page).filter(status=WorkflowState.STATUS_IN_PROGRESS).exists()


def test_generate_report_with_ai(sold, fake_openai):
    fake_openai.queue_json({
        "summary": "Doanh thu 15.000 ₫, lợi nhuận gộp 5.000 ₫ (biên 33,3%). Doanh thu thực tế là 99.999 ₫.",
        "highlights": ["Sản phẩm bán tốt"], "risks": ["Không có"], "recommendations": ["Theo dõi tồn kho"],
    })
    page = generate_report(DAY - timedelta(days=6), DAY, submit_for_review=False)
    page.refresh_from_db()
    assert page.generated_by_ai
    assert page.ai_warnings == ["Số '99.999' không khớp KPI hệ thống"]
    assert "33,3%" in str(page.body[1].value)
    # The KPI JSON sent to the model is exactly what is stored
    assert '"revenue": 15000.0' in fake_openai.requests[0]["messages"][1]["content"]


def test_published_report_page_requires_login_and_renders(sold, no_openai, client):
    page = generate_report(DAY - timedelta(days=6), DAY, submit_for_review=False)
    page.save_revision().publish()
    anonymous = client.get(page.url, HTTP_HOST="localhost")
    assert anonymous.status_code == 302 and "login" in anonymous.url
    client.force_login(get_user_model().objects.create_user("viewer", password="pw"))
    response = client.get(page.url, HTTP_HOST="localhost")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Tóm tắt điều hành" in body and "Lợi nhuận gộp" in body
    assert AIReportPage.objects.live().count() == 1
