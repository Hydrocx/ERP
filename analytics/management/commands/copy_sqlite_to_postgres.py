"""Sao chép toàn bộ dữ liệu từ db.sqlite3 sang PostgreSQL (dùng khi chuyển sang stack phân tích).

    # DATABASE_URL đã trỏ vào Postgres
    python manage.py migrate
    python manage.py copy_sqlite_to_postgres

Cách làm: đọc từng bảng của SQLite rồi ghi thẳng vào bảng cùng tên bên Postgres
trong MỘT giao dịch. Django tạo khóa ngoại dạng DEFERRABLE INITIALLY DEFERRED
nên thứ tự chèn không quan trọng; ràng buộc chỉ được kiểm tra khi commit.

So với dumpdata/loaddata, cách này nhanh hơn nhiều và xử lý được cả kế thừa
nhiều bảng của Wagtail (AIReportPage kế thừa Page).
"""

import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

# Bảng không sao chép:
#   django_migrations  - do `migrate` tự ghi
#   django_session     - dữ liệu tạm
#   wagtailsearch_*    - chỉ mục tìm kiếm, cấu trúc khác nhau giữa SQLite và
#                        Postgres (Postgres có thêm cột SearchVectorField);
#                        dựng lại bằng `python manage.py update_index`
SKIP = {"django_migrations", "django_session", "sqlite_sequence",
        "wagtailsearch_indexentry"}
BATCH = 2000


class Command(BaseCommand):
    help = "Sao chép dữ liệu từ db.sqlite3 sang PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument("--sqlite", default=str(settings.BASE_DIR / "db.sqlite3"))
        parser.add_argument("--yes", action="store_true", help="Không hỏi xác nhận")

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            raise CommandError("DATABASE_URL phải trỏ tới PostgreSQL khi chạy lệnh này.")
        source = Path(opts["sqlite"])
        if not source.exists():
            raise CommandError(f"Không tìm thấy {source}")

        target = connection.settings_dict
        self.stdout.write(f"Nguồn : {source}")
        self.stdout.write(f"Đích  : postgres://{target['USER']}@{target['HOST']}:{target['PORT']}/{target['NAME']}")
        if not opts["yes"]:
            self.stdout.write(self.style.WARNING(
                "Toàn bộ dữ liệu hiện có trong CSDL đích sẽ bị xóa trước khi chép."))
            if input("Tiếp tục? [y/N] ").strip().lower() not in {"y", "yes"}:
                raise CommandError("Đã hủy.")

        sqlite = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        sqlite.row_factory = sqlite3.Row
        try:
            self._copy(sqlite)
        finally:
            sqlite.close()

    # ------------------------------------------------------------------------
    def _copy(self, sqlite):
        src_tables = {r[0] for r in sqlite.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")} - SKIP
        with connection.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """)
            dst_tables = {r[0] for r in cur.fetchall()}

        tables = sorted(src_tables & dst_tables)
        missing = sorted(src_tables - dst_tables)
        if missing:
            self.stdout.write(self.style.WARNING(
                f"Bỏ qua {len(missing)} bảng không có bên Postgres: {', '.join(missing[:5])}…"))

        total = 0
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("SET CONSTRAINTS ALL DEFERRED")
            cur.execute(f"TRUNCATE {', '.join(_q(t) for t in tables)} RESTART IDENTITY CASCADE")

            for table in tables:
                rows = sqlite.execute(f'SELECT * FROM "{table}"').fetchall()
                if not rows:
                    continue
                types = _column_types(cur, table)
                columns = [c for c in rows[0].keys() if c in types]
                if skipped := [c for c in rows[0].keys() if c not in types]:
                    self.stdout.write(self.style.WARNING(
                        f"  {table}: bỏ qua cột không có bên Postgres: {', '.join(skipped)}"))
                placeholders = ", ".join(
                    "%s::jsonb" if types.get(c) == "jsonb" else "%s" for c in columns
                )
                sql = (f"INSERT INTO {_q(table)} ({', '.join(_q(c) for c in columns)}) "
                       f"VALUES ({placeholders})")
                data = [tuple(_convert(row[c], types.get(c)) for c in columns) for row in rows]
                for i in range(0, len(data), BATCH):
                    cur.executemany(sql, data[i:i + BATCH])
                total += len(data)
                self.stdout.write(f"  {table}: {len(data):,} dòng")

            self.stdout.write("Đặt lại bộ đếm khóa chính…")
            _reset_sequences(cur)

        self.stdout.write(self.style.SUCCESS(
            f"\nĐã chép {total:,} dòng trong {len(tables)} bảng."))


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _column_types(cur, table):
    cur.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
    """, [table])
    return dict(cur.fetchall())


def _convert(value, pg_type):
    """SQLite không có kiểu bool; Django lưu 0/1 vào cột boolean của Postgres."""
    if value is not None and pg_type == "boolean":
        return bool(value)
    return value


def _reset_sequences(cur):
    cur.execute("""
        SELECT c.table_name, c.column_name,
               pg_get_serial_sequence(quote_ident(c.table_name), c.column_name)
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) IS NOT NULL
    """)
    for table, column, sequence in cur.fetchall():
        cur.execute(
            f"SELECT setval(%s, COALESCE((SELECT max({_q(column)}) FROM {_q(table)}), 0) + 1, false)",
            [sequence],
        )
