#!/usr/bin/env bash
# Khởi tạo Superset rồi chạy web server.
# Chạy lại được nhiều lần: các bước đã làm sẽ tự bỏ qua.
set -euo pipefail

echo "==> Nâng cấp schema metadata của Superset"
superset db upgrade

echo "==> Tạo tài khoản quản trị (bỏ qua nếu đã có)"
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER:-admin}" \
    --firstname Admin --lastname ERP \
    --email "admin@example.com" \
    --password "${SUPERSET_ADMIN_PASSWORD}" || true

echo "==> Khởi tạo vai trò và quyền mặc định"
superset init

echo "==> Superset sẵn sàng tại http://127.0.0.1:8089"
exec gunicorn \
    --bind 0.0.0.0:8088 \
    --workers 4 \
    --worker-class gthread \
    --threads 12 \
    --timeout 180 \
    --limit-request-line 0 \
    --limit-request-field_size 0 \
    "superset.app:create_app()"
