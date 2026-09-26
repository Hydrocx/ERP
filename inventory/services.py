"""All stock changes go through these functions.

Every function must be called inside transaction.atomic() (they open one
themselves) and locks the StockLevel row with select_for_update so that
concurrent orders cannot drive stock negative.
"""

from decimal import Decimal

from django.db import transaction

from core.exceptions import ERPError, InsufficientStockError

from .models import StockLevel, StockMovement


def _lock_level(product, warehouse):
    level, _ = StockLevel.objects.get_or_create(product=product, warehouse=warehouse)
    return StockLevel.objects.select_for_update().get(pk=level.pk)


def _record(level, movement_type, quantity, *, unit_cost=None, reference="", note="",
            user=None, occurred_at=None):
    kwargs = {}
    if occurred_at is not None:
        kwargs["occurred_at"] = occurred_at
    return StockMovement.objects.create(
        product=level.product,
        warehouse=level.warehouse,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=level.on_hand,
        unit_cost=unit_cost,
        reference=reference,
        note=note,
        created_by=user,
        **kwargs,
    )


def _check_positive(quantity):
    if quantity <= 0:
        raise ERPError("Số lượng phải lớn hơn 0")


@transaction.atomic
def stock_in(product, warehouse, quantity, unit_cost, *, movement_type=StockMovement.Type.IN,
             reference="", note="", user=None, occurred_at=None):
    """Increase on_hand and update moving-average cost."""
    _check_positive(quantity)
    level = _lock_level(product, warehouse)
    unit_cost = Decimal(unit_cost)
    total_value = level.avg_cost * level.on_hand + unit_cost * quantity
    level.on_hand += quantity
    level.avg_cost = (total_value / level.on_hand).quantize(Decimal("0.01"))
    level.save(update_fields=["on_hand", "avg_cost", "updated_at"])
    return _record(level, movement_type, quantity, unit_cost=unit_cost, reference=reference,
                   note=note, user=user, occurred_at=occurred_at)


@transaction.atomic
def stock_out(product, warehouse, quantity, *, movement_type=StockMovement.Type.OUT,
              from_reserved=False, reference="", note="", user=None, occurred_at=None):
    """Decrease on_hand. If from_reserved, the quantity was reserved earlier."""
    _check_positive(quantity)
    level = _lock_level(product, warehouse)
    if from_reserved:
        if level.reserved < quantity:
            raise ERPError(f"Số lượng giữ hàng của {product} không đủ để xuất")
        level.reserved -= quantity
    elif level.available < quantity:
        raise InsufficientStockError(product, warehouse, quantity, level.available)
    level.on_hand -= quantity
    level.save(update_fields=["on_hand", "reserved", "updated_at"])
    return _record(level, movement_type, -quantity, unit_cost=level.avg_cost,
                   reference=reference, note=note, user=user, occurred_at=occurred_at)


@transaction.atomic
def reserve(product, warehouse, quantity):
    _check_positive(quantity)
    level = _lock_level(product, warehouse)
    if level.available < quantity:
        raise InsufficientStockError(product, warehouse, quantity, level.available)
    level.reserved += quantity
    level.save(update_fields=["reserved", "updated_at"])
    return level


@transaction.atomic
def release(product, warehouse, quantity):
    _check_positive(quantity)
    level = _lock_level(product, warehouse)
    level.reserved = max(0, level.reserved - quantity)
    level.save(update_fields=["reserved", "updated_at"])
    return level


@transaction.atomic
def adjust_stock(product, warehouse, new_quantity, *, reason, user=None, occurred_at=None,
                 unit_cost=None):
    """Set on_hand to a counted quantity (stock take / opening balance)."""
    if not reason:
        raise ERPError("Điều chỉnh tồn kho bắt buộc phải có lý do")
    if new_quantity < 0:
        raise ERPError("Số lượng tồn không được âm")
    level = _lock_level(product, warehouse)
    if new_quantity < level.reserved:
        raise ERPError(
            f"Không thể điều chỉnh {product} xuống {new_quantity} vì đang giữ {level.reserved} cho đơn bán"
        )
    delta = new_quantity - level.on_hand
    if delta == 0:
        return None
    if delta > 0 and unit_cost is not None:
        total_value = level.avg_cost * level.on_hand + Decimal(unit_cost) * delta
        level.avg_cost = (total_value / new_quantity).quantize(Decimal("0.01"))
    level.on_hand = new_quantity
    level.save(update_fields=["on_hand", "avg_cost", "updated_at"])
    return _record(level, StockMovement.Type.ADJUST, delta, unit_cost=unit_cost or level.avg_cost,
                   note=reason, user=user, occurred_at=occurred_at)


@transaction.atomic
def transfer_stock(product, from_warehouse, to_warehouse, quantity, *, reference="", user=None,
                   occurred_at=None):
    if from_warehouse == to_warehouse:
        raise ERPError("Kho đi và kho đến phải khác nhau")
    out = stock_out(product, from_warehouse, quantity,
                    movement_type=StockMovement.Type.TRANSFER_OUT, reference=reference,
                    note=f"Chuyển đến {to_warehouse.code}", user=user, occurred_at=occurred_at)
    stock_in(product, to_warehouse, quantity, out.unit_cost,
             movement_type=StockMovement.Type.TRANSFER_IN, reference=reference,
             note=f"Chuyển từ {from_warehouse.code}", user=user, occurred_at=occurred_at)
    return out
