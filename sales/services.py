from django.db import transaction
from django.utils import timezone

from core.exceptions import ERPError, InvalidStatusError
from inventory import services as stock

from .models import SalesOrder

Status = SalesOrder.Status


def _lock(so):
    return SalesOrder.objects.select_for_update().get(pk=so.pk)


@transaction.atomic
def confirm_so(so, user=None, at=None):
    """Reserve stock for every line. Fails as a whole if any line lacks stock."""
    so = _lock(so)
    if so.status != Status.DRAFT:
        raise InvalidStatusError(f"Chỉ xác nhận được SO ở trạng thái Nháp ({so.code})")
    lines = list(so.lines.select_related("product"))
    if not lines:
        raise ERPError(f"SO {so.code} chưa có dòng hàng")
    for line in lines:
        stock.reserve(line.product, so.warehouse, line.quantity)
    so.status = Status.CONFIRMED
    so.confirmed_at = at or timezone.now()
    so.save()
    return so


@transaction.atomic
def ship_so(so, user=None, at=None):
    so = _lock(so)
    if so.status != Status.CONFIRMED:
        raise InvalidStatusError(f"Chỉ giao được SO đã xác nhận ({so.code}: {so.get_status_display()})")
    for line in so.lines.select_related("product"):
        stock.stock_out(line.product, so.warehouse, line.quantity, from_reserved=True,
                        reference=so.code, note=f"Giao cho {so.customer.code}",
                        user=user, occurred_at=at)
    so.status = Status.SHIPPED
    so.shipped_at = at or timezone.now()
    so.save()
    return so


@transaction.atomic
def cancel_so(so, user=None):
    so = _lock(so)
    if so.status == Status.SHIPPED:
        raise InvalidStatusError(f"SO {so.code} đã giao, không thể hủy")
    if so.status == Status.CANCELLED:
        raise InvalidStatusError(f"SO {so.code} đã hủy trước đó")
    if so.status == Status.CONFIRMED:
        for line in so.lines.select_related("product"):
            stock.release(line.product, so.warehouse, line.quantity)
    so.status = Status.CANCELLED
    so.save()
    return so
