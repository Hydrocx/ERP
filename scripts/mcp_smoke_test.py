"""Kiểm tra Superset MCP server (gói `mcp-superset`) bằng một MCP client thật qua stdio.

    python scripts/mcp_smoke_test.py            # kiểm tra đọc + các chốt an toàn
    python scripts/mcp_smoke_test.py --write    # thêm: tạo một biểu đồ thử, lấy dữ liệu, rồi xóa

Cần file .env.mcp (SUPERSET_BASE_URL / SUPERSET_USERNAME / SUPERSET_PASSWORD) của tài khoản MCP chuyên dụng
(role Alpha, không phải admin). Xem docs/AI_ANALYTICS.md. Thoát với mã 1 nếu có kiểm tra thất bại.
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.mcp"
RESULTS = []


def server_command():
    if found := shutil.which("mcp-superset"):
        return found
    scripts = ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    return str(scripts / ("mcp-superset.exe" if sys.platform == "win32" else "mcp-superset"))


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def payload(result):
    """Dữ liệu có cấu trúc của một lần gọi công cụ."""
    data = getattr(result, "data", None)
    text = data if isinstance(data, str) else "\n".join(
        getattr(c, "text", "") or "" for c in getattr(result, "content", []))
    if data is not None and not isinstance(data, str):
        return data
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def dump(value, limit=300):
    return json.dumps(value, ensure_ascii=False, default=str)[:limit]


def rows_of(value):
    if isinstance(value, dict):
        return value.get("result") or value.get("data") or value.get("rows") or []
    return value if isinstance(value, list) else []


async def call(client, tool, args=None):
    return payload(await client.call_tool(tool, args or {}))


async def run(write):
    transport = StdioTransport(command=server_command(),
                               args=["--transport", "stdio", "--env-file", str(ENV_FILE)])
    async with Client(transport) as client:
        tools = {t.name for t in await client.list_tools()}
        check("Kết nối MCP qua stdio", len(tools) >= 100, f"{len(tools)} công cụ")

        databases = rows_of(await call(client, "superset_database_list"))
        names = [d.get("database_name") for d in databases if isinstance(d, dict)]
        check("Thấy kết nối CSDL 'Mini ERP (analytics)'", "Mini ERP (analytics)" in names, dump(names))
        database_id = next((d["id"] for d in databases if d.get("database_name") == "Mini ERP (analytics)"), None)

        datasets = rows_of(await call(client, "superset_dataset_list", {"get_all": True}))
        table_names = {d.get("table_name") for d in datasets if isinstance(d, dict)}
        check("Thấy các dataset analytics", {"mart_daily_sales", "mart_ai_forecast"} <= table_names,
              f"{len(table_names)} dataset")

        dashboards = rows_of(await call(client, "superset_dashboard_list", {"get_all": True}))
        slugs = {d.get("slug") for d in dashboards if isinstance(d, dict)}
        check("Thấy 2 dashboard", {"mini-erp-crm", "mini-erp-ai-insights"} <= slugs, dump(sorted(map(str, slugs))))

        # --- truy vấn đọc qua SQL Lab
        out = await call(client, "superset_sqllab_execute", {
            "database_id": database_id, "schema": "analytics",
            "sql": "SELECT count(*) AS n FROM analytics.mart_daily_sales"})
        check("SQL Lab: SELECT trên schema analytics", "n" in dump(out) or "count" in dump(out).lower(), dump(out, 160))

        # --- các chốt an toàn
        for label, sql in (("DELETE", "DELETE FROM analytics.mart_daily_sales"),
                           ("DROP", "DROP TABLE analytics.mart_daily_sales"),
                           ("đọc bảng gốc auth_user", "SELECT * FROM public.auth_user")):
            try:
                out = await call(client, "superset_sqllab_execute",
                                 {"database_id": database_id, "sql": sql, "schema": "analytics"})
                text = dump(out, 400).lower()
                blocked = any(k in text for k in ("error", "denied", "not allowed", "blocked", "permission",
                                                  "reject", "forbidden", "read-only", "readonly"))
                check(f"Chặn {label}", blocked, dump(out, 140))
            except ToolError as exc:
                check(f"Chặn {label}", True, str(exc).splitlines()[0][:140])

        user_tool = next((t for t in sorted(tools) if "user" in t and t.endswith("list")), None)
        if user_tool:
            try:
                out = await call(client, user_tool, {})
                text = dump(out, 300).lower()
                check("Tài khoản Alpha không quản lý được user", any(k in text for k in ("403", "forbidden", "unauthor", "denied", "not allowed", "error", "permission")), dump(out, 140))
            except ToolError as exc:
                check("Tài khoản Alpha không quản lý được user", True, str(exc).splitlines()[0][:140])

        if write:
            ds = next(d for d in datasets if d.get("table_name") == "mart_daily_sales")
            params = {"viz_type": "big_number_total", "datasource": f"{ds['id']}__table",
                      "metric": {"expressionType": "SQL", "sqlExpression": "SUM(revenue)", "label": "Doanh thu"},
                      "granularity_sqla": "sales_date",   # mcp-superset bắt buộc để filter dashboard hoạt động
                      "adhoc_filters": [], "time_range": "No filter"}
            # chart_get_data chỉ chạy được khi biểu đồ có query_context (UI tự lưu, API thì phải gửi kèm)
            query_context = {"datasource": {"id": ds["id"], "type": "table"}, "force": False,
                             "queries": [{"metrics": [params["metric"]], "columns": [], "filters": [],
                                          "row_limit": 1}],
                             "result_format": "json", "result_type": "full"}
            created = await call(client, "superset_chart_create", {
                "slice_name": "MCP smoke test (tự xóa)", "viz_type": "big_number_total",
                "datasource_id": ds["id"], "datasource_type": "table", "params": json.dumps(params),
                "query_context": json.dumps(query_context)})
            chart_id = (created.get("id") if isinstance(created, dict) else None) or \
                (created.get("result", {}).get("id") if isinstance(created, dict) else None)
            check("Tạo biểu đồ qua MCP", bool(chart_id), dump(created, 160))
            if chart_id:
                try:
                    data = await call(client, "superset_chart_get_data", {"chart_id": chart_id})
                    check("Lấy dữ liệu biểu đồ qua MCP", "Doanh thu" in dump(data, 2000), dump(data, 160))
                except ToolError as exc:
                    check("Lấy dữ liệu biểu đồ qua MCP", False, str(exc).splitlines()[0][:160])
                finally:                                     # luôn dọn biểu đồ thử
                    deleted = await call(client, "superset_chart_delete",
                                         {"chart_id": chart_id, "confirm_delete": True})
                    check("Xóa biểu đồ thử", True, dump(deleted, 100))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.write))
    passed = sum(RESULTS)
    print(f"\n{passed}/{len(RESULTS)} kiểm tra đạt")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
