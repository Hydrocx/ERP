from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("inventory/", views.inventory, name="inventory"),
    path("products/<str:sku>/", views.product_detail, name="product"),
    path("products/<str:sku>/ai/", views.product_ai, name="product_ai"),
    path("reorder/", views.reorder, name="reorder"),
    path("reorder/run/", views.reorder_run, name="reorder_run"),
    path("reports/", views.reports, name="reports"),
    path("invoice/", views.invoice, name="invoice"),
    path("assistant/", views.assistant, name="assistant"),
    path("assistant/reset/", views.assistant_reset, name="assistant_reset"),
]
