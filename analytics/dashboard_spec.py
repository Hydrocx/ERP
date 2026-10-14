"""Khai báo dataset, biểu đồ và bố cục dashboard sẽ được tạo trong Superset.

Tách riêng khỏi lệnh provisioning để dễ đọc và dễ sửa: muốn thêm biểu đồ thì
thêm một mục vào CHARTS rồi chạy lại `python manage.py provision_superset`.
"""

DATABASE_NAME = "Mini ERP (analytics)"
SCHEMA = "analytics"
DASHBOARD_TITLE = "Mini ERP · Kinh doanh & CRM"
DASHBOARD_SLUG = "mini-erp-crm"

#: Các view được đưa vào Superset thành dataset.
DATASETS = [
    "mart_daily_sales",
    "mart_customer_rfm",
    "mart_inventory_status",
    "mart_supplier_performance",
    "mart_reorder_suggestion",
    "mart_ai_usage",
    "fct_sales_line",
    "fct_purchase_line",
    "fct_stock_movement",
    "dim_customer",
    "dim_product",
    "dim_warehouse",
    "dim_supplier",
    # Kết quả mô hình AI / ML (xem docs/AI_ANALYTICS.md)
    "mart_ai_model_runs",
    "mart_ai_model_metrics",
    "mart_ai_forecast",
    "mart_ai_customer_score",
    "mart_ai_anomaly",
    "mart_ai_basket_rules",
    "mart_ai_insight",
]


def metric(column, aggregate="SUM", label=None):
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": column},
        "aggregate": aggregate,
        "label": label or f"{aggregate}({column})",
        "optionName": f"metric_{aggregate.lower()}_{column}",
    }


def billions(column, label):
    """Tiền tệ đổi sang tỷ đồng: số nguyên VNĐ quá dài, bị cắt chữ và chồng nhãn trục."""
    return {
        "expressionType": "SQL",
        "sqlExpression": f"SUM({column}) / 1e9",
        "label": label,
        "optionName": f"metric_billions_{column}",
    }


def count_of(column, label="Số lượng"):
    return {"expressionType": "SIMPLE", "column": {"column_name": column},
            "aggregate": "COUNT", "label": label, "optionName": f"metric_count_{column}"}


def where(subject, operator, comparator):
    return {"expressionType": "SIMPLE", "subject": subject, "operator": operator,
            "comparator": comparator, "clause": "WHERE"}


SHIPPED = where("status", "==", "SHIPPED")

