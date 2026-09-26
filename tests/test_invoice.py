from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from ai.client import AIError
from ai.models import AICallLog
from ai.services import extract_invoice
from purchasing.invoice import build_preview, create_po_from_invoice, match_product
from purchasing.models import PurchaseOrder

from .factories import ProductFactory, SupplierFactory, WarehouseFactory

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
HOST = {"HTTP_HOST": "localhost"}


@pytest.fixture
def catalog():
    supplier = SupplierFactory(name="Công ty TNHH Nước giải khát Sông Xanh", tax_code="0312345678")
    water = ProductFactory(sku="DU-NKHOANG", name="Nước khoáng 500ml (thùng 24 chai)", cost_price=Decimal("85000"),
                           default_supplier=supplier)
    tea = ProductFactory(sku="DU-TRAXANH", name="Trà xanh 455ml (thùng 24 chai)", cost_price=Decimal("160000"),
                         default_supplier=supplier)
    return supplier, water, tea


INVOICE = {
    "supplier_name": "CTY TNHH NƯỚC GIẢI KHÁT SÔNG XANH", "supplier_tax_code": "0312 345 678",
    "invoice_number": "0001234", "invoice_date": "2026-09-20",
    "lines": [
        {"sku": "DU-NKHOANG", "name": "Nước khoáng 500ml thùng 24", "quantity": 50, "unit_price": 85000},
        {"sku": None, "name": "Tra xanh 455ml thung 24 chai", "quantity": 20, "unit_price": 230000},
        {"sku": None, "name": "Phí vận chuyển", "quantity": 1, "unit_price": 100000},
    ],
    "total_amount": 12_000_000, "note": None,
}


def test_extract_invoice_sends_image_and_hides_it_from_log(fake_openai):
    fake_openai.queue_json(INVOICE)
    assert extract_invoice(PNG, "image/png")["invoice_number"] == "0001234"
    content = fake_openai.requests[0]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert fake_openai.requests[0]["response_format"]["json_schema"]["name"] == "supplier_invoice"
    log = AICallLog.objects.get()
    assert log.feature == "invoice" and "base64" not in log.request_excerpt


def test_extract_invoice_rejects_bad_type(fake_openai):
    with pytest.raises(AIError):
        extract_invoice(b"%PDF", "application/pdf")


def test_matching_and_warnings(catalog):
    supplier, water, tea = catalog
    preview = build_preview(INVOICE)
    assert preview["supplier_id"] == supplier.pk and preview["supplier_score"] == 1.0  # by tax code
    ids = [line["product_id"] for line in preview["lines"]]
    assert ids[:2] == [water.pk, tea.pk] and ids[2] is None
    text = " | ".join(preview["warnings"])
    assert "Phí vận chuyển" in text          # unmatched line
    assert "lệch >20%" in text               # tea price 230k vs 160k
    assert "Tổng tiền trên hóa đơn" in text  # 9,000,000 vs sum of lines


def test_match_product_by_name_threshold(catalog):
    _, water, _ = catalog
    products = [water]
    assert match_product(None, "nuoc khoang 500ml thung 24 chai", products)[0] == water
    assert match_product(None, "Bút bi xanh", products)[0] is None


def test_create_po_skips_empty_lines(catalog):
    supplier, water, tea = catalog
    wh = WarehouseFactory()
    po = create_po_from_invoice(supplier, wh, [(water, 50, Decimal("85000")), (tea, 0, Decimal("1")),
                                               (None, 3, Decimal("1"))], "0001234", "2026-09-20")
    assert po.status == PurchaseOrder.Status.DRAFT and po.source == PurchaseOrder.Source.INVOICE_OCR
    assert list(po.lines.values_list("quantity", flat=True)) == [50]
    assert str(po.order_date) == "2026-09-20" and "0001234" in po.note


def test_invoice_page_flow(client, catalog, fake_openai):
    call_command("setup_roles", demo_users=True, verbosity=0)
    from django.contrib.auth import get_user_model
    supplier, water, tea = catalog
    wh = WarehouseFactory()
    client.force_login(get_user_model().objects.get(username="muahang"))
    url = reverse("dashboard:invoice")
    assert client.get(url, **HOST).status_code == 200

    fake_openai.queue_json(INVOICE)
    client.post(url, {"action": "extract", "image": SimpleUploadedFile("hd.png", PNG, content_type="image/png")},
                **HOST)
    page = client.get(url, **HOST).content.decode()
    assert "0001234" in page and "Cần kiểm tra" in page

    response = client.post(url, {
        "action": "create", "supplier": supplier.pk, "warehouse": wh.pk,
        "product_0": water.pk, "qty_0": "50", "price_0": "85000",
        "product_1": tea.pk, "qty_1": "20", "price_1": "160000",
        "product_2": "", "qty_2": "1", "price_2": "100000",
    }, **HOST)
    po = PurchaseOrder.objects.get(source=PurchaseOrder.Source.INVOICE_OCR)
    assert response.status_code == 302 and str(po.pk) in response.url
    assert po.total_amount == 50 * 85000 + 20 * 160000
    assert "invoice_preview" not in client.session


def test_invoice_page_requires_purchase_permission(client):
    call_command("setup_roles", demo_users=True, verbosity=0)
    from django.contrib.auth import get_user_model
    client.force_login(get_user_model().objects.get(username="thukho"))
    assert client.get(reverse("dashboard:invoice"), **HOST).status_code == 403
