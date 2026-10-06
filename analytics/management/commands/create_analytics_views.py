"""Tạo schema `analytics` và cấp quyền chỉ-đọc cho Apache Superset.

    python manage.py create_analytics_views                  # tạo/cập nhật view
    python manage.py create_analytics_views --grant-only     # chỉ cấp lại quyền

Mô hình bảo mật
---------------
Superset đăng nhập Postgres bằng role riêng (mặc định `superset_ro`):

* không phải superuser, không tạo được bảng, mặc định mọi giao dịch là READ ONLY;
* bị thu hồi quyền trên schema `public`, nên không đọc được `auth_user`
  (mật khẩu băm), `django_session` hay `ai_aicalllog` (nội dung gửi cho AI);
* chỉ có SELECT trên schema `analytics`.

Các view thuộc sở hữu của user ứng dụng nên vẫn đọc được bảng gốc
(PostgreSQL chạy view theo quyền của chủ view). Nói cách khác, schema
`analytics` là cửa duy nhất và chỉ để lộ đúng những cột đã chọn.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

SQL_FILE = Path(__file__).resolve().parent.parent.parent / "sql" / "analytics_views.sql"

GRANTS = """
-- Thu hồi mọi quyền mặc định rồi cấp lại đúng những gì cần
REVOKE ALL ON SCHEMA public FROM {role};
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};
REVOKE ALL ON SCHEMA analytics FROM {role};
REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM {role};

GRANT CONNECT ON DATABASE {database} TO {role};
GRANT USAGE ON SCHEMA analytics TO {role};
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {role};

-- View tạo sau này cũng tự được cấp SELECT
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO {role};

-- Chặn ghi ngay ở tầng kết nối
ALTER ROLE {role} SET default_transaction_read_only = on;
ALTER ROLE {role} SET statement_timeout = '120s';
ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
"""


class Command(BaseCommand):
    help = "Tạo schema analytics (view cho Superset) và cấp quyền chỉ-đọc."

    def add_arguments(self, parser):
        parser.add_argument("--role", default=settings.SUPERSET_DB_USER,
                            help="Role Postgres mà Superset dùng")
        parser.add_argument("--password", default=settings.SUPERSET_DB_PASSWORD,
                            help="Mật khẩu role (tạo mới nếu role chưa tồn tại)")
        parser.add_argument("--grant-only", action="store_true", help="Bỏ qua việc tạo view")
        parser.add_argument("--no-grant", action="store_true", help="Chỉ tạo view, không cấp quyền")

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            raise CommandError(
                "Lớp analytics chỉ chạy trên PostgreSQL (hiện tại: "
                f"{connection.vendor}). Đặt DATABASE_URL trỏ tới Postgres, ví dụ:\n"
                "  DATABASE_URL=postgres://erp:erp@127.0.0.1:5433/erp"
            )

        with connection.cursor() as cur:
            if not opts["grant_only"]:
                self.stdout.write("Đang tạo / cập nhật view trong schema analytics…")
                cur.execute(SQL_FILE.read_text(encoding="utf-8"))
                cur.execute("""
                    SELECT table_name FROM information_schema.views
                    WHERE table_schema = 'analytics' ORDER BY table_name
                """)
                views = [r[0] for r in cur.fetchall()]
                for name in views:
                    self.stdout.write(f"  ✓ analytics.{name}")
                self.stdout.write(self.style.SUCCESS(f"{len(views)} view sẵn sàng."))

            if opts["no_grant"]:
                return

            role, password = opts["role"], opts["password"]
            if not password:
                raise CommandError("Thiếu mật khẩu role. Đặt SUPERSET_DB_PASSWORD hoặc dùng --password.")

            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            if cur.fetchone():
                cur.execute(f"ALTER ROLE {_ident(role)} WITH LOGIN PASSWORD %s", [password])
                self.stdout.write(f"Cập nhật role {role}.")
            else:
                cur.execute(f"CREATE ROLE {_ident(role)} WITH LOGIN PASSWORD %s", [password])
                self.stdout.write(f"Tạo role {role}.")

            cur.execute(GRANTS.format(role=_ident(role),
                                      database=_ident(connection.settings_dict["NAME"])))
            self.stdout.write(self.style.SUCCESS(
                f"Đã cấp quyền chỉ-đọc trên schema analytics cho {role}; "
                "role này không đọc được schema public."
            ))


def _ident(name):
    """Bọc định danh SQL (không thể truyền qua tham số)."""
    if not name.replace("_", "").isalnum():
        raise CommandError(f"Tên không hợp lệ: {name!r}")
    return f'"{name}"'
