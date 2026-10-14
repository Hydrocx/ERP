"""Test pipeline AI/ML trên CSDL: lưu kết quả, tính nhất quán số liệu, cách ly lỗi, view cho Superset."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncDate

from ai.models import AICallLog
from insights import data, narrative
from insights.models import (
    AIInsight, AnomalyEvent, BasketRule, CustomerScore, ModelMetric, ModelRun, SalesForecast,
)
from insights.pipeline import TASKS, Pipeline
from sales.models import SalesOrder, SalesOrderLine

from .history import make_history

pytestmark = pytest.mark.django_db


def test_default_as_of_is_a_sunday_in_the_past():
    from datetime import date
    for today in (date(2026, 10, 14), date(2026, 10, 11), date(2026, 10, 12), date(2026, 10, 18)):
        as_of = data.default_as_of(today)
        assert as_of.weekday() == 6 and 2 <= (today - as_of).days <= 8


def test_dependencies_are_added_and_ordered():
    assert Pipeline._expand(["churn"]) == ["segmentation", "churn"]
    assert Pipeline._expand(["insight", "forecast"]) == ["forecast", "insight"]
    assert Pipeline._expand(TASKS) == TASKS


def test_full_pipeline_stores_consistent_results(settings):
    settings.AI_API_KEY = ""                                        # không gọi LLM
    history = make_history()
    pipeline = Pipeline(use_ai=False)
    runs = pipeline.run()

    assert {t: r.status for t, r in runs.items()} == {t: "ok" for t in TASKS}, \
        {t: r.error for t, r in runs.items() if r.error}
    assert ModelRun.objects.filter(is_current=True).count() == len(TASKS)

    # --- dự báo: 5 phạm vi (TOTAL, 2 kho, 2 danh mục) x 3 thuật toán x (84 backtest + 28 tương lai) ngày
    assert SalesForecast.objects.count() == 5 * 3 * (84 + 28)
    for scope in ("TOTAL", "HN", "HCM", "Đồ uống", "Bánh kẹo"):
        champs = set(SalesForecast.objects.filter(scope=scope, is_champion=True).values_list("algorithm", flat=True))
        assert len(champs) == 1
    future = SalesForecast.objects.filter(kind="future", scope="TOTAL", is_champion=True)
    assert future.count() == 28 and future.order_by("date").first().date == pipeline.as_of + timedelta(days=1)
    assert all(f.lower <= f.yhat <= f.upper for f in future)

    # số thực tế lưu trong bảng dự báo phải đúng bằng doanh thu tính trực tiếp từ dòng đơn hàng
    revenue = {r["d"]: r["rev"] for r in (
        SalesOrderLine.objects.filter(order__status="SHIPPED").annotate(d=TruncDate("order__shipped_at"))
        .values("d").annotate(rev=Sum(ExpressionWrapper(F("quantity") * F("unit_price"),
                                                        output_field=DecimalField(max_digits=20, decimal_places=2)))))}
    for row in SalesForecast.objects.filter(scope="TOTAL", kind="backtest", is_champion=True):
        assert row.actual == revenue.get(row.date, 0)

    # --- chỉ số: mỗi (bài toán, phạm vi) có đúng một thuật toán được đánh dấu champion ở bảng dự báo
    wape = ModelMetric.objects.filter(metric="wape_weekly", scope="TOTAL")
    assert wape.count() == 3 and wape.filter(is_champion=True).count() == 1
    skill = ModelMetric.objects.get(metric="skill_vs_naive_pct", scope="TOTAL", algorithm="seasonal_naive")
    assert skill.value == pytest.approx(0.0)                        # mốc so sánh không hơn chính nó

    # --- khách hàng: mỗi khách có đơn đều có điểm; xác suất rỗng chỉ ở nhóm đã rời bỏ
    buyers = SalesOrder.objects.values("customer").distinct().count()
    assert CustomerScore.objects.count() == buyers
    for score in CustomerScore.objects.all():
        assert (score.churn_prob is None) == score.risk_band.startswith("Đã rời bỏ")
        assert score.risk_band in {"Cao", "Trung bình", "Thấp"} or score.churn_prob is None
        assert score.segment and score.rule_segment
    assert CustomerScore.objects.filter(risk_band="Cao").exists()
    churn = ModelRun.objects.get(task="churn", is_current=True).summary
    assert 0.5 < churn["auc"] <= 1 and churn["high_risk"] == CustomerScore.objects.filter(risk_band="Cao").count()

    # --- giỏ hàng: cặp P0 -> P1 được cài sẵn phải nằm đầu bảng luật
    top = BasketRule.objects.order_by("-lift").first()
    assert {top.antecedent_sku, top.consequent_sku} == {"P0", "P1"} and top.lift > 1.2
    assert ModelMetric.objects.filter(algorithm="pair_lift", metric="orders").exists()

    # --- bất thường, nhận xét
    assert AnomalyEvent.objects.count() >= 0
    assert ModelMetric.objects.filter(split="injection_eval", metric="f1").count() == 2
    insights = AIInsight.objects.all()
    assert insights.count() >= 3 and not any(i.generated_by_ai for i in insights)

    # --- chạy lại: bản ghi mới thành hiện hành, bản cũ giữ lịch sử
    first_forecast = runs["forecast"]
    Pipeline(use_ai=False).run(["forecast"])
    first_forecast.refresh_from_db()
    assert not first_forecast.is_current
    assert ModelRun.objects.filter(task="forecast").count() == 2
    assert ModelRun.objects.filter(task="forecast", is_current=True).count() == 1
    assert SalesForecast.objects.filter(run=first_forecast).exists()          # lịch sử còn nguyên
    assert history["orders"] > 500

    # --- chạy riêng nhận xét: dùng kết quả hiện hành trong CSDL, không viết trên dữ liệu rỗng
    only = Pipeline(use_ai=False).run(["insight"])["insight"]
    assert only.status == "ok"
    bodies = " ".join(AIInsight.objects.filter(run=only).values_list("body", flat=True))
    forecast_now = ModelRun.objects.get(task="forecast", is_current=True).summary["total"]
    assert narrative.vnd_text(forecast_now["forecast_next_28d_revenue"]) in bodies

    if connection.vendor == "postgresql":
        check_analytics_views()


def check_analytics_views():
    """Các view mart_ai_* chỉ lộ lần chạy hiện hành và khớp số liệu nghiệp vụ."""
    call_command("create_analytics_views", no_grant=True, verbosity=0)

    def one(sql):
        with connection.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()[0]

    assert one("SELECT count(*) FROM analytics.mart_ai_forecast") == \
        SalesForecast.objects.filter(run__is_current=True).count()
    assert one("SELECT count(*) FROM analytics.mart_ai_customer_score") == CustomerScore.objects.count()
    assert one("SELECT count(*) FROM analytics.mart_ai_model_runs") == ModelRun.objects.count()
    assert one("SELECT count(*) FROM analytics.mart_ai_model_runs WHERE is_current") == len(TASKS)
    assert one("SELECT count(*) FROM analytics.mart_ai_customer_score WHERE risk_band = 'Cao'") > 0
    assert one("SELECT count(*) FROM analytics.mart_ai_basket_rules") == BasketRule.objects.count()
    # số thực tế trong dự báo == doanh thu theo ngày của mart_daily_sales
    mismatch = one("""
        SELECT count(*) FROM analytics.mart_ai_forecast f
        LEFT JOIN (SELECT sales_date, sum(revenue) AS revenue FROM analytics.mart_daily_sales GROUP BY 1) d
               ON d.sales_date = f.forecast_date
        WHERE f.scope = 'TOTAL' AND f.is_champion AND f.kind = 'backtest'
          AND abs(f.actual - COALESCE(d.revenue, 0)) > 0.01""")
    assert mismatch == 0
    # khách hàng đã che thông tin liên lạc
    assert one("SELECT count(*) FROM analytics.mart_ai_customer_score WHERE email_masked NOT LIKE '%***%'") == 0
    # lệnh đối chiếu tổng thể (gồm nhóm AI) phải qua
    call_command("verify_analytics", verbosity=0)


def test_a_failing_task_is_recorded_and_does_not_stop_the_others(monkeypatch, settings):
    settings.AI_API_KEY = ""
    make_history(days=60, customers=15)
    monkeypatch.setattr("insights.pipeline.data.revenue_series",
                        lambda end: (_ for _ in ()).throw(RuntimeError("boom")))
    runs = Pipeline(use_ai=False).run(["forecast", "basket"])
    assert runs["forecast"].status == "failed" and "boom" in runs["forecast"].error
    assert not runs["forecast"].is_current and runs["basket"].status == "ok"
    assert SalesForecast.objects.count() == 0                       # transaction của bài toán lỗi đã rollback


def test_forecast_needs_enough_history(settings):
    settings.AI_API_KEY = ""
    make_history(days=100, customers=15)
    runs = Pipeline(use_ai=False).run(["forecast"])
    assert runs["forecast"].status == "failed" and "quá ngắn" in runs["forecast"].error


# ------------------------------------------------------------------ nhận xét AI

FACTS = {
    "period": {"start": "2026-09-14", "end": "2026-10-11"},
    "previous_period": {"start": "2026-08-17", "end": "2026-09-13"},
    "forecast": {"total": {
        "champion": "gbm", "wape_weekly_pct": 12.3, "baseline_wape_weekly_pct": 15.0, "skill_vs_naive_pct": 18.0,
        "forecast_next_28d_revenue": 3_250_000_000, "actual_last_28d_revenue": 3_000_000_000, "change_pct": 8.3}},
    "churn": {"champion": "gbm", "auc": 0.82, "high_risk": 40, "revenue_at_risk_365": 900_000_000},
}


def test_fallback_insights_use_only_given_numbers():
    items = narrative.fallback_insights(FACTS)
    text = " ".join(body for _, _, body in items)
    assert "3,25 tỷ" in text and "12.3%" in text and "40 khách hàng" in text


def test_ai_insights_are_checked_against_the_facts(fake_openai):
    fake_openai.queue_json({"insights": [
        {"kind": "forecast", "title": "Doanh thu tăng", "body": "Dự báo 3,25 tỷ, tăng 8,3% so với 4 tuần trước."},
        {"kind": "model_quality", "title": "Chất lượng", "body": "Mô hình sai số 55,5% khá cao."},   # 55,5% không có trong facts
    ]})
    items, used_ai, label = narrative.generate_insights(FACTS, use_ai=True)
    assert used_ai and label != "template"
    assert items[0]["warnings"] == []
    assert items[1]["warnings"] and "55" in items[1]["warnings"][0]
    assert AICallLog.objects.get().feature == "insight"


def test_ai_failure_falls_back_to_template(fake_openai):
    fake_openai.queue_error(RuntimeError("rate limited"))
    items, used_ai, label = narrative.generate_insights(FACTS, use_ai=True)
    assert not used_ai and label == "template" and len(items) >= 3


# ----------------------------------------------------------------------- lệnh

def test_command_rejects_bad_arguments():
    with pytest.raises(CommandError):
        call_command("run_ml_pipeline", only="khong_co")
    with pytest.raises(CommandError):
        call_command("run_ml_pipeline", horizon=60)
