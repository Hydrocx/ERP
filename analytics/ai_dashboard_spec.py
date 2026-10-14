"""Đặc tả dashboard thứ hai "AI Insights": hiển thị kết quả các mô hình AI / ML.

Dữ liệu đến từ các view analytics.mart_ai_* (xem analytics/sql/analytics_views.sql), được ghi bởi
`python manage.py run_ml_pipeline`. Dùng chung các hàm tạo chỉ số / bộ lọc với dashboard_spec.
"""

from analytics.dashboard_spec import billions, count_of, where

AI_DASHBOARD_TITLE = "Mini ERP · AI Insights"
AI_DASHBOARD_SLUG = "mini-erp-ai-insights"


def sql_metric(expression, label):
    """Chỉ số tự viết bằng biểu thức SQL (khi cần nhân/chia hoặc hàm gộp đặc biệt)."""
    return {"expressionType": "SQL", "sqlExpression": expression, "label": label,
            "optionName": "metric_sql_" + "".join(ch if ch.isalnum() else "_" for ch in label)[:40]}


def sql_filter(expression):
    return {"expressionType": "SQL", "sqlExpression": expression, "clause": "WHERE"}


CHAMPION = sql_filter("is_champion")

AI_CHARTS = [
    # ------------------------------------------------------------------ hàng 1: KPI
    {
        "name": "AI · Sai số dự báo doanh thu (WAPE tuần, %)",
        "dataset": "mart_ai_model_metrics", "viz_type": "big_number_total", "width": 3, "height": 50,
        "params": {
            "metric": sql_metric("MIN(value) * 100", "WAPE tuần (%)"),
            "subheader": "Mô hình tốt nhất, toàn công ty · thấp hơn là tốt",
            "y_axis_format": ",.1f", "time_range": "No filter",
            "adhoc_filters": [where("metric", "==", "wape_weekly"), where("scope", "==", "TOTAL"), CHAMPION],
        },
    },
    {
        "name": "AI · AUC dự báo khách rời bỏ",
        "dataset": "mart_ai_model_metrics", "viz_type": "big_number_total", "width": 3, "height": 50,
        "params": {
            "metric": sql_metric("MAX(value)", "AUC"),
            "subheader": "Tập kiểm tra theo thời gian · 0,5 = đoán mò, 1 = hoàn hảo",
            "y_axis_format": ",.3f", "time_range": "No filter",
            "adhoc_filters": [where("task", "==", "churn"), where("metric", "==", "auc"),
                              where("split", "==", "test"), CHAMPION],
        },
    },
    {
        "name": "AI · Khách hàng nguy cơ rời bỏ cao",
        "dataset": "mart_ai_customer_score", "viz_type": "big_number_total", "width": 3, "height": 50,
        "params": {
            "metric": count_of("customer_id", "Khách hàng"),
            "subheader": "20% khách có xác suất không mua trong 60 ngày tới cao nhất",
            "y_axis_format": ",.0f", "time_range": "No filter",
            "adhoc_filters": [where("risk_band", "==", "Cao")],
        },
    },
    {
        "name": "AI · Doanh số 365 ngày đang có nguy cơ (tỷ đồng)",
        "dataset": "mart_ai_customer_score", "viz_type": "big_number_total", "width": 3, "height": 50,
        "params": {
            "metric": billions("monetary_365", "Doanh số có nguy cơ (tỷ đồng)"),
            "subheader": "Tỷ đồng · của nhóm khách nguy cơ cao",
            "y_axis_format": ",.2f", "time_range": "No filter",
            "adhoc_filters": [where("risk_band", "==", "Cao")],
        },
    },

    # --------------------------------------------------------------- hàng 2: dự báo
    {
        "name": "AI · Doanh thu thực tế và dự báo (tuần, tỷ đồng)",
        "dataset": "mart_ai_forecast", "viz_type": "echarts_timeseries_line", "width": 8, "height": 62,
        "params": {
            "x_axis": "forecast_date", "time_grain_sqla": "P1W", "groupby": [],
            "metrics": [sql_metric("SUM(actual) / 1e9", "Thực tế"),
                        sql_metric("SUM(yhat) / 1e9", "Dự báo"),
                        sql_metric("SUM(lower) / 1e9", "Cận dưới (P10)"),
                        sql_metric("SUM(upper) / 1e9", "Cận trên (P90)")],
            "adhoc_filters": [where("scope", "==", "TOTAL"), CHAMPION],
            "row_limit": 10000, "y_axis_format": ",.2f", "show_legend": True, "time_range": "No filter",
        },
    },
    {
        "name": "AI · So sánh sai số các thuật toán dự báo (WAPE tuần, %)",
        "dataset": "mart_ai_model_metrics", "viz_type": "echarts_timeseries_bar", "width": 4, "height": 62,
        "params": {
            "x_axis": "algorithm", "groupby": [],
            "metrics": [sql_metric("AVG(value) * 100", "WAPE tuần (%)")],
            "adhoc_filters": [where("metric", "==", "wape_weekly"), where("scope", "==", "TOTAL")],
            "row_limit": 20, "y_axis_format": ",.1f", "time_range": "No filter",
        },
    },

    # ------------------------------------------------------------- hàng 3: rời bỏ
    {
        "name": "AI · Mức rủi ro rời bỏ của khách hàng",
        "dataset": "mart_ai_customer_score", "viz_type": "pie", "width": 4, "height": 58,
        "params": {
            "groupby": ["risk_band"], "metric": count_of("customer_id", "Khách hàng"), "row_limit": 20,
            "show_labels": True, "label_type": "key_percent", "number_format": ",.0f", "donut": True,
            "innerRadius": 40, "time_range": "No filter",
        },
    },
    {
        "name": "AI · Khách hàng nguy cơ cao cần chăm sóc",
        "dataset": "mart_ai_customer_score", "viz_type": "table", "width": 5, "height": 58,
        "params": {
            "query_mode": "raw",
            "all_columns": ["customer_code", "customer_name", "segment", "churn_prob", "recency_days",
                            "monetary_365", "driver"],
            "adhoc_filters": [where("risk_band", "==", "Cao")],
            "order_by_cols": ['["monetary_365", false]'], "row_limit": 15, "time_range": "No filter",
            "column_config": {"churn_prob": {"d3NumberFormat": ".0%"},
                              "monetary_365": {"d3NumberFormat": ",.0f"}},
        },
    },
    {
        "name": "AI · AUC các mô hình dự báo rời bỏ",
        "dataset": "mart_ai_model_metrics", "viz_type": "echarts_timeseries_bar", "width": 3, "height": 58,
        "params": {
            "x_axis": "algorithm", "groupby": [],
            "metrics": [sql_metric("MAX(value)", "AUC")],
            "adhoc_filters": [where("task", "==", "churn"), where("metric", "==", "auc"),
                              where("split", "==", "test")],
            "row_limit": 20, "y_axis_format": ",.3f", "time_range": "No filter",
        },
    },

    # ----------------------------------------------------------- hàng 4: phân khúc
    {
        "name": "AI · Phân khúc khách hàng (K-Means)",
        "dataset": "mart_ai_customer_score", "viz_type": "pie", "width": 4, "height": 58,
        "params": {
            "groupby": ["segment"], "metric": count_of("customer_id", "Khách hàng"), "row_limit": 20,
            "show_labels": True, "label_type": "key_percent", "number_format": ",.0f", "time_range": "No filter",
        },
    },
    {
        "name": "AI · Đặc điểm từng phân khúc",
        "dataset": "mart_ai_customer_score", "viz_type": "table", "width": 8, "height": 58,
        "params": {
            "query_mode": "aggregate", "groupby": ["segment"],
            "metrics": [count_of("customer_id", "Số khách"),
                        sql_metric("AVG(recency_days)", "Ngày từ lần mua cuối (TB)"),
                        sql_metric("AVG(frequency_365)", "Số đơn 365 ngày (TB)"),
                        sql_metric("AVG(monetary_365) / 1e6", "Doanh số 365 ngày (triệu, TB)"),
                        sql_metric("AVG(churn_prob)", "Xác suất rời bỏ (TB)")],
            "row_limit": 20, "order_desc": True,
            "timeseries_limit_metric": sql_metric("AVG(monetary_365) / 1e6", "Doanh số 365 ngày (triệu, TB)"),
            "time_range": "No filter",
            "column_config": {"Xác suất rời bỏ (TB)": {"d3NumberFormat": ".0%"}},
        },
    },

    # --------------------------------------------------------- hàng 5: bất thường
    {
        "name": "AI · Ngày doanh thu bất thường",
        "dataset": "mart_ai_anomaly", "viz_type": "table", "width": 7, "height": 58,
        "params": {
            "query_mode": "raw",
            "all_columns": ["anomaly_date", "warehouse_code", "algorithm", "actual", "expected",
                            "deviation_pct", "severity"],
            "order_by_cols": ['["anomaly_date", false]'], "row_limit": 25, "time_range": "No filter",
            "column_config": {"actual": {"d3NumberFormat": ",.0f"}, "expected": {"d3NumberFormat": ",.0f"}},
        },
    },
    {
        "name": "AI · Chất lượng phát hiện bất thường (F1 khi chèn bất thường giả)",
        "dataset": "mart_ai_model_metrics", "viz_type": "echarts_timeseries_bar", "width": 5, "height": 58,
        "params": {
            "x_axis": "algorithm", "groupby": ["metric"],
            "metrics": [sql_metric("MAX(value)", "Giá trị")],
            "adhoc_filters": [where("task", "==", "anomaly"), where("split", "==", "injection_eval")],
            "row_limit": 50, "y_axis_format": ",.2f", "show_legend": True, "time_range": "No filter",
        },
    },

    # ----------------------------------------------------- hàng 6: giỏ hàng + nhận xét
    {
        "name": "AI · Cặp sản phẩm hay mua kèm (lift cao nhất)",
        "dataset": "mart_ai_basket_rules", "viz_type": "table", "width": 6, "height": 62,
        "params": {
            "query_mode": "raw",
            "all_columns": ["rule_label", "pair_orders", "confidence", "lift"],
            "order_by_cols": ['["lift", false]'], "row_limit": 12, "time_range": "No filter",
            "column_config": {"confidence": {"d3NumberFormat": ".0%"}, "lift": {"d3NumberFormat": ".2f"}},
        },
    },
    {
        "name": "AI · Nhận xét do AI viết từ kết quả mô hình",
        "dataset": "mart_ai_insight", "viz_type": "table", "width": 6, "height": 62,
        "params": {
            "query_mode": "raw", "all_columns": ["kind", "title", "body", "generated_by_ai", "warning_count"],
            "order_by_cols": ['["insight_id", true]'], "row_limit": 10, "time_range": "No filter",
        },
    },

    # --------------------------------------------------------------- hàng 7: lịch sử
    {
        "name": "AI · Lịch sử các lần chạy mô hình",
        "dataset": "mart_ai_model_runs", "viz_type": "table", "width": 12, "height": 46,
        "params": {
            "query_mode": "raw",
            "all_columns": ["run_id", "task", "algorithm", "status", "as_of", "started_at", "duration_s",
                            "n_rows", "is_current"],
            "order_by_cols": ['["run_id", false]'], "row_limit": 20, "time_range": "No filter",
        },
    },
]

AI_ROWS = [
    ["AI · Sai số dự báo doanh thu (WAPE tuần, %)", "AI · AUC dự báo khách rời bỏ",
     "AI · Khách hàng nguy cơ rời bỏ cao", "AI · Doanh số 365 ngày đang có nguy cơ (tỷ đồng)"],
    ["AI · Doanh thu thực tế và dự báo (tuần, tỷ đồng)",
     "AI · So sánh sai số các thuật toán dự báo (WAPE tuần, %)"],
    ["AI · Mức rủi ro rời bỏ của khách hàng", "AI · Khách hàng nguy cơ cao cần chăm sóc",
     "AI · AUC các mô hình dự báo rời bỏ"],
    ["AI · Phân khúc khách hàng (K-Means)", "AI · Đặc điểm từng phân khúc"],
    ["AI · Ngày doanh thu bất thường", "AI · Chất lượng phát hiện bất thường (F1 khi chèn bất thường giả)"],
    ["AI · Cặp sản phẩm hay mua kèm (lift cao nhất)", "AI · Nhận xét do AI viết từ kết quả mô hình"],
    ["AI · Lịch sử các lần chạy mô hình"],
]
