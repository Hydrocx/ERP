from decimal import Decimal

import pytest

from core.exceptions import ERPError, InsufficientStockError
from inventory import services
from inventory.models import StockLevel, StockMovement

from .factories import ProductFactory, WarehouseFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def product():
    return ProductFactory()


@pytest.fixture
def warehouse():
    return WarehouseFactory()


def level(product, warehouse):
    return StockLevel.objects.get(product=product, warehouse=warehouse)


def test_stock_in_creates_level_movement_and_average_cost(product, warehouse):
    services.stock_in(product, warehouse, 10, Decimal("100"))
    services.stock_in(product, warehouse, 30, Decimal("200"))

    lv = level(product, warehouse)
    assert lv.on_hand == 40
    assert lv.avg_cost == Decimal("175.00")  # (10*100 + 30*200) / 40
    movements = StockMovement.objects.filter(product=product).order_by("id")
    assert [m.quantity for m in movements] == [10, 30]
    assert [m.balance_after for m in movements] == [10, 40]


def test_stock_out_rejects_more_than_available(product, warehouse):
    services.stock_in(product, warehouse, 5, 100)
    with pytest.raises(InsufficientStockError):
        services.stock_out(product, warehouse, 6)
    assert level(product, warehouse).on_hand == 5


def test_stock_out_records_negative_quantity(product, warehouse):
    services.stock_in(product, warehouse, 5, 100)
    movement = services.stock_out(product, warehouse, 3)
    assert movement.quantity == -3
    assert movement.balance_after == 2


def test_reserved_stock_is_not_available(product, warehouse):
    services.stock_in(product, warehouse, 10, 100)
    services.reserve(product, warehouse, 8)
    with pytest.raises(InsufficientStockError):
        services.reserve(product, warehouse, 3)
    with pytest.raises(InsufficientStockError):
        services.stock_out(product, warehouse, 3)
    assert level(product, warehouse).available == 2


def test_release_never_goes_negative(product, warehouse):
    services.stock_in(product, warehouse, 10, 100)
    services.reserve(product, warehouse, 2)
    services.release(product, warehouse, 5)
    assert level(product, warehouse).reserved == 0


@pytest.mark.parametrize("qty", [0, -1])
def test_quantity_must_be_positive(product, warehouse, qty):
    with pytest.raises(ERPError):
        services.stock_in(product, warehouse, qty, 100)


def test_adjust_requires_reason_and_respects_reserved(product, warehouse):
    services.stock_in(product, warehouse, 10, 100)
    with pytest.raises(ERPError):
        services.adjust_stock(product, warehouse, 8, reason="")
    services.reserve(product, warehouse, 6)
    with pytest.raises(ERPError):
        services.adjust_stock(product, warehouse, 5, reason="Kiểm kê")

    movement = services.adjust_stock(product, warehouse, 7, reason="Kiểm kê")
    assert movement.quantity == -3
    assert movement.movement_type == StockMovement.Type.ADJUST
    assert level(product, warehouse).on_hand == 7


def test_adjust_without_change_returns_none(product, warehouse):
    services.stock_in(product, warehouse, 4, 100)
    assert services.adjust_stock(product, warehouse, 4, reason="Kiểm kê") is None


def test_transfer_moves_stock_and_cost(product, warehouse):
    other = WarehouseFactory()
    services.stock_in(product, warehouse, 10, 100)
    services.transfer_stock(product, warehouse, other, 4, reference="CK-1")

    assert level(product, warehouse).on_hand == 6
    target = level(product, other)
    assert target.on_hand == 4
    assert target.avg_cost == Decimal("100.00")
    types = set(StockMovement.objects.filter(reference="CK-1").values_list("movement_type", flat=True))
    assert types == {StockMovement.Type.TRANSFER_OUT, StockMovement.Type.TRANSFER_IN}


def test_transfer_to_same_warehouse_is_rejected(product, warehouse):
    services.stock_in(product, warehouse, 10, 100)
    with pytest.raises(ERPError):
        services.transfer_stock(product, warehouse, warehouse, 1)
