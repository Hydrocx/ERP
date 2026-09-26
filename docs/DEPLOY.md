# Hướng dẫn triển khai

## 1. Biến môi trường

| Biến | Bắt buộc | Mặc định | Ghi chú |
|---|---|---|---|
| `SECRET_KEY` | production | – | Chuỗi ngẫu nhiên dài (`python -c "import secrets; print(secrets.token_urlsafe(50))"`) |
| `DATABASE_URL` | không | SQLite `db.sqlite3` | Ví dụ `postgres://erp:erp@db:5432/erp` |
| `ALLOWED_HOSTS` | production | – | Danh sách phân cách bằng dấu phẩy |
| `CSRF_TRUSTED_ORIGINS` | production | – | Ví dụ `https://erp.example.com` |
| `WAGTAILADMIN_BASE_URL` | không | `http://localhost:8000` | Dùng cho link trong email thông báo duyệt |
| `OPENAI_API_KEY` | không | rỗng | Không có key → các tính năng AI dùng phương án dự phòng theo công thức/mẫu |
| `OPENAI_MODEL` | không | `gpt-4o-mini` | Có thể đổi trong Wagtail Settings. Kiểm tra model còn được OpenAI hỗ trợ trước khi dùng |
| `HTTPS`, `SECURE_SSL_REDIRECT` | không | `False` | Bật khi chạy sau proxy TLS |
| `GUNICORN_WORKERS` | không | 3 | |
| `LOG_LEVEL` | không | `WARNING` | |
| `MAIL_BACKEND` | không | `console` | `smtp` để gửi email thật (thông báo workflow duyệt báo cáo) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `DEFAULT_FROM_EMAIL` | khi `MAIL_BACKEND=smtp` | | |

## 2. Chạy local (Windows / macOS / Linux)

```bash
python -m venv .venv
.venv\Scripts\activate              # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env              # macOS/Linux: cp .env.example .env

python manage.py migrate
python manage.py seed_data --admin          # ~7 phút, 400 ngày dữ liệu; thử nhanh: --days 30
python manage.py setup_roles --demo-users   # nhóm quyền + user demo
python manage.py run_forecast               # dự báo + đề xuất nhập hàng
python manage.py generate_weekly_report --user admin
python manage.py runserver
```

Sau khi điền `OPENAI_API_KEY`, kiểm tra kết nối và cả 4 tính năng AI bằng `python manage.py ai_selftest`
(tốn vài nghìn token; kết quả cũng được ghi vào *Nhật ký gọi AI*).

- Frontend: <http://localhost:8000/app/> · Quản trị: <http://localhost:8000/admin/>
- Tài khoản dev: `admin/admin123`; `quanly`, `thukho`, `muahang`, `banhang` / `demo12345`. **Không dùng ở production.**

## 3. Docker Compose (PostgreSQL + web + scheduler)

```bash
cp .env.example .env
# sửa .env: SECRET_KEY, OPENAI_API_KEY, (tùy chọn) POSTGRES_PASSWORD, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py setup_roles
# (tùy chọn) dữ liệu mẫu:
docker compose exec web python manage.py seed_data
docker compose exec web python manage.py run_forecast
```

Dịch vụ:

- `db`: PostgreSQL 16, dữ liệu trong volume `pgdata`.
- `web`: chạy `migrate` rồi gunicorn tại cổng 8000; static phục vụ bằng WhiteNoise; media trong volume `media`.
- `scheduler`: `manage.py run_scheduler` — chạy `run_forecast` hằng ngày sau 01:00 và tạo báo cáo tuần trước vào thứ Hai sau 06:00
  (giờ `Asia/Ho_Chi_Minh`). Báo cáo tự động không có người tạo nên ở trạng thái nháp, Quản lý gửi duyệt / xuất bản trong admin.

> Ghi chú: Dockerfile và docker-compose.yml chưa được build thử trên máy phát triển (không cài Docker).
> Cấu hình production (`check --deploy`, `collectstatic` với WhiteNoise) đã được kiểm tra bằng Python.

## 4. Triển khai lên VPS / PaaS

1. Tạo PostgreSQL, đặt `DATABASE_URL`.
2. Đặt `DJANGO_SETTINGS_MODULE=erp.settings.production` và các biến ở mục 1.
3. Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`.
4. Release: `python manage.py migrate --noinput && python manage.py setup_roles`.
5. Web: `gunicorn erp.wsgi:application --bind 0.0.0.0:$PORT`.
6. Tác vụ định kỳ: chạy `python manage.py run_scheduler` như một worker, hoặc dùng cron của nền tảng:
   `0 1 * * * python manage.py run_forecast` và `0 6 * * 1 python manage.py generate_weekly_report`.
7. Đặt TLS ở reverse proxy (Nginx/Caddy/PaaS), bật `HTTPS=True`.

## 5. Kiểm thử

```bash
pytest                 # 93 test, không gọi OpenAI thật (dùng client giả lập)
python manage.py check --deploy --settings=erp.settings.production   # cần SECRET_KEY, ALLOWED_HOSTS
```

## 6. Chi phí & an toàn AI

- Mỗi lần gọi được ghi vào *AI & Dự báo → Nhật ký gọi AI* (token vào/ra, thời gian, lỗi). Hệ thống không tự tính tiền;
  nhân số token với bảng giá hiện hành của model bạn chọn.
- F1 gửi tối đa 15 đề xuất mỗi lần gọi; báo cáo tuần 1 lần gọi; trợ lý tối đa 5 vòng gọi tool mỗi câu hỏi.
- Dữ liệu gửi cho OpenAI: mã/tên sản phẩm, số liệu tổng hợp bán hàng/tồn kho, KPI. Không gửi thông tin liên hệ khách hàng.
- Tắt từng tính năng AI trong *Cài đặt → Cấu hình AI & dự báo*.
