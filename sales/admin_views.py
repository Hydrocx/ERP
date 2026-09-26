from core.admin_actions import DocumentInspectView, action, snippet_url, status_action_view
from core.admin_utils import format_vnd

from . import services
from .models import SalesOrder

S = SalesOrder.Status

confirm_view = status_action_view(SalesOrder, "sales.confirm_salesorder", services.confirm_so,
                                  "Đã xác nhận {obj} và giữ hàng.")
ship_view = status_action_view(SalesOrder, "sales.ship_salesorder", services.ship_so,
                               "Đã xuất kho giao {obj}.")
cancel_view = status_action_view(SalesOrder, "sales.confirm_salesorder", services.cancel_so,
                                 "Đã hủy {obj}.")


class SalesOrderInspectView(DocumentInspectView):
    line_columns = [("Sản phẩm", "product"), ("Số lượng", "quantity"), ("Đơn giá", "unit_price"),
                    ("Thành tiền", "line_total")]

    def get_actions(self):
        so, user, actions = self.object, self.request.user, []
        if so.status == S.DRAFT and user.has_perm("sales.change_salesorder"):
            actions.append(action("Sửa", snippet_url(so, "edit", so.pk), method="get", style="button-secondary"))
        if so.status == S.DRAFT and user.has_perm("sales.confirm_salesorder"):
            actions.append(action("Xác nhận & giữ hàng", snippet_url(so, "confirm", so.pk)))
        if so.status == S.CONFIRMED and user.has_perm("sales.ship_salesorder"):
            actions.append(action("Xuất kho giao hàng", snippet_url(so, "ship", so.pk)))
        if so.status in (S.DRAFT, S.CONFIRMED) and user.has_perm("sales.confirm_salesorder"):
            actions.append(action("Hủy SO", snippet_url(so, "cancel", so.pk), style="no",
                                  confirm=f"Hủy {so.code}?"))
        return actions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_subtitle"] = f"{self.object.code} — {self.object.get_status_display()} — " \
                                   f"{format_vnd(self.object.total_amount)}"
        return context
