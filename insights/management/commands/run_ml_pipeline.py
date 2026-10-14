"""Chạy pipeline AI/ML: dự báo doanh thu, phân khúc + rời bỏ khách hàng, bất thường, giỏ hàng, nhận xét.

    python manage.py run_ml_pipeline                    # chạy tất cả
    python manage.py run_ml_pipeline --only forecast,churn
    python manage.py run_ml_pipeline --no-ai            # nhận xét theo mẫu, không gọi LLM

Kết quả ghi vào các bảng insights_* và hiện trên dashboard Superset "Mini ERP · AI Insights".
Trả về mã lỗi khác 0 nếu có bài toán thất bại.
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from insights import forecasting
from insights.models import ModelRun
from insights.pipeline import TASKS, run_pipeline


class Command(BaseCommand):
    help = "Huấn luyện / chấm điểm các mô hình AI-ML và ghi kết quả cho Superset."

    def add_arguments(self, parser):
        parser.add_argument("--only", help=f"Danh sách bài toán, phân cách bằng dấu phẩy: {','.join(TASKS)}")
        parser.add_argument("--as-of", type=date.fromisoformat, help="Dữ liệu đến ngày (YYYY-MM-DD)")
        parser.add_argument("--horizon", type=int, default=forecasting.HORIZON, help="Số ngày dự báo (<= 28)")
        parser.add_argument("--folds", type=int, default=forecasting.FOLDS, help="Số lần backtest")
        parser.add_argument("--no-ai", action="store_true", help="Không gọi LLM để viết nhận xét")

    def handle(self, *args, **opts):
        tasks = None
        if opts["only"]:
            tasks = [t.strip() for t in opts["only"].split(",") if t.strip()]
            unknown = [t for t in tasks if t not in TASKS]
            if unknown:
                raise CommandError(f"Bài toán không hợp lệ: {', '.join(unknown)}")
        if not 1 <= opts["horizon"] <= 28:
            raise CommandError("--horizon phải từ 1 đến 28 (đặc trưng độ trễ của GBM >= 28 ngày)")

        pipeline, runs = run_pipeline(
            tasks, as_of=opts["as_of"], horizon=opts["horizon"], folds=opts["folds"],
            use_ai=not opts["no_ai"], log=self.stdout.write)

        failed = [t for t, r in runs.items() if r.status == ModelRun.Status.FAILED]
        self.stdout.write("")
        for task, run in runs.items():
            if run.status == ModelRun.Status.OK:
                self.stdout.write(self.style.SUCCESS(f"OK    {task:13} {run.algorithm}"))
            else:
                self.stdout.write(self.style.ERROR(f"LỖI   {task:13} {run.error}"))
        if failed:
            raise CommandError(f"{len(failed)} bài toán thất bại: {', '.join(failed)}")
        self.stdout.write(self.style.SUCCESS(f"\nHoàn tất (dữ liệu đến {pipeline.as_of})."))
