from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse

from core.admin_actions import DocumentInspectView, action, inspect_url, snippet_url, status_action_view
from core.admin_utils import format_vnd
from core.exceptions import ERPError

from . import services
from .models import PurchaseOrder

S = PurchaseOrder.Status

approve_view = status_action_view(PurchaseOrder, "purchasing.approve_purchaseorder", services.approve_po,
                                  "Đã duyệt {obj}.")
cancel_view = status_action_view(PurchaseOrder, "purchasing.approve_purchaseorder", services.cancel_po,
                                 "Đã hủy {obj}.")


def receive_view(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not request.user.has_perm("purchasing.receive_purchaseorder"):
        raise PermissionDenied
    lines = list(po.lines.select_related("product").order_by("sort_order", "pk"))
    if request.method == "POST":
        try:
            quantities = {}
            for line in lines:
                raw = request.POST.get(f"qty_{line.pk}", "0").strip() or "0"
                quantities[line.pk] = int(raw)
            services.receive_po(po, quantities, user=request.user)
        except ValueError:
            messages.error(request, "Số lượng không hợp lệ.")
        except ERPError as exc:
            messages.error(request, str(exc))
        else:
            po.refresh_from_db()
            messages.success(request, f"Đã nhập kho theo {po.code} ({po.get_status_display()}).")
            return redirect(inspect_url(po))
    return TemplateResponse(request, "purchasing/admin/receive.html", {
        "po": po, "lines": [ln for ln in lines if ln.remaining_quantity > 0], "back_url": inspect_url(po),
    })


class PurchaseOrderInspectView(DocumentInspectView):
    line_columns = [("Sản phẩm", "product"), ("Số lượng", "quantity"), ("Đã nhận", "received_quantity"),
                    ("Đơn giá", "unit_price"), ("Thành tiền", "line_total")]

    def get_actions(self):
        po, user, actions = self.object, self.request.user, []
        if po.status == S.DRAFT and user.has_perm("purchasing.change_purchaseorder"):
            actions.append(action("Sửa", snippet_url(po, "edit", po.pk), method="get", style="button-secondary"))
        if po.status == S.DRAFT and user.has_perm("purchasing.approve_purchaseorder"):
            actions.append(action("Duyệt PO", snippet_url(po, "approve", po.pk)))
        if po.status in PurchaseOrder.OPEN_STATUSES and user.has_perm("purchasing.receive_purchaseorder"):
            actions.append(action("Nhận hàng", snippet_url(po, "receive", po.pk), method="get"))
        if po.status in (S.DRAFT, S.APPROVED) and user.has_perm("purchasing.approve_purchaseorder"):
            actions.append(action("Hủy PO", snippet_url(po, "cancel", po.pk), style="no",
                                  confirm=f"Hủy {po.code}?"))
        return actions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_subtitle"] = f"{self.object.code} — {self.object.get_status_display()} — " \
                                   f"{format_vnd(self.object.total_amount)}"
        return context
