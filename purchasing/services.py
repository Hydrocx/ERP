from django.db import transaction
from django.utils import timezone

from core.exceptions import ERPError, InvalidStatusError
from inventory import services as stock

from .models import PurchaseOrder, PurchaseOrderLine

Status = PurchaseOrder.Status


def _lock(po):
    return PurchaseOrder.objects.select_for_update().get(pk=po.pk)


@transaction.atomic
def approve_po(po, user=None, at=None):
    po = _lock(po)
    if po.status != Status.DRAFT:
        raise InvalidStatusError(f"Chỉ duyệt được PO ở trạng thái Nháp ({po.code}: {po.get_status_display()})")
    if not po.lines.exists():
        raise ERPError(f"PO {po.code} chưa có dòng hàng")
    po.status = Status.APPROVED
    po.approved_by = user
    po.approved_at = at or timezone.now()
    po.save()
    return po


@transaction.atomic
def receive_po(po, quantities=None, user=None, at=None):
    """Receive goods for a PO.

    quantities: {line_id: qty}. None means receive all remaining quantities.
    """
    po = _lock(po)
    if po.status not in PurchaseOrder.OPEN_STATUSES:
        raise InvalidStatusError(f"PO {po.code} đang ở trạng thái {po.get_status_display()}, không thể nhận hàng")

    lines = {line.pk: line for line in PurchaseOrderLine.objects.select_for_update().filter(order=po)}
    if quantities is None:
        quantities = {pk: line.remaining_quantity for pk, line in lines.items()}

    received_any = False
    for line_id, qty in quantities.items():
        if not qty:
            continue
        line = lines.get(int(line_id))
        if line is None:
            raise ERPError(f"Dòng {line_id} không thuộc PO {po.code}")
        if qty < 0 or qty > line.remaining_quantity:
            raise ERPError(
                f"{line.product.sku}: số lượng nhận {qty} vượt số còn lại {line.remaining_quantity}"
            )
        stock.stock_in(line.product, po.warehouse, qty, line.unit_price,
                       reference=po.code, note=f"Nhận hàng từ {po.supplier.code}",
                       user=user, occurred_at=at)
        line.received_quantity += qty
        line.save(update_fields=["received_quantity"])
        received_any = True

    if not received_any:
        raise ERPError("Không có số lượng nào được nhận")

    if all(line.remaining_quantity == 0 for line in lines.values()):
        po.status = Status.RECEIVED
        po.received_date = timezone.localdate(at) if at else timezone.localdate()
    else:
        po.status = Status.PARTIAL
    po.save()
    return po


@transaction.atomic
def cancel_po(po, user=None):
    po = _lock(po)
    if po.status not in (Status.DRAFT, Status.APPROVED):
        raise InvalidStatusError(
            f"Không thể hủy PO {po.code} ở trạng thái {po.get_status_display()} (đã nhận hàng)"
        )
    po.status = Status.CANCELLED
    po.save()
    return po


def on_order_quantity(product, warehouse):
    """Quantity ordered from suppliers but not yet received."""
    lines = PurchaseOrderLine.objects.filter(
        product=product, order__warehouse=warehouse, order__status__in=PurchaseOrder.OPEN_STATUSES
    ).values_list("quantity", "received_quantity")
    return sum(q - r for q, r in lines)
