"""Tạo kết nối, dataset, biểu đồ và dashboard trong Apache Superset qua REST API.

    python manage.py provision_superset

Chạy lại được nhiều lần: thứ đã tồn tại thì cập nhật, không tạo trùng.

Luồng dữ liệu:
    PostgreSQL (schema analytics, view chỉ-đọc)
        -> Superset Database "Mini ERP (analytics)"  (role superset_ro)
        -> Dataset cho từng view
        -> Chart
        -> Dashboard "Mini ERP · Kinh doanh & CRM"
"""

import json
import os

from django.core.management.base import BaseCommand, CommandError

from analytics import dashboard_spec as spec
from analytics.superset_api import SupersetClient, SupersetError


class Command(BaseCommand):
    help = "Tạo kết nối / dataset / biểu đồ / dashboard trong Superset."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=os.environ.get("SUPERSET_URL", "http://127.0.0.1:8089"))
        parser.add_argument("--user", default=os.environ.get("SUPERSET_ADMIN_USER", "admin"))
        parser.add_argument("--password", default=os.environ.get("SUPERSET_ADMIN_PASSWORD", ""))
        parser.add_argument("--db-host", default=os.environ.get("ERP_DB_HOST", "erp_db"),
                            help="Host Postgres nhìn từ bên trong container Superset")
        parser.add_argument("--db-port", default=os.environ.get("ERP_DB_PORT", "5432"))
        parser.add_argument("--db-name", default=os.environ.get("POSTGRES_DB", "erp"))
        parser.add_argument("--db-user", default=os.environ.get("SUPERSET_DB_USER", "superset_ro"))
        parser.add_argument("--db-password", default=os.environ.get("SUPERSET_DB_PASSWORD", ""))

    def handle(self, *args, **opts):
        if not opts["password"]:
            raise CommandError("Thiếu mật khẩu admin Superset (SUPERSET_ADMIN_PASSWORD hoặc --password).")
        if not opts["db_password"]:
            raise CommandError("Thiếu mật khẩu role chỉ-đọc (SUPERSET_DB_PASSWORD hoặc --db-password).")

        try:
            client = SupersetClient(opts["url"], opts["user"], opts["password"])
        except SupersetError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Đã đăng nhập Superset tại {opts['url']}")

        try:
            database_id = self._database(client, opts)
            datasets = self._datasets(client, database_id)
            charts = self._charts(client, datasets)
            url = self._dashboard(client, charts)
        except SupersetError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"\nHoàn tất. Mở dashboard: {opts['url']}{url}"
        ))

    # ------------------------------------------------------------------ bước 1
    def _database(self, client, opts):
        uri = (f"postgresql+psycopg2://{opts['db_user']}:{opts['db_password']}"
               f"@{opts['db_host']}:{opts['db_port']}/{opts['db_name']}")
        payload = {
            "database_name": spec.DATABASE_NAME,
            "sqlalchemy_uri": uri,
            "expose_in_sqllab": True,
            "allow_ctas": False,       # không cho tạo bảng từ SQL Lab
            "allow_cvas": False,
            "allow_dml": False,        # không cho INSERT/UPDATE/DELETE
            "allow_run_async": False,
            "cache_timeout": 300,
            "extra": json.dumps({
                "metadata_params": {},
                "engine_params": {"connect_args": {"options": "-c statement_timeout=120000"}},
                "schemas_allowed_for_file_upload": [],
            }),
        }
        existing = client.find("database", "database_name", spec.DATABASE_NAME)
        if existing:
            client.put(f"/api/v1/database/{existing}", payload)
            self.stdout.write(f"  ✓ Cập nhật kết nối CSDL #{existing}")
            return existing
        created = client.post("/api/v1/database/", payload)["id"]
        self.stdout.write(f"  ✓ Tạo kết nối CSDL #{created} (role chỉ-đọc {opts['db_user']})")
        return created

    # ------------------------------------------------------------------ bước 2
    def _datasets(self, client, database_id):
        self.stdout.write("Dataset:")
        result = {}
        for view in spec.DATASETS:
            existing = client.find("dataset", "table_name", view)
            if existing:
                result[view] = existing
                self.stdout.write(f"  · {view} (đã có, #{existing})")
                continue
            created = client.post("/api/v1/dataset/", {
                "database": database_id, "schema": spec.SCHEMA, "table_name": view,
            })["id"]
            result[view] = created
            self.stdout.write(f"  ✓ {view} (#{created})")
        return result

    # ------------------------------------------------------------------ bước 3
    def _charts(self, client, datasets):
        self.stdout.write("Biểu đồ:")
        result = {}
        for chart in spec.CHARTS:
            dataset_id = datasets[chart["dataset"]]
            params = {
                "datasource": f"{dataset_id}__table",
                "viz_type": chart["viz_type"],
                "adhoc_filters": [],
                **chart["params"],
            }
            payload = {
                "slice_name": chart["name"],
                "viz_type": chart["viz_type"],
                "datasource_id": dataset_id,
                "datasource_type": "table",
                "params": json.dumps(params, ensure_ascii=False),
            }
            existing = client.find("chart", "slice_name", chart["name"])
            if existing:
                client.put(f"/api/v1/chart/{existing}", payload)
                result[chart["name"]] = existing
                self.stdout.write(f"  ✓ {chart['name']} (cập nhật #{existing})")
            else:
                created = client.post("/api/v1/chart/", payload)["id"]
                result[chart["name"]] = created
                self.stdout.write(f"  ✓ {chart['name']} (#{created})")
        return result

    # ------------------------------------------------------------------ bước 4
    def _dashboard(self, client, charts):
        position = _build_position(charts)
        payload = {
            "dashboard_title": spec.DASHBOARD_TITLE,
            "slug": spec.DASHBOARD_SLUG,
            "published": True,
            "position_json": json.dumps(position, ensure_ascii=False),
            "json_metadata": json.dumps({
                "color_scheme": "supersetColors",
                "refresh_frequency": 0,
                "expanded_slices": {},
                "chart_configuration": {},
                "default_filters": "{}",
            }),
        }
        existing = client.find("dashboard", "slug", spec.DASHBOARD_SLUG)
        if existing:
            client.put(f"/api/v1/dashboard/{existing}", payload)
            self.stdout.write(f"Dashboard: cập nhật #{existing}")
            dashboard_id = existing
        else:
            dashboard_id = client.post("/api/v1/dashboard/", payload)["id"]
            self.stdout.write(f"Dashboard: tạo mới #{dashboard_id}")

        # Gắn biểu đồ vào dashboard (quan hệ nhiều-nhiều, position_json chưa đủ)
        for chart_id in charts.values():
            client.put(f"/api/v1/chart/{chart_id}", {"dashboards": [dashboard_id]})
        return f"/superset/dashboard/{spec.DASHBOARD_SLUG}/"


def _build_position(charts):
    """Dựng cây bố cục của Superset: ROOT -> GRID -> ROW -> CHART."""
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID",
                      "meta": {"text": spec.DASHBOARD_TITLE}},
    }
    by_name = {c["name"]: c for c in spec.CHARTS}

    for row_index, row in enumerate(spec.ROWS, start=1):
        row_id = f"ROW-{row_index}"
        position["GRID_ID"]["children"].append(row_id)
        position[row_id] = {
            "type": "ROW", "id": row_id, "children": [],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        for name in row:
            chart = by_name[name]
            chart_id = f"CHART-{_slug(name)}"
            position[row_id]["children"].append(chart_id)
            position[chart_id] = {
                "type": "CHART", "id": chart_id, "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {
                    "chartId": charts[name],
                    "sliceName": name,
                    "width": chart["width"],
                    "height": chart["height"],
                    "uuid": None,
                },
            }
    return position


def _slug(text):
    return "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in text).strip("-")[:40]
