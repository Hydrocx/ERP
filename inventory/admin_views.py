from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.functional import cached_property
from wagtail.admin.widgets import HeaderButton
from wagtail.snippets.views.snippets import IndexView

from catalog.models import Product, Warehouse
from core.admin_actions import snippet_url
from core.exceptions import ERPError

from . import services
from .models import StockLevel

PERMISSION = "inventory.adjust_stocklevel"


class AdjustForm(forms.Form):
    product = forms.ModelChoiceField(Product.objects.filter(is_active=True), label="Sản phẩm")
    warehouse = forms.ModelChoiceField(Warehouse.objects.filter(is_active=True), label="Kho")
    counted_quantity = forms.IntegerField(label="Số lượng thực tế (kiểm kê)", min_value=0)
    reason = forms.CharField(label="Lý do", max_length=200)


class TransferForm(forms.Form):
    product = forms.ModelChoiceField(Product.objects.filter(is_active=True), label="Sản phẩm")
    from_warehouse = forms.ModelChoiceField(Warehouse.objects.filter(is_active=True), label="Từ kho")
    to_warehouse = forms.ModelChoiceField(Warehouse.objects.filter(is_active=True), label="Đến kho")
    quantity = forms.IntegerField(label="Số lượng", min_value=1)
    reference = forms.CharField(label="Số chứng từ", max_length=50, required=False)


def _form_view(request, form_class, title, submit_label, help_text, handle):
    if not request.user.has_perm(PERMISSION):
        raise PermissionDenied
    back_url = snippet_url(StockLevel, "list")
    form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            message = handle(form.cleaned_data, request.user)
        except ERPError as exc:
            form.add_error(None, str(exc))
            messages.error(request, str(exc))
        else:
            messages.success(request, message)
            return redirect(back_url)
    return TemplateResponse(request, "inventory/admin/stock_form.html", {
        "form": form, "title": title, "submit_label": submit_label, "help_text": help_text,
        "back_url": back_url,
    })


def adjust_view(request):
    def handle(data, user):
        movement = services.adjust_stock(data["product"], data["warehouse"], data["counted_quantity"],
                                         reason=data["reason"], user=user)
        if movement is None:
            return "Số lượng không thay đổi."
        return f"Đã điều chỉnh {data['product'].sku} tại {data['warehouse'].code}: {movement.quantity:+d}."

    return _form_view(request, AdjustForm, "Điều chỉnh tồn kho", "Ghi điều chỉnh",
                      "Nhập số lượng đếm thực tế; hệ thống ghi phiếu điều chỉnh chênh lệch.", handle)


def transfer_view(request):
    def handle(data, user):
        services.transfer_stock(data["product"], data["from_warehouse"], data["to_warehouse"],
                                data["quantity"], reference=data["reference"], user=user)
        return (f"Đã chuyển {data['quantity']} {data['product'].sku} từ {data['from_warehouse'].code} "
                f"đến {data['to_warehouse'].code}.")

    return _form_view(request, TransferForm, "Chuyển kho", "Chuyển kho", "", handle)


class StockLevelIndexView(IndexView):
    @cached_property
    def header_buttons(self):
        buttons = super().header_buttons
        if self.request.user.has_perm(PERMISSION):
            buttons += [
                HeaderButton("Điều chỉnh tồn", url=snippet_url(StockLevel, "adjust"), icon_name="edit"),
                HeaderButton("Chuyển kho", url=snippet_url(StockLevel, "transfer"), icon_name="arrow-right"),
            ]
        return buttons
