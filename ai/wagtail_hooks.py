from wagtail.admin.ui.tables import Column
from wagtail.permissions import register_permission_policy
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet, SnippetViewSetGroup

from core.permissions import ReadOnlyPermissionPolicy
from forecast.models import DemandForecast, ReorderSuggestion

from .models import AICallLog

for _model in (AICallLog, DemandForecast, ReorderSuggestion):
    register_permission_policy(_model, ReadOnlyPermissionPolicy(_model))


class AICallLogViewSet(SnippetViewSet):
    model = AICallLog
    icon = "history"
    list_display = ["created_at", "feature", "model", "success", "input_tokens", "output_tokens",
                    "latency_ms", "user"]
    list_filter = ["feature", "success"]
    list_export = ["created_at", "feature", "model", "success", "input_tokens", "output_tokens",
                   "latency_ms", "error"]
    list_per_page = 50
    inspect_view_enabled = True

    def get_queryset(self, request):
        return self.model.objects.select_related("user")


class ReorderSuggestionViewSet(SnippetViewSet):
    model = ReorderSuggestion
    icon = "tasks"
    list_display = ["product", "warehouse", "supplier", "available", "reorder_point", "suggested_qty",
                    "ai_action", Column("recommended_qty", label="SL khuyến nghị"), "status", "purchase_order"]
    list_filter = ["status", "ai_action", "warehouse", "ai_flagged"]
    search_fields = ["product__sku", "product__name"]
    search_backend_name = None
    list_per_page = 50
    inspect_view_enabled = True

    def get_queryset(self, request):
        return self.model.objects.select_related("product", "warehouse", "supplier", "purchase_order")


class DemandForecastViewSet(SnippetViewSet):
    model = DemandForecast
    icon = "calendar-alt"
    list_display = ["run_date", "product", "warehouse", "forecast_daily", "seasonal_factor", "safety_stock",
                    "reorder_point", "available", "on_order", "stockout_days"]
    list_filter = ["run_date", "warehouse"]
    search_fields = ["product__sku", "product__name"]
    search_backend_name = None
    list_export = ["run_date", "product", "warehouse", "avg_daily", "recent_daily", "std_daily",
                   "seasonal_factor", "forecast_daily", "lead_time_days", "safety_stock", "reorder_point",
                   "order_up_to", "available", "on_order", "stockout_days"]
    list_per_page = 50
    inspect_view_enabled = True

    def get_queryset(self, request):
        return self.model.objects.select_related("product", "warehouse")


class AIGroup(SnippetViewSetGroup):
    menu_label = "AI & Dự báo"
    menu_icon = "wagtail"
    menu_order = 240
    items = (ReorderSuggestionViewSet, DemandForecastViewSet, AICallLogViewSet)


register_snippet(AIGroup)
