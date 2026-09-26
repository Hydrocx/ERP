from datetime import date

from django.core.management.base import BaseCommand

from forecast.services import run_forecast


class Command(BaseCommand):
    help = "Tính dự báo nhu cầu, cập nhật ROP theo kho và tạo đề xuất nhập hàng (có AI giải thích)."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="Ngày chạy (YYYY-MM-DD), mặc định hôm nay")
        parser.add_argument("--no-ai", action="store_true", help="Không gọi AI, chỉ dùng công thức")

    def handle(self, *args, **opts):
        result = run_forecast(as_of=opts["date"], use_ai=not opts["no_ai"])
        self.stdout.write(self.style.SUCCESS(
            f"Dự báo ngày {result['run_date']}: {result['forecasts']} dòng, "
            f"{result['suggestions']} đề xuất nhập hàng ({result['expired']} đề xuất cũ hết hiệu lực), "
            f"AI giải thích {result['ai_explained']}."
        ))
        if result["ai_error"]:
            self.stdout.write(self.style.WARNING(f"AI không chạy: {result['ai_error']}"))
