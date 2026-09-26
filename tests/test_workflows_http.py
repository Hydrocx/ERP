"""End-to-end flows through HTTP: admin document actions, role permissions and the frontend."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from forecast.models import ReorderSuggestion
from inventory import services as stock
from inventory.models import StockLevel, StockMovement
from purchasing.models import PurchaseOrder
from sales.models import SalesOrder

from .factories import (
    ProductFactory,
    PurchaseOrderFactory,
    PurchaseOrderLineFactory,
    SalesOrderFactory,
    SalesOrderLineFactory,
    WarehouseFactory,
)

pytestmark = pytest.mark.django_db
HOST = {"HTTP_HOST": "localhost"}


def surl(model, name, *args):
    return reverse(model.snippet_viewset.get_url_name(name), args=args)


@pytest.fixture
def roles():
    call_command("setup_roles", demo_users=True, verbosity=0)
    return {u.username: u for u in get_user_model().objects.all()}


def login(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def po():
    order = PurchaseOrderFactory()
    PurchaseOrderLineFactory(order=order, quantity=10, unit_price=Decimal("1000"))
    order.save()
    return order


# ------------------------------------------------------------- admin actions

def test_po_approve_and_partial_receive_via_admin(client, roles, po):
    manager, keeper = roles["quanly"], roles["thukho"]
    line = po.lines.get()

    # Warehouse keeper cannot approve
    login(client, keeper)
    client.post(surl(PurchaseOrder, "approve", po.pk), **HOST)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.DRAFT

    login(client, manager)
    page = client.get(surl(PurchaseOrder, "inspect", po.pk), **HOST).content.decode()
    assert "Duyệt PO" in page
    response = client.post(surl(PurchaseOrder, "approve", po.pk), **HOST)
    assert response.status_code == 302
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.APPROVED

    login(client, keeper)
    assert client.get(surl(PurchaseOrder, "receive", po.pk), **HOST).status_code == 200
    client.post(surl(PurchaseOrder, "receive", po.pk), {f"qty_{line.pk}": "4"}, **HOST)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.PARTIAL
    assert StockLevel.objects.get(product=line.product, warehouse=po.warehouse).on_hand == 4

    # over-receiving is rejected with a message, nothing changes
    client.post(surl(PurchaseOrder, "receive", po.pk), {f"qty_{line.pk}": "99"}, **HOST)
    assert StockLevel.objects.get(product=line.product, warehouse=po.warehouse).on_hand == 4


def test_so_confirm_and_ship_via_admin(client, roles):
    wh = WarehouseFactory()
    p = ProductFactory()
    stock.stock_in(p, wh, 10, 1000)
    so = SalesOrderFactory(warehouse=wh)
    SalesOrderLineFactory(order=so, product=p, quantity=3)
    so.save()

    login(client, roles["banhang"])
    client.post(surl(SalesOrder, "confirm", so.pk), **HOST)
    client.post(surl(SalesOrder, "ship", so.pk), **HOST)  # sales role cannot ship
    so.refresh_from_db()
    assert so.status == SalesOrder.Status.CONFIRMED

    login(client, roles["thukho"])
    client.post(surl(SalesOrder, "ship", so.pk), **HOST)
    so.refresh_from_db()
    assert so.status == SalesOrder.Status.SHIPPED
    assert StockLevel.objects.get(product=p, warehouse=wh).on_hand == 7


def test_stock_adjust_and_transfer_forms(client, roles):
    p, a, b = ProductFactory(), WarehouseFactory(), WarehouseFactory()
    stock.stock_in(p, a, 10, 1000)
    login(client, roles["banhang"])
    assert client.get(surl(StockLevel, "adjust"), **HOST).status_code == 302  # no permission -> dashboard

    login(client, roles["thukho"])
    r = client.post(surl(StockLevel, "adjust"),
                    {"product": p.pk, "warehouse": a.pk, "counted_quantity": 8, "reason": "Kiểm kê tháng"}, **HOST)
    assert r.status_code == 302
    client.post(surl(StockLevel, "transfer"),
                {"product": p.pk, "from_warehouse": a.pk, "to_warehouse": b.pk, "quantity": 3}, **HOST)
    assert StockLevel.objects.get(product=p, warehouse=a).on_hand == 5
    assert StockLevel.objects.get(product=p, warehouse=b).on_hand == 3
    assert StockMovement.objects.filter(product=p, movement_type="ADJUST", note="Kiểm kê tháng").exists()


# ------------------------------------------------------------------ frontend

FRONTEND = ["dashboard:home", "dashboard:inventory", "dashboard:reorder", "dashboard:reports",
            "dashboard:assistant"]


@pytest.mark.parametrize("name", FRONTEND)
def test_frontend_requires_login(client, name):
    response = client.get(reverse(name), **HOST)
    assert response.status_code == 302 and "/accounts/login/" in response.url


@pytest.mark.parametrize("name", FRONTEND)
def test_frontend_pages_render_for_manager(client, roles, name):
    login(client, roles["quanly"])
    assert client.get(reverse(name), **HOST).status_code == 200


def test_sales_role_cannot_see_reorder(client, roles):
    login(client, roles["banhang"])
    assert client.get(reverse("dashboard:reorder"), **HOST).status_code == 403


def test_product_page_and_ai_fallback(client, roles, no_openai):
    p, wh = ProductFactory(), WarehouseFactory()
    stock.stock_in(p, wh, 10, 1000)
    login(client, roles["quanly"])
    assert client.get(reverse("dashboard:product", args=[p.sku]), **HOST).status_code == 200
    html = client.post(reverse("dashboard:product_ai", args=[p.sku]), **HOST).content.decode()
    assert "OPENAI_API_KEY" in html and "Tóm tắt theo công thức" in html


def test_reorder_run_and_approve_from_frontend(client, roles, no_openai):
    p, wh = ProductFactory(reorder_point=10), WarehouseFactory()
    stock.stock_in(p, wh, 1, 1000)
    s = ReorderSuggestion.objects.create(product=p, warehouse=wh, supplier=p.default_supplier, available=1,
                                         on_order=0, reorder_point=10, suggested_qty=25)
    login(client, roles["muahang"])
    page = client.get(reverse("dashboard:reorder"), **HOST).content.decode()
    assert p.name in page and 'value="25"' in page
    client.post(reverse("dashboard:reorder"), {"selected": [s.pk], f"qty_{s.pk}": "30", "action": "approve"},
                **HOST)
    s.refresh_from_db()
    assert s.status == ReorderSuggestion.Status.APPROVED and s.purchase_order.lines.get().quantity == 30

    r = client.post(reverse("dashboard:reorder_run"), **HOST)
    assert r.status_code == 302


def test_generate_report_from_frontend(client, roles, no_openai):
    login(client, roles["quanly"])
    r = client.post(reverse("dashboard:reports"), {"start": "2026-09-01", "end": "2026-09-07"}, **HOST)
    assert r.status_code == 302
    page = client.get(reverse("dashboard:reports"), **HOST).content.decode()
    assert "Báo cáo 01/09 – 07/09/2026" in page

    login(client, roles["thukho"])  # no generate permission
    client.post(reverse("dashboard:reports"), {"start": "2026-08-01", "end": "2026-08-07"}, **HOST)
    from reports.models import AIReportPage
    assert AIReportPage.objects.count() == 1


def test_assistant_fallback_and_answer(client, roles, no_openai, settings, monkeypatch):
    login(client, roles["banhang"])
    html = client.post(reverse("dashboard:assistant"), {"question": "Tồn kho?"}, **HOST).content.decode()
    assert "OPENAI_API_KEY" in html

    monkeypatch.setattr("ai.assistant.ask", lambda q, h, user=None: ("**Có 3** mặt hàng\n- A\n- B", ["low_stock_items"]))
    html = client.post(reverse("dashboard:assistant"), {"question": "Hết hàng?"}, **HOST).content.decode()
    assert "<strong>Có 3</strong>" in html and "<li>A</li>" in html and "low_stock_items" in html
    assert len(client.session["assistant_history"]) == 2


def test_admin_home_shows_stock_alert_panel(client, roles):
    login(client, roles["quanly"])
    html = client.get(reverse("wagtailadmin_home"), **HOST).content.decode()
    assert "Cảnh báo tồn kho" in html
