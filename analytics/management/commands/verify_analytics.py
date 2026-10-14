"""Đối chiếu số liệu của lớp analytics (Superset đọc) với ORM của Django (ERP tính).

    python manage.py verify_analytics
    python manage.py verify_analytics --start 2026-09-01 --end 2026-09-30

Mục đích: chứng minh dashboard trên Superset hiển thị đúng số liệu nghiệp vụ,
chứ không phải một phiên bản gần đúng. Lệnh trả về mã lỗi khác 0 nếu có sai lệch,
nên có thể đưa vào CI.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from django.db.models import Sum

from catalog.models import Customer
from insights.models import BasketRule, CustomerScore, ModelMetric, ModelRun, SalesForecast
from inventory.models import StockLevel
from reports.kpis import inventory_snapshot, sales_summary
from sales.models import SalesOrder

TOLERANCE = Decimal("0.01")  # cho phép lệch 0,01 do làm tròn numeric


class Command(BaseCommand):
    help = "Kiểm tra số liệu view analytics khớp với KPI tính bằng Django ORM."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=date.fromisoformat)
        parser.add_argument("--end", type=date.fromisoformat)

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            raise CommandError("Lệnh này cần PostgreSQL (lớp analytics không chạy trên SQLite).")

        end = opts["end"] or timezone.localdate()
        start = opts["start"] or (end - timedelta(days=89))
        self.stdout.write(f"Đối chiếu kỳ {start} → {end}\n")

        checks = [
            *self._sales_checks(start, end),
            *self._inventory_checks(),
            *self._customer_checks(),
            *self._ai_checks(),
        ]

        width = max(len(name) for name, _, _ in checks)
        failures = 0
        for name, orm_value, sql_value in checks:
            ok = _close(orm_value, sql_value)
            failures += not ok
            mark = self.style.SUCCESS("OK  ") if ok else self.style.ERROR("LỆCH")
            self.stdout.write(
                f"{mark} {name.ljust(width)}  ORM={_fmt(orm_value):>18}  SQL={_fmt(sql_value):>18}"
            )

        self.stdout.write("")
        if failures:
            raise CommandError(f"{failures}/{len(checks)} phép đối chiếu bị lệch.")
        self.stdout.write(self.style.SUCCESS(
            f"Tất cả {len(checks)} phép đối chiếu khớp. Dashboard Superset dùng cùng số liệu với ERP."
        ))

    # ------------------------------------------------------------------ nhóm 1
    def _sales_checks(self, start, end):
        orm = sales_summary(start, end)
        row = _one("""
            SELECT COALESCE(sum(revenue), 0), COALESCE(sum(cogs), 0),
                   COALESCE(sum(gross_profit), 0), COALESCE(sum(orders), 0)
            FROM analytics.mart_daily_sales
            WHERE sales_date BETWEEN %s AND %s
        """, [start, end])
        checks = [
            ("Doanh thu (mart_daily_sales)", orm["revenue"], row[0]),
            ("Giá vốn (mart_daily_sales)", orm["cogs"], row[1]),
            ("Lợi nhuận gộp (mart_daily_sales)", orm["gross_profit"], row[2]),
            ("Số đơn đã giao (mart_daily_sales)", orm["orders_shipped"], row[3]),
        ]
        # Doanh thu tính lại từ fact table phải trùng với mart
        line_revenue = _one("""
            SELECT COALESCE(sum(revenue), 0) FROM analytics.fct_sales_line
            WHERE status = 'SHIPPED' AND shipped_date BETWEEN %s AND %s
        """, [start, end])[0]
        checks.append(("Doanh thu (fct_sales_line)", orm["revenue"], line_revenue))
        return checks

    # ------------------------------------------------------------------ nhóm 2
    def _inventory_checks(self):
        orm = inventory_snapshot()
        row = _one("""
            SELECT COALESCE(sum(stock_value), 0),
                   count(*) FILTER (WHERE stock_status = 'Hết hàng'),
                   count(*) FILTER (WHERE stock_status IN ('Hết hàng', 'Sắp hết'))
            FROM analytics.mart_inventory_status
        """)
        # inventory_snapshot() chỉ đếm sản phẩm đang kinh doanh khi báo hết hàng
        out_of_stock_active = _one("""
            SELECT count(*) FROM analytics.mart_inventory_status m
            JOIN analytics.dim_product p ON p.product_id = m.product_id
            WHERE m.stock_status = 'Hết hàng' AND p.is_active
        """)[0]
        total_on_hand = StockLevel.objects.count()
        sql_rows = _one("SELECT count(*) FROM analytics.mart_inventory_status")[0]
        return [
            ("Giá trị tồn kho", orm["inventory_value"], row[0]),
            ("Số vị trí sản phẩm × kho", total_on_hand, sql_rows),
            ("Số vị trí hết hàng", orm["out_of_stock"], out_of_stock_active),
        ]

    # ------------------------------------------------------------------ nhóm 3
    # ------------------------------------------------------------------ nhóm 4
    def _ai_checks(self):
        """Kết quả mô hình AI (insights_*) phải khớp với view mà Superset đọc và với dữ liệu nghiệp vụ."""
        if not ModelRun.objects.filter(task="forecast", is_current=True).exists():
            self.stdout.write(self.style.WARNING(
                "Chưa có kết quả AI hiện hành, bỏ qua nhóm đối chiếu AI (chạy: python manage.py run_ml_pipeline)\n"))
            return []
        orm_forecast = SalesForecast.objects.filter(run__is_current=True)
        backtest = orm_forecast.filter(scope="TOTAL", is_champion=True, kind="backtest")
        dates = list(backtest.values_list("date", flat=True))
        actual_in_ai_table = backtest.aggregate(s=Sum("actual"))["s"] or 0
        revenue_in_mart = _one("""
            SELECT COALESCE(sum(revenue), 0) FROM analytics.mart_daily_sales WHERE sales_date = ANY(%s)
        """, [dates])[0]
        high = CustomerScore.objects.filter(run__is_current=True, risk_band="Cao")
        return [
            ("AI: dòng dự báo (view Superset)", orm_forecast.count(),
             _one("SELECT count(*) FROM analytics.mart_ai_forecast")[0]),
            ("AI: số thực tế dự báo = doanh thu mart_daily_sales", actual_in_ai_table, revenue_in_mart),
            ("AI: khách hàng được chấm điểm", CustomerScore.objects.filter(run__is_current=True).count(),
             _one("SELECT count(*) FROM analytics.mart_ai_customer_score")[0]),
            ("AI: khách nguy cơ cao", high.count(),
             _one("SELECT count(*) FROM analytics.mart_ai_customer_score WHERE risk_band = 'Cao'")[0]),
            ("AI: doanh số 365 ngày của nhóm nguy cơ cao", high.aggregate(s=Sum("monetary_365"))["s"] or 0,
             _one("SELECT COALESCE(sum(monetary_365), 0) FROM analytics.mart_ai_customer_score "
                  "WHERE risk_band = 'Cao'")[0]),
            ("AI: luật mua kèm", BasketRule.objects.filter(run__is_current=True).count(),
             _one("SELECT count(*) FROM analytics.mart_ai_basket_rules")[0]),
            ("AI: chỉ số mô hình hiện hành", ModelMetric.objects.filter(run__is_current=True).count(),
             _one("SELECT count(*) FROM analytics.mart_ai_model_metrics")[0]),
        ]

    def _customer_checks(self):
        orm_customers = Customer.objects.count()
        sql_customers = _one("SELECT count(*) FROM analytics.dim_customer")[0]

        # Khách có đơn đã giao: ORM vs view RFM
        orm_buyers = (SalesOrder.objects.filter(status=SalesOrder.Status.SHIPPED)
                      .values("customer_id").distinct().count())
        sql_buyers = _one("SELECT count(*) FROM analytics.mart_customer_rfm")[0]

        # Tổng doanh thu trong RFM phải bằng tổng doanh thu toàn bộ đơn đã giao
        orm_lifetime = sales_summary(date(2000, 1, 1), timezone.localdate())["revenue"]
        sql_lifetime = _one("SELECT COALESCE(sum(monetary), 0) FROM analytics.mart_customer_rfm")[0]
        return [
            ("Số khách hàng (dim_customer)", orm_customers, sql_customers),
            ("Khách có đơn đã giao (RFM)", orm_buyers, sql_buyers),
            ("Doanh thu trọn đời (RFM)", orm_lifetime, sql_lifetime),
        ]


def _one(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchone()


def _close(a, b):
    return abs(Decimal(str(a)) - Decimal(str(b))) <= TOLERANCE


def _fmt(value):
    number = Decimal(str(value))
    return f"{number:,.0f}" if number == number.to_integral_value() else f"{number:,.2f}"
