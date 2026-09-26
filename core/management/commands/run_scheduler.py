"""Very small scheduler for the Docker "scheduler" service (no cron/Celery needed).

- every day at FORECAST_HOUR: run_forecast (if not run today)
- every Monday at REPORT_HOUR: generate last week's report (if it does not exist yet)
"""

import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from forecast.models import DemandForecast
from reports.models import AIReportPage
from reports.services import previous_week

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Chạy các tác vụ định kỳ: dự báo hằng ngày, báo cáo tuần."

    def add_arguments(self, parser):
        parser.add_argument("--forecast-hour", type=int, default=1)
        parser.add_argument("--report-hour", type=int, default=6)
        parser.add_argument("--interval", type=int, default=300, help="Số giây giữa các lần kiểm tra")
        parser.add_argument("--once", action="store_true", help="Kiểm tra một lần rồi thoát")

    def handle(self, *args, **opts):
        self.stdout.write("Scheduler started")
        while True:
            close_old_connections()
            try:
                self.tick(opts["forecast_hour"], opts["report_hour"])
            except Exception:  # keep the loop alive; the error is logged
                logger.exception("Scheduler task failed")
            if opts["once"]:
                return
            time.sleep(opts["interval"])

    def tick(self, forecast_hour, report_hour):
        now = timezone.localtime()
        today = now.date()
        if now.hour >= forecast_hour and not DemandForecast.objects.filter(run_date=today).exists():
            self.stdout.write(f"[{now:%Y-%m-%d %H:%M}] run_forecast")
            call_command("run_forecast")
        if today.weekday() == 0 and now.hour >= report_hour:
            start, end = previous_week(today)
            if not AIReportPage.objects.filter(period_start=start, period_end=end).exists():
                self.stdout.write(f"[{now:%Y-%m-%d %H:%M}] generate_weekly_report {start}..{end}")
                call_command("generate_weekly_report", start=start, end=end)
