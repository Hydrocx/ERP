"""Create the ERP role groups (idempotent) and optionally demo users."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from wagtail.models import GroupApprovalTask, GroupPagePermission

VIEW_CATALOG = ["catalog.view_product", "catalog.view_category", "catalog.view_supplier",
                "catalog.view_warehouse", "catalog.view_customer"]
VIEW_STOCK = ["inventory.view_stocklevel", "inventory.view_stockmovement"]

ROLES = {
    "Quản lý": [
        "wagtailadmin.access_admin",
        *[f"catalog.{a}_{m}" for m in ("product", "category", "supplier", "warehouse", "customer")
          for a in ("add", "change", "delete", "view")],
        *VIEW_STOCK, "inventory.adjust_stocklevel",
        *[f"purchasing.{a}_purchaseorder" for a in ("add", "change", "delete", "view", "approve", "receive")],
        *[f"sales.{a}_salesorder" for a in ("add", "change", "delete", "view", "confirm", "ship")],
        "forecast.view_reordersuggestion", "forecast.change_reordersuggestion", "forecast.view_demandforecast",
        "ai.view_aicalllog", "ai.use_assistant", "ai.generate_report", "ai.run_forecast", "ai.change_aisettings",
    ],
    "Thủ kho": [
        "wagtailadmin.access_admin", *VIEW_CATALOG, *VIEW_STOCK, "inventory.adjust_stocklevel",
        "purchasing.view_purchaseorder", "purchasing.receive_purchaseorder",
        "sales.view_salesorder", "sales.ship_salesorder", "ai.use_assistant",
    ],
    "Mua hàng": [
        "wagtailadmin.access_admin", *VIEW_CATALOG, *VIEW_STOCK,
        "catalog.add_product", "catalog.change_product", "catalog.add_supplier", "catalog.change_supplier",
        *[f"purchasing.{a}_purchaseorder" for a in ("add", "change", "delete", "view")],
        "forecast.view_reordersuggestion", "forecast.change_reordersuggestion", "forecast.view_demandforecast",
        "ai.use_assistant", "ai.run_forecast",
    ],
    "Bán hàng": [
        "wagtailadmin.access_admin", *VIEW_CATALOG, *VIEW_STOCK,
        "catalog.add_customer", "catalog.change_customer",
        *[f"sales.{a}_salesorder" for a in ("add", "change", "delete", "view", "confirm")],
        "ai.use_assistant",
    ],
}

DEMO_USERS = {"quanly": "Quản lý", "thukho": "Thủ kho", "muahang": "Mua hàng", "banhang": "Bán hàng"}
DEMO_PASSWORD = "demo12345"


def get_permission(label):
    app_label, codename = label.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


class Command(BaseCommand):
    help = "Tạo nhóm quyền Quản lý / Thủ kho / Mua hàng / Bán hàng."

    def add_arguments(self, parser):
        parser.add_argument("--demo-users", action="store_true",
                            help=f"Tạo user demo {', '.join(DEMO_USERS)} (mật khẩu {DEMO_PASSWORD})")

    def handle(self, *args, **opts):
        for name, labels in ROLES.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set([get_permission(label) for label in labels])
            self.stdout.write(f"Nhóm {name}: {len(labels)} quyền")

        self.setup_report_permissions(Group.objects.get(name="Quản lý"))

        if opts["demo_users"]:
            User = get_user_model()
            for username, group_name in DEMO_USERS.items():
                user, created = User.objects.get_or_create(
                    username=username, defaults={"email": f"{username}@example.com", "first_name": group_name})
                if created:
                    user.set_password(DEMO_PASSWORD)
                    user.save()
                user.groups.add(Group.objects.get(name=group_name))
            self.stdout.write(self.style.WARNING(
                f"User demo: {', '.join(DEMO_USERS)} / {DEMO_PASSWORD} (chỉ dùng dev)"))
        self.stdout.write(self.style.SUCCESS("Hoàn tất phân quyền."))

    def setup_report_permissions(self, managers):
        """Managers may edit/publish report pages and approve them in the moderation workflow."""
        from reports.services import get_report_index, restrict_to_logged_in

        index = get_report_index()
        restrict_to_logged_in(index)
        for codename in ("add_page", "change_page", "publish_page"):
            GroupPagePermission.objects.get_or_create(
                group=managers, page=index,
                permission=Permission.objects.get(content_type__app_label="wagtailcore", codename=codename),
            )
        for task in GroupApprovalTask.objects.filter(active=True):
            task.groups.add(managers)
