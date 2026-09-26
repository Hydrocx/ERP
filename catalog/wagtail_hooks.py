from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet, SnippetViewSetGroup

from core.admin_utils import MoneyColumn

from .models import Category, Customer, Product, Supplier, Warehouse


class ProductViewSet(SnippetViewSet):
    model = Product
    icon = "tag"
    list_display = [
        "sku", "name", "category", "unit",
        MoneyColumn("cost_price", label="Giá nhập"),
        MoneyColumn("sale_price", label="Giá bán"),
        "reorder_point", "is_active",
    ]
    list_filter = ["category", "default_supplier", "is_active"]
    search_fields = ["sku", "name"]
    search_backend_name = None
    list_export = ["sku", "name", "category", "unit", "cost_price", "sale_price",
                   "reorder_point", "safety_stock", "default_supplier", "is_active"]
    list_per_page = 50
    inspect_view_enabled = True

    def get_queryset(self, request):
        return self.model.objects.select_related("category__parent")


class CategoryViewSet(SnippetViewSet):
    model = Category
    icon = "folder-open-inverse"
    list_display = ["name", "parent"]
    search_fields = ["name"]
    search_backend_name = None


class SupplierViewSet(SnippetViewSet):
    model = Supplier
    icon = "site"
    list_display = ["code", "name", "contact_person", "phone", "lead_time_days", "rating", "is_active"]
    list_filter = ["is_active", "rating"]
    search_fields = ["code", "name", "contact_person", "email"]
    search_backend_name = None
    list_export = ["code", "name", "contact_person", "email", "phone", "lead_time_days", "rating"]
    inspect_view_enabled = True


class WarehouseViewSet(SnippetViewSet):
    model = Warehouse
    icon = "home"
    list_display = ["code", "name", "address", "manager", "is_active"]
    search_fields = ["code", "name"]
    search_backend_name = None


class CustomerViewSet(SnippetViewSet):
    model = Customer
    icon = "user"
    list_display = ["code", "name", "phone", "email", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["code", "name", "phone", "email"]
    search_backend_name = None
    list_export = ["code", "name", "email", "phone", "address", "tax_code"]
    list_per_page = 50
    inspect_view_enabled = True


class CatalogGroup(SnippetViewSetGroup):
    menu_label = "Danh mục"
    menu_icon = "folder-open-1"
    menu_order = 200
    items = (ProductViewSet, CategoryViewSet, SupplierViewSet, WarehouseViewSet, CustomerViewSet)


register_snippet(CatalogGroup)
