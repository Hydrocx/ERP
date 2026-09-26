"""Call every AI feature once against the real OpenAI API and report the result.

Run after setting OPENAI_API_KEY (and seeding data):  python manage.py ai_selftest
Costs a few thousand tokens. Reorder suggestions are only read, nothing else is changed
except the AI fields of one pending suggestion.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ai import client
from ai.assistant import ask
from ai.models import AICallLog
from ai.services import analyze_product, explain_reorder, write_report
from catalog.models import Product
from forecast.models import ReorderSuggestion
from forecast.services import compute_forecasts
from reports.kpis import compute_kpis
from reports.services import check_numbers, previous_week


class Command(BaseCommand):
    help = "Gọi thử từng tính năng AI với OpenAI thật (cần OPENAI_API_KEY)."

    def handle(self, *args, **opts):
        if not client.is_enabled():
            raise CommandError("Chưa cấu hình OPENAI_API_KEY hoặc AI đang tắt trong Cấu hình AI & dự báo.")
        self.stdout.write(f"Model: {client.get_settings().model_name}")
        results = [
            self.run("F1 đề xuất nhập hàng", self.reorder),
            self.run("Phân tích sản phẩm", self.product),
            self.run("F2 báo cáo", self.report),
            self.run("F3 trợ lý", lambda: ask("Những mặt hàng nào đang hết hàng? Liệt kê tối đa 5.")[0][:400]),
        ]
        logs = AICallLog.objects.filter(created_at__gte=self.started)
        self.stdout.write(f"\nTổng token: vào {sum(l.input_tokens for l in logs)}, ra {sum(l.output_tokens for l in logs)}")
        if not all(results):
            raise CommandError("Có tính năng AI lỗi, xem chi tiết ở trên và trong Nhật ký gọi AI.")
        self.stdout.write(self.style.SUCCESS("Tất cả tính năng AI hoạt động."))

    def run(self, name, func):
        if not hasattr(self, "started"):
            self.started = timezone.now()
        t = time.monotonic()
        try:
            output = func()
        except (client.AIError, client.AIUnavailable, ValueError) as exc:
            self.stdout.write(self.style.ERROR(f"\n[{name}] LỖI: {exc}"))
            return False
        self.stdout.write(self.style.SUCCESS(f"\n[{name}] OK ({time.monotonic() - t:.1f}s)"))
        self.stdout.write(str(output))
        return True

    def reorder(self):
        s = ReorderSuggestion.objects.filter(status=ReorderSuggestion.Status.PENDING).select_related(
            "product__category", "warehouse", "forecast").first()
        if s is None:
            raise ValueError("Không có đề xuất chờ duyệt, hãy chạy run_forecast trước.")
        explain_reorder([s])
        s.refresh_from_db()
        return (f"{s.product.sku}@{s.warehouse.code}: hệ thống {s.suggested_qty} → AI {s.ai_action} "
                f"{s.ai_adjusted_qty} ({s.ai_confidence}, guardrail={s.ai_flagged}): {s.ai_reason}")

    def product(self):
        product = Product.objects.select_related("category").first()
        return analyze_product(product, compute_forecasts(products=[product]))

    def report(self):
        kpis = compute_kpis(*previous_week())
        sections = write_report(kpis)
        texts = [sections["summary"], *sections["highlights"], *sections["risks"], *sections["recommendations"]]
        warnings = check_numbers(texts, kpis)
        return f"{sections['summary']}\nCảnh báo số liệu: {warnings or 'không có'}"
