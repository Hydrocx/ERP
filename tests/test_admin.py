import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from catalog.models import Category, Customer, Product, Supplier, Warehouse
from inventory import services as stock
from inventory.models import StockLevel, StockMovement
from purchasing import services as purchasing
from purchasing.models import PurchaseOrder
from sales.models import SalesOrder

from .factories import ProductFactory, PurchaseOrderFactory, PurchaseOrderLineFactory, WarehouseFactory

pytestmark = pytest.mark.django_db

ALL_MODELS = [Category, Product, Supplier, Warehouse, Customer, StockLevel, StockMovement,
              PurchaseOrder, SalesOrder]


def url(model, name, *args):
    return reverse(model.snippet_viewset.get_url_name(name), args=args)


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
    client.force_login(user)
    return client


@pytest.fixture
def data():
    product = ProductFactory()
    warehouse = WarehouseFactory()
    stock.stock_in(product, warehouse, 5, 100)
    return product, warehouse


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_list_pages_render(admin_client, data, model):
    response = admin_client.get(url(model, "list"))
    assert response.status_code == 200


@pytest.mark.parametrize("model", [Product, Supplier, PurchaseOrder, SalesOrder], ids=lambda m: m.__name__)
def test_add_pages_render(admin_client, model):
    assert admin_client.get(url(model, "add")).status_code == 200


def test_search_uses_orm(admin_client, data):
    product, _ = data
    response = admin_client.get(url(Product, "list"), {"q": product.sku})
    assert product.name in response.content.decode()


@pytest.mark.parametrize("model", [StockLevel, StockMovement], ids=lambda m: m.__name__)
def test_ledger_is_read_only(admin_client, data, model):
    obj = model.objects.first()
    # Wagtail answers PermissionDenied with a redirect to the dashboard
    for response in (admin_client.get(url(model, "add")), admin_client.get(url(model, "edit", obj.pk))):
        assert response.status_code == 302
        assert response.url == reverse("wagtailadmin_home")
    assert admin_client.get(url(model, "inspect", obj.pk)).status_code == 200


def test_draft_po_is_editable_but_approved_is_locked(admin_client):
    po = PurchaseOrderFactory()
    PurchaseOrderLineFactory(order=po)
    assert admin_client.get(url(PurchaseOrder, "edit", po.pk)).status_code == 200

    purchasing.approve_po(po)
    response = admin_client.get(url(PurchaseOrder, "edit", po.pk))
    assert response.status_code == 302
    assert response.url == url(PurchaseOrder, "inspect", po.pk)
    response = admin_client.post(url(PurchaseOrder, "delete", po.pk))
    assert response.status_code == 302
    assert PurchaseOrder.objects.filter(pk=po.pk).exists()


def test_create_po_through_admin_form(admin_client, data):
    product, warehouse = data
    supplier = product.default_supplier
    form = {
        "supplier": supplier.pk,
        "warehouse": warehouse.pk,
        "order_date": "2026-09-01",
        "expected_date": "2026-09-08",
        "note": "",
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-product": product.pk,
        "lines-0-quantity": "12",
        "lines-0-unit_price": "9000",
        "lines-0-ORDER": "1",
        "lines-0-id": "",
        "lines-0-DELETE": "",
    }
    response = admin_client.post(url(PurchaseOrder, "add"), form)
    assert response.status_code == 302, response.content.decode()[:2000]
    po = PurchaseOrder.objects.get()
    assert po.code == "PO-202609-0001"
    assert po.total_amount == 12 * 9000
    assert po.created_by.username == "root"
    assert po.status == PurchaseOrder.Status.DRAFT
