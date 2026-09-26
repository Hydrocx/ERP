from django.urls import reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.admin.ui.components import Component

from forecast.models import ReorderSuggestion
from inventory.models import StockLevel
from purchasing.models import PurchaseOrder
from sales.models import SalesOrder


class StockAlertPanel(Component):
    name = "erp_stock_alerts"
    order = 50
    template_name = "dashboard/admin/stock_alert_panel.html"

    def get_context_data(self, parent_context=None):
        levels = list(StockLevel.objects.select_related("product", "warehouse").filter(product__is_active=True))
        low = sorted((lv for lv in levels if lv.available <= lv.effective_reorder_point),
                     key=lambda lv: lv.available - lv.effective_reorder_point)
        return {
            "out_count": sum(1 for lv in low if lv.available <= 0),
            "low_count": len(low),
            "items": low[:8],
            "pending_suggestions": ReorderSuggestion.objects.filter(
                status=ReorderSuggestion.Status.PENDING).count(),
            "po_draft": PurchaseOrder.objects.filter(status=PurchaseOrder.Status.DRAFT).count(),
            "so_open": SalesOrder.objects.filter(
                status__in=[SalesOrder.Status.DRAFT, SalesOrder.Status.CONFIRMED]).count(),
            "app_url": reverse("dashboard:home"),
            "reorder_url": reverse("dashboard:reorder"),
            "stock_url": reverse(StockLevel.snippet_viewset.get_url_name("list")),
        }


@hooks.register("construct_homepage_panels")
def add_stock_alert_panel(request, panels):
    panels.insert(0, StockAlertPanel())


@hooks.register("register_admin_menu_item")
def register_app_menu_item():
    return MenuItem("Ứng dụng ERP", reverse("dashboard:home"), icon_name="desktop", order=100)
