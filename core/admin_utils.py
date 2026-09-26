from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from wagtail.admin.ui.tables import Column


def format_vnd(value):
    return f"{value:,.0f} ₫".replace(",", ".") if value is not None else ""


class MoneyColumn(Column):
    """Show a Decimal as 1.234.567 ₫."""

    def get_value(self, instance):
        return format_vnd(super().get_value(instance))


def lock_non_draft_documents(models):
    """Build before_edit/before_delete/after_create snippet hooks for documents with a status.

    Only DRAFT documents may be edited or deleted; status changes go through services.
    """

    def _index_url(model):
        return reverse(model.snippet_viewset.get_url_name("list"))

    def before_edit(request, instance):
        if isinstance(instance, models) and not instance.is_editable:
            messages.warning(
                request,
                f"{instance} đang ở trạng thái \"{instance.get_status_display()}\", "
                "chỉ sửa được chứng từ Nháp.",
            )
            return redirect(reverse(type(instance).snippet_viewset.get_url_name("inspect"), args=[instance.pk]))

    def before_delete(request, instances):
        blocked = [obj for obj in instances if isinstance(obj, models) and not obj.is_editable]
        if blocked:
            messages.error(
                request, "Chỉ xóa được chứng từ Nháp: " + ", ".join(str(obj) for obj in blocked)
            )
            return redirect(_index_url(type(blocked[0])))

    def after_create(request, instance):
        if isinstance(instance, models) and not instance.created_by_id:
            instance.created_by = request.user
            instance.save(update_fields=["created_by"])

    return before_edit, before_delete, after_create
