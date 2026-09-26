from django.urls import path
from wagtail import hooks
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from core.admin_utils import MoneyColumn, lock_non_draft_documents

from . import admin_views
from .models import PurchaseOrder


class PurchaseOrderViewSet(SnippetViewSet):
    model = PurchaseOrder
    icon = "download"
    menu_label = "Mua hàng"
    menu_order = 220
    add_to_admin_menu = True
    list_display = ["code", "supplier", "warehouse", "status", "source", "order_date",
                    "expected_date", MoneyColumn("total_amount", label="Tổng tiền")]
    list_filter = ["status", "source", "supplier", "warehouse"]
    search_fields = ["code", "supplier__name", "supplier__code"]
    search_backend_name = None
    list_export = ["code", "supplier", "warehouse", "status", "source", "order_date",
                   "expected_date", "received_date", "total_amount"]
    list_per_page = 50
    inspect_view_enabled = True
    inspect_view_class = admin_views.PurchaseOrderInspectView
    inspect_view_fields = ["supplier", "warehouse", "status", "source", "order_date", "expected_date",
                           "received_date", "created_by", "approved_by", "approved_at", "note"]

    def get_queryset(self, request):
        return self.model.objects.select_related("supplier", "warehouse")

    def get_urlpatterns(self):
        return super().get_urlpatterns() + [
            path("approve/<str:pk>/", admin_views.approve_view, name="approve"),
            path("cancel/<str:pk>/", admin_views.cancel_view, name="cancel"),
            path("receive/<str:pk>/", admin_views.receive_view, name="receive"),
        ]


register_snippet(PurchaseOrderViewSet)

_before_edit, _before_delete, _after_create = lock_non_draft_documents(PurchaseOrder)
hooks.register("before_edit_snippet", _before_edit)
hooks.register("before_delete_snippet", _before_delete)
hooks.register("after_create_snippet", _after_create)
