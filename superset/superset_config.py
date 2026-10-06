"""Cấu hình Apache Superset cho Mini ERP.

Được mount vào container tại /app/pythonpath/superset_config.py
(xem docker-compose.analytics.yml).

Mọi bí mật đều lấy từ biến môi trường; file này không chứa mật khẩu.
"""

import os

# -----------------------------------------------------------------------------
# Bí mật & metadata
# -----------------------------------------------------------------------------
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

_META_PASSWORD = os.environ.get("SUPERSET_META_PASSWORD", "superset")
SQLALCHEMY_DATABASE_URI = f"postgresql+psycopg2://superset:{_META_PASSWORD}@superset_meta:5432/superset"
SQLALCHEMY_TRACK_MODIFICATIONS = False

# -----------------------------------------------------------------------------
# Cache & hàng đợi (Redis)
# -----------------------------------------------------------------------------
_REDIS = {"host": "superset_cache", "port": 6379}

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": _REDIS["host"],
    "CACHE_REDIS_PORT": _REDIS["port"],
    "CACHE_REDIS_DB": 1,
}
DATA_CACHE_CONFIG = {**CACHE_CONFIG, "CACHE_KEY_PREFIX": "superset_data_", "CACHE_REDIS_DB": 2,
                     "CACHE_DEFAULT_TIMEOUT": 600}
FILTER_STATE_CACHE_CONFIG = {**CACHE_CONFIG, "CACHE_KEY_PREFIX": "superset_filter_", "CACHE_REDIS_DB": 3}
EXPLORE_FORM_DATA_CACHE_CONFIG = {**CACHE_CONFIG, "CACHE_KEY_PREFIX": "superset_form_", "CACHE_REDIS_DB": 4}

# -----------------------------------------------------------------------------
# Bảo mật
# -----------------------------------------------------------------------------
# Chặn các URI kết nối nguy hiểm (sqlite trên máy chủ Superset, v.v.)
PREVENT_UNSAFE_DB_CONNECTIONS = True

# SQL Lab chỉ được chạy truy vấn đọc. Role Postgres cũng đã READ ONLY,
# đây là lớp chặn thứ hai ngay trong ứng dụng.
SQLLAB_CTAS_NO_LIMIT = False
SQL_MAX_ROW = 100_000
DEFAULT_SQLLAB_LIMIT = 1_000
SQLLAB_TIMEOUT = 120

# Cookie phiên làm việc
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.environ.get("SUPERSET_HTTPS", "").lower() == "true"
WTF_CSRF_ENABLED = True
# Cho phép health check và API import/export chạy từ script bootstrap
WTF_CSRF_EXEMPT_LIST = ["superset.views.core.log"]

TALISMAN_ENABLED = False  # chỉ chạy local qua http; bật khi deploy sau TLS

# Vai trò mặc định khi đăng nhập: chỉ xem dashboard, không sửa được
AUTH_USER_REGISTRATION = False
PUBLIC_ROLE_LIKE = None  # không cho khách vãng lai xem

FEATURE_FLAGS = {
    "DASHBOARD_RBAC": True,          # phân quyền theo từng dashboard
    "EMBEDDED_SUPERSET": False,
    "ALERT_REPORTS": False,
    "DRILL_TO_DETAIL": True,
    "DRILL_BY": True,
}

# -----------------------------------------------------------------------------
# Hiển thị
# -----------------------------------------------------------------------------
SUPERSET_WEBSERVER_TIMEOUT = 180
BABEL_DEFAULT_LOCALE = "en"
LANGUAGES = {"en": {"flag": "us", "name": "English"}, "vi": {"flag": "vn", "name": "Vietnamese"}}
DEFAULT_FEATURE_FLAGS = {}

# Định dạng số mặc định hợp với VNĐ (không có phần thập phân)
D3_FORMAT = ",.0f"

ROW_LIMIT = 50_000
VIZ_ROW_LIMIT = 10_000

# Múi giờ nghiệp vụ
DEFAULT_TIME_FILTER = "No filter"

LOG_LEVEL = os.environ.get("SUPERSET_LOG_LEVEL", "INFO")
