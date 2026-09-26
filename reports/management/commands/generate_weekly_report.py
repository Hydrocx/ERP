from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from reports.services import generate_report, previous_week


class Command(BaseCommand):
    help = "Tạo báo cáo (mặc định tuần trước) dưới dạng trang Wagtail nháp và gửi duyệt."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=date.fromisoformat)
        parser.add_argument("--end", type=date.fromisoformat)
        parser.add_argument("--no-ai", action="store_true")
        parser.add_argument("--user", help="Username người tạo (để gửi vào workflow duyệt)")

    def handle(self, *args, **opts):
        start, end = opts["start"], opts["end"]
        if bool(start) != bool(end):
            raise CommandError("Cần cả --start và --end")
        if not start:
            start, end = previous_week()
        user = None
        if opts["user"]:
            user = get_user_model().objects.filter(username=opts["user"]).first()
            if user is None:
                raise CommandError(f"Không có user {opts['user']}")
        page = generate_report(start, end, user=user, use_ai=not opts["no_ai"])
        self.stdout.write(self.style.SUCCESS(
            f"Đã tạo '{page.title}' (id={page.pk}, AI={'có' if page.generated_by_ai else 'không'}). "
            "Trang ở trạng thái nháp, cần duyệt và xuất bản trong Wagtail admin."
        ))
        for w in page.ai_warnings:
            self.stdout.write(self.style.WARNING(f"  - {w}"))
