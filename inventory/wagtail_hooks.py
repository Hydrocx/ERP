from django.urls import path
from wagtail.admin.ui.tables import Column
from wagtail.permissions import register_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet, SnippetViewSetGroup

from core.admin_utils import MoneyColumn
from core.permissions import ReadOnlyPermissionPolicy

from . import admin_views
from .models import StockLevel, StockMovement

# Must run before any viewset looks the policy up.
register_permission_policy(StockLevel, ReadOnlyPermissionPolicy(StockLevel))
register_permission_policy(StockMovement, ReadOnlyPermissionPolicy(StockMovement))


class StockLevelViewSet(SnippetViewSet):
    model = StockLevel
    icon = "table"
    menu_label = "Tồn kho"
    index_view_class = admin_views.StockLevelIndexView
    list_display = [
        "product", "warehouse", "on_hand", "reserved",
        Column("available", label="Khả dụng"),
        Column("effective_reorder_point", label="ROP"),
        Column("status", label="Tình trạng"),
        MoneyColumn("stock_value", label="Giá trị tồn"),
    ]
    list_filter = ["warehouse", "product__category"]
    search_fields = ["product__sku", "product__name"]
    search_backend_name = None
    list_export = ["product", "warehouse", "on_hand", "reserved", "available", "avg_cost", "stock_value"]
    list_per_page = 50
    inspect_view_enabled = True

    def get_queryset(self, request):
        return self.model.objects.select_related("product", "warehouse")

    def get_urlpatterns(self):
        return super().get_urlpatterns() + [
            path("adjust/", admin_views.adjust_view, name="adjust"),
            path("transfer/", admin_views.transfer_view, name="transfer"),
        ]


class StockMovementViewSet(SnippetViewSet):
    model = StockMovement
    icon = "history"
    list_display = ["occurred_at", "movement_type", "product", "warehouse", "quantity",
                    "balance_after", "reference", "created_by"]
    list_filter = ["movement_type", "warehouse"]
    search_fields = ["product__sku", "product__name", "reference"]
    search_backend_name = None
    list_export = ["occurred_at", "movement_type", "product", "warehouse", "quantity",
                   "balance_after", "unit_cost", "reference", "note"]
    list_per_page = 50
    inspect_view_enabled = True

    def get_queryset(self, request):
        return self.model.objects.select_related("product", "warehouse", "created_by")


class InventoryGroup(SnippetViewSetGroup):
    menu_label = "Kho"
    menu_icon = "table"
    menu_order = 210
    items = (StockLevelViewSet, StockMovementViewSet)


register_snippet(InventoryGroup)
