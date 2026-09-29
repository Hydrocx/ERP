"""One command for a fresh clone: database, demo data, demo accounts, forecast, sample report.

    python manage.py setup_demo            # ~7 min (400 days, includes last year's season)
    python manage.py setup_demo --quick    # ~1 min (60 days, no seasonality)

The database (db.sqlite3) and .env are not in git, so every developer runs this once.
"""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

from catalog.models import Product

from .setup_roles import DEMO_PASSWORD, DEMO_USERS


class Command(BaseCommand):
    help = "Khởi tạo môi trường demo: migrate, dữ liệu mẫu, tài khoản demo, dự báo, báo cáo mẫu."

    def add_arguments(self, parser):
        parser.add_argument("--quick", action="store_true", help="Chỉ mô phỏng 60 ngày (nhanh, không có mùa vụ)")
        parser.add_argument("--reset", action="store_true", help="Xóa dữ liệu ERP hiện có và tạo lại")

    def step(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n==> {text}"))

    def handle(self, *args, **opts):
        self.step("Tạo / cập nhật database")
        call_command("migrate", interactive=False, verbosity=0)

        if Product.objects.exists() and not opts["reset"]:
            self.step("Đã có dữ liệu mẫu, bỏ qua (dùng --reset để tạo lại)")
            self._ensure_admin()
        else:
            days = 60 if opts["quick"] else 400
            self.step(f"Tạo dữ liệu mẫu {days} ngày (có thể mất vài phút)")
            call_command("seed_data", days=days, admin=True, reset=opts["reset"])

        self.step("Tạo nhóm quyền và tài khoản demo")
        call_command("setup_roles", demo_users=True, verbosity=0)

        self.step("Chạy dự báo nhập hàng (không gọi AI)")
        call_command("run_forecast", no_ai=True)

        self.step("Tạo báo cáo tuần mẫu (không gọi AI)")
        call_command("generate_weekly_report", no_ai=True, user="admin")

        self.stdout.write(self.style.SUCCESS("\nXong! Chạy: python manage.py runserver  →  http://127.0.0.1:8000/app/"))
        self.stdout.write("Tài khoản (chỉ dùng thử):")
        self.stdout.write("  admin / admin123 (toàn quyền)")
        for username, role in DEMO_USERS.items():
            self.stdout.write(f"  {username} / {DEMO_PASSWORD} ({role})")

    def _ensure_admin(self):
        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", "admin123")
