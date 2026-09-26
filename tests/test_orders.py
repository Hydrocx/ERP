from decimal import Decimal

import pytest

from core.exceptions import ERPError, InsufficientStockError, InvalidStatusError
from inventory import services as stock
from inventory.models import StockLevel
from purchasing import services as purchasing
from purchasing.models import PurchaseOrder
from sales import services as sales
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


# ---------- Purchase orders ----------

@pytest.fixture
def po():
    order = PurchaseOrderFactory()
    PurchaseOrderLineFactory(order=order, quantity=10, unit_price=Decimal("1000"))
    PurchaseOrderLineFactory(order=order, quantity=5, unit_price=Decimal("2000"))
    order.save()  # recompute total from saved lines
    return order


def test_po_code_and_total(po):
    assert po.code.startswith("PO-")
    assert po.total_amount == Decimal("20000")
    second = PurchaseOrderFactory(order_date=po.order_date)
    assert int(second.code[-4:]) == int(po.code[-4:]) + 1


def test_receive_requires_approval(po):
    with pytest.raises(InvalidStatusError):
        purchasing.receive_po(po)


def test_partial_then_full_receipt(po):
    purchasing.approve_po(po)
    line1, line2 = po.lines.order_by("id")

    po = purchasing.receive_po(po, {line1.pk: 4})
    assert po.status == PurchaseOrder.Status.PARTIAL
    assert purchasing.on_order_quantity(line1.product, po.warehouse) == 6

    po = purchasing.receive_po(po)  # the rest
    assert po.status == PurchaseOrder.Status.RECEIVED
    assert po.received_date is not None
    assert StockLevel.objects.get(product=line1.product, warehouse=po.warehouse).on_hand == 10
    assert StockLevel.objects.get(product=line2.product, warehouse=po.warehouse).on_hand == 5
    assert purchasing.on_order_quantity(line1.product, po.warehouse) == 0


def test_cannot_over_receive(po):
    purchasing.approve_po(po)
    line = po.lines.first()
    with pytest.raises(ERPError):
        purchasing.receive_po(po, {line.pk: line.quantity + 1})


def test_cannot_cancel_after_receipt(po):
    purchasing.approve_po(po)
    purchasing.receive_po(po, {po.lines.first().pk: 1})
    with pytest.raises(InvalidStatusError):
        purchasing.cancel_po(po)


def test_cannot_approve_twice(po):
    purchasing.approve_po(po)
    with pytest.raises(InvalidStatusError):
        purchasing.approve_po(po)


# ---------- Sales orders ----------

@pytest.fixture
def stocked():
    warehouse = WarehouseFactory()
    product = ProductFactory(sale_price=Decimal("15000"))
    stock.stock_in(product, warehouse, 10, 10000)
    return product, warehouse


def make_so(product, warehouse, qty):
    so = SalesOrderFactory(warehouse=warehouse)
    SalesOrderLineFactory(order=so, product=product, quantity=qty)
    so.save()
    return so


def level(product, warehouse):
    return StockLevel.objects.get(product=product, warehouse=warehouse)


def test_line_price_defaults_to_product_price(stocked):
    so = make_so(*stocked, qty=2)
    assert so.lines.first().unit_price == Decimal("15000")
    assert so.total_amount == Decimal("30000")


def test_confirm_reserves_and_ship_deducts(stocked):
    product, warehouse = stocked
    so = make_so(product, warehouse, 4)

    so = sales.confirm_so(so)
    lv = level(product, warehouse)
    assert (so.status, lv.on_hand, lv.reserved) == (SalesOrder.Status.CONFIRMED, 10, 4)

    so = sales.ship_so(so)
    lv = level(product, warehouse)
    assert (so.status, lv.on_hand, lv.reserved) == (SalesOrder.Status.SHIPPED, 6, 0)


def test_confirm_fails_atomically_when_stock_short(stocked):
    product, warehouse = stocked
    other = ProductFactory()  # no stock at all
    so = make_so(product, warehouse, 3)
    SalesOrderLineFactory(order=so, product=other, quantity=1)

    with pytest.raises(InsufficientStockError):
        sales.confirm_so(so)
    so.refresh_from_db()
    assert so.status == SalesOrder.Status.DRAFT
    assert level(product, warehouse).reserved == 0  # first line rolled back


def test_cancel_confirmed_releases_reservation(stocked):
    product, warehouse = stocked
    so = sales.confirm_so(make_so(product, warehouse, 4))
    sales.cancel_so(so)
    assert level(product, warehouse).reserved == 0


def test_cannot_ship_draft_or_cancel_shipped(stocked):
    so = make_so(*stocked, qty=1)
    with pytest.raises(InvalidStatusError):
        sales.ship_so(so)
    so = sales.ship_so(sales.confirm_so(so))
    with pytest.raises(InvalidStatusError):
        sales.cancel_so(so)
