"""Danh sách dashboard được `provision_superset` tạo trong Superset."""

from analytics import dashboard_spec as business
from analytics import ai_dashboard_spec as ai

DASHBOARDS = [
    {"title": business.DASHBOARD_TITLE, "slug": business.DASHBOARD_SLUG,
     "rows": business.ROWS, "charts": business.CHARTS},
    {"title": ai.AI_DASHBOARD_TITLE, "slug": ai.AI_DASHBOARD_SLUG,
     "rows": ai.AI_ROWS, "charts": ai.AI_CHARTS},
]

ALL_CHARTS = [chart for dashboard in DASHBOARDS for chart in dashboard["charts"]]
