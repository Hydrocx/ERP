"""Shared pieces for document detail pages and status-changing actions in the Wagtail admin."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from wagtail.snippets.views.snippets import InspectView

from .exceptions import ERPError


def snippet_url(obj_or_model, name, *args):
    model = obj_or_model if isinstance(obj_or_model, type) else type(obj_or_model)
    return reverse(model.snippet_viewset.get_url_name(name), args=args)


def inspect_url(obj):
    return snippet_url(obj, "inspect", obj.pk)


def action(label, url, *, method="post", style="", confirm=""):
    return {"label": label, "url": url, "method": method, "style": style, "confirm": confirm}


def status_action_view(model, permission, func, success_message):
    """POST view that runs a service function on one document and redirects back to its detail page."""

    @require_POST
    def view(request, pk):
        obj = get_object_or_404(model, pk=pk)
        if not request.user.has_perm(permission):
            raise PermissionDenied
        try:
            func(obj, user=request.user)
        except ERPError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, success_message.format(obj=obj))
        return redirect(inspect_url(obj))

    return view


class DocumentInspectView(InspectView):
    """Inspect view that also shows document lines and the actions allowed for the current user."""

    template_name = "core/admin/document_inspect.html"
    line_columns = []  # [(label, attribute)]

    def get_field_display_value(self, field_name, field):
        value = super().get_field_display_value(field_name, field)
        return "–" if value is None or value == "" else value

    def get_lines(self):
        return self.object.lines.select_related("product")

    def get_actions(self):
        return []

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = [[getattr(line, attr) for _, attr in self.line_columns] for line in self.get_lines()]
        context.update(
            line_headers=[label for label, _ in self.line_columns],
            line_rows=rows,
            document_actions=self.get_actions(),
        )
        return context