#: Mỗi biểu đồ: name, dataset, viz_type, params (không gồm datasource/viz_type),
#: width (trên lưới 12 cột) và height (đơn vị 8px).
CHARTS = [
    # ---------------------------------------------------------------- KPI hàng 1
    {
        "name": "Doanh thu (toàn kỳ)",
        "dataset": "mart_daily_sales",
        "viz_type": "big_number_total",
        "width": 3, "height": 50,
        "params": {
            "metric": billions("revenue", "Doanh thu (tỷ đồng)"),
            "subheader": "Tỷ đồng · tổng doanh thu đơn đã giao",
            "y_axis_format": ",.2f",
            "time_range": "No filter",
        },
    },
    {
        "name": "Lợi nhuận gộp",
        "dataset": "mart_daily_sales",
        "viz_type": "big_number_total",
        "width": 3, "height": 50,
        "params": {
            "metric": billions("gross_profit", "Lợi nhuận gộp (tỷ đồng)"),
            "subheader": "Tỷ đồng · doanh thu trừ giá vốn theo sổ kho",
            "y_axis_format": ",.2f",
            "time_range": "No filter",
        },
    },
    {
        "name": "Số đơn đã giao",
        "dataset": "mart_daily_sales",
        "viz_type": "big_number_total",
        "width": 3, "height": 50,
        "params": {
            "metric": metric("orders", label="Đơn đã giao"),
            "subheader": "Đơn bán ở trạng thái Đã giao",
            "y_axis_format": ",.0f",
            "time_range": "No filter",
        },
    },
    {
        "name": "Giá trị tồn kho",
        "dataset": "mart_inventory_status",
        "viz_type": "big_number_total",
        "width": 3, "height": 50,
        "params": {
            "metric": billions("stock_value", "Giá trị tồn (tỷ đồng)"),
            "subheader": "Tỷ đồng · tồn thực tế × giá vốn bình quân",
            "y_axis_format": ",.2f",
            "time_range": "No filter",
        },
    },

    # ------------------------------------------------------------ xu hướng doanh thu
    {
        "name": "Doanh thu theo tháng",
        "dataset": "mart_daily_sales",
        "viz_type": "echarts_timeseries_bar",
        "width": 8, "height": 60,
        "params": {
            "x_axis": "sales_date",
            "time_grain_sqla": "P1M",
            "metrics": [billions("revenue", "Doanh thu (tỷ đồng)"),
                        billions("gross_profit", "Lợi nhuận gộp (tỷ đồng)")],
            "groupby": [],
            "row_limit": 10000,
            "y_axis_format": ",.2f",
            "x_axis_sort_asc": True,
            "show_legend": True,
            "time_range": "No filter",
        },
    },
    {
        "name": "Doanh thu theo kho",
        "dataset": "mart_daily_sales",
        "viz_type": "pie",
        "width": 4, "height": 60,
        "params": {
            "groupby": ["warehouse_code"],
            "metric": metric("revenue", label="Doanh thu"),
            "row_limit": 20,
            "show_labels": True,
            "label_type": "key_percent",
            "number_format": ",.0f",
            "donut": True,
            "innerRadius": 40,
            "time_range": "No filter",
        },
    },

    # ------------------------------------------------------------------- CRM / RFM
    {
        "name": "Phân khúc khách hàng (RFM)",
        "dataset": "mart_customer_rfm",
        "viz_type": "pie",
        "width": 4, "height": 60,
        "params": {
            "groupby": ["segment"],
            "metric": count_of("customer_id", "Số khách hàng"),
            "row_limit": 20,
            "show_labels": True,
            "label_type": "key_percent",
            "number_format": ",.0f",
            "time_range": "No filter",
        },
    },
    {
        "name": "Giá trị theo phân khúc khách hàng",
        "dataset": "mart_customer_rfm",
        "viz_type": "echarts_timeseries_bar",
        "width": 4, "height": 60,
        "params": {
            "x_axis": "segment",
            "metrics": [billions("monetary", "Tổng doanh thu (tỷ đồng)")],
            "groupby": [],
            "row_limit": 20,
            "y_axis_format": ",.2f",
            "orientation": "horizontal",
            "time_range": "No filter",
        },
    },
    {
        "name": "Top 10 khách hàng",
        "dataset": "mart_customer_rfm",
        "viz_type": "table",
        "width": 4, "height": 60,
        "params": {
            "query_mode": "aggregate",
            "groupby": ["customer_code", "customer_name", "segment"],
            "metrics": [metric("monetary", label="Doanh thu"),
                        metric("frequency", label="Số đơn"),
                        metric("recency_days", "MIN", label="Ngày từ lần mua cuối")],
            "row_limit": 10,
            "order_desc": True,
            "timeseries_limit_metric": metric("monetary", label="Doanh thu"),
            "time_range": "No filter",
        },
    },

    # ------------------------------------------------------------- sản phẩm & kho
    {
        "name": "Doanh thu theo danh mục",
        "dataset": "fct_sales_line",
        "viz_type": "echarts_timeseries_bar",
        "width": 6, "height": 55,
        "params": {
            "x_axis": "category",
            "metrics": [billions("revenue", "Doanh thu (tỷ đồng)")],
            "groupby": [],
            "adhoc_filters": [SHIPPED],
            "row_limit": 50,
            "y_axis_format": ",.2f",
            "orientation": "horizontal",
            "time_range": "No filter",
        },
    },
    {
        "name": "Tồn kho cần chú ý",
        "dataset": "mart_inventory_status",
        "viz_type": "table",
        "width": 6, "height": 55,
        "params": {
            "query_mode": "raw",
            "all_columns": ["sku", "product_name", "warehouse_code", "available",
                            "reorder_point", "stock_status"],
            "adhoc_filters": [where("stock_status", "IN", ["Hết hàng", "Sắp hết"])],
            "order_by_cols": ['["available", true]'],
            "row_limit": 50,
            "time_range": "No filter",
        },
    },

    # --------------------------------------------------------- nhà cung cấp & AI
    {
        "name": "Tỷ lệ giao trễ theo nhà cung cấp",
        "dataset": "mart_supplier_performance",
        "viz_type": "echarts_timeseries_bar",
        "width": 6, "height": 55,
        "params": {
            "x_axis": "supplier_code",
            "metrics": [metric("late_rate_pct", "MAX", label="Tỷ lệ giao trễ (%)")],
            "groupby": [],
            "row_limit": 50,
            "y_axis_format": ",.1f",
            "time_range": "No filter",
        },
    },
    {
        "name": "Đề xuất nhập hàng: hệ thống và AI",
        "dataset": "mart_reorder_suggestion",
        "viz_type": "table",
        "width": 6, "height": 55,
        "params": {
            "query_mode": "aggregate",
            "groupby": ["ai_action", "status"],
            "metrics": [count_of("suggestion_id", "Số đề xuất"),
                        metric("suggested_qty", label="SL hệ thống"),
                        metric("ai_adjusted_qty", label="SL AI đề xuất")],
            "row_limit": 50,
            "order_desc": True,
            "timeseries_limit_metric": count_of("suggestion_id", "Số đề xuất"),
            "time_range": "No filter",
        },
    },
]

#: Chia biểu đồ thành các hàng trên dashboard (theo thứ tự trong CHARTS).
ROWS = [
    ["Doanh thu (toàn kỳ)", "Lợi nhuận gộp", "Số đơn đã giao", "Giá trị tồn kho"],
    ["Doanh thu theo tháng", "Doanh thu theo kho"],
    ["Phân khúc khách hàng (RFM)", "Giá trị theo phân khúc khách hàng", "Top 10 khách hàng"],
    ["Doanh thu theo danh mục", "Tồn kho cần chú ý"],
    ["Tỷ lệ giao trễ theo nhà cung cấp", "Đề xuất nhập hàng: hệ thống và AI"],
]
