from django.urls import path
from wagtail import hooks
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from core.admin_utils import MoneyColumn, lock_non_draft_documents

from . import admin_views
from .models import SalesOrder


class SalesOrderViewSet(SnippetViewSet):
    model = SalesOrder
    icon = "upload"
    menu_label = "Bán hàng"
    menu_order = 230
    add_to_admin_menu = True
    list_display = ["code", "customer", "warehouse", "status", "order_date",
                    MoneyColumn("total_amount", label="Tổng tiền")]
    list_filter = ["status", "warehouse"]
    search_fields = ["code", "customer__name", "customer__code"]
    search_backend_name = None
    list_export = ["code", "customer", "warehouse", "status", "order_date", "shipped_at", "total_amount"]
    list_per_page = 50
    inspect_view_enabled = True
    inspect_view_class = admin_views.SalesOrderInspectView
    inspect_view_fields = ["customer", "warehouse", "status", "order_date", "confirmed_at", "shipped_at",
                           "created_by", "note"]

    def get_queryset(self, request):
        return self.model.objects.select_related("customer", "warehouse")

    def get_urlpatterns(self):
        return super().get_urlpatterns() + [
            path("confirm/<str:pk>/", admin_views.confirm_view, name="confirm"),
            path("ship/<str:pk>/", admin_views.ship_view, name="ship"),
            path("cancel/<str:pk>/", admin_views.cancel_view, name="cancel"),
        ]


register_snippet(SalesOrderViewSet)

_before_edit, _before_delete, _after_create = lock_non_draft_documents(SalesOrder)
hooks.register("before_edit_snippet", _before_edit)
hooks.register("before_delete_snippet", _before_delete)
hooks.register("after_create_snippet", _after_create)
