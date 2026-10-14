"""Bảng lưu kết quả của các mô hình AI/ML.

Mỗi lần chạy pipeline tạo một ModelRun (có phiên bản, tham số, chỉ số đánh giá).
Superset đọc các bảng này qua view analytics.mart_ai_* (xem analytics/sql/analytics_views.sql),
chỉ lấy lần chạy đang `is_current` nên dashboard luôn hiển thị kết quả mới nhất, còn lịch sử
vẫn được giữ để so sánh chất lượng mô hình theo thời gian.
"""

from django.db import models

from catalog.models import Customer


class ModelRun(models.Model):
    class Task(models.TextChoices):
        FORECAST = "forecast", "Dự báo doanh thu"
        SEGMENTATION = "segmentation", "Phân khúc khách hàng"
        CHURN = "churn", "Dự báo rời bỏ"
        ANOMALY = "anomaly", "Phát hiện bất thường"
        BASKET = "basket", "Phân tích giỏ hàng"
        INSIGHT = "insight", "Nhận xét do AI viết"

    class Status(models.TextChoices):
        OK = "ok", "Thành công"
        FAILED = "failed", "Lỗi"

    wagtail_reference_index_ignore = True

    task = models.CharField("Bài toán", max_length=20, choices=Task.choices, db_index=True)
    algorithm = models.CharField("Thuật toán (champion)", max_length=60, blank=True)
    status = models.CharField("Trạng thái", max_length=10, choices=Status.choices, default=Status.OK)
    as_of = models.DateField("Dữ liệu đến ngày")
    started_at = models.DateTimeField("Bắt đầu", auto_now_add=True)
    duration_s = models.FloatField("Thời gian chạy (giây)", default=0)
    params = models.JSONField("Tham số", default=dict, blank=True)
    summary = models.JSONField("Tóm tắt kết quả", default=dict, blank=True)
    n_rows = models.PositiveIntegerField("Số dòng kết quả", default=0)
    error = models.TextField("Lỗi", blank=True)
    is_current = models.BooleanField("Là kết quả hiện hành", default=False, db_index=True)

    class Meta:
        verbose_name = "Lần chạy mô hình"
        verbose_name_plural = "Các lần chạy mô hình"
        ordering = ["-id"]

    def __str__(self):
        return f"#{self.pk} {self.get_task_display()} ({self.algorithm or '-'}) {self.started_at:%d/%m/%Y %H:%M}"


class ModelMetric(models.Model):
    """Một chỉ số đánh giá của một thuật toán (một dòng một số, để Superset vẽ trực tiếp)."""

    wagtail_reference_index_ignore = True

    run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="metrics")
    algorithm = models.CharField("Thuật toán", max_length=60)
    scope = models.CharField("Phạm vi", max_length=80, default="ALL")
    split = models.CharField("Tập đánh giá", max_length=20, default="test")
    metric = models.CharField("Chỉ số", max_length=60)
    value = models.FloatField("Giá trị")
    is_champion = models.BooleanField("Của thuật toán được chọn", default=False)

    class Meta:
        verbose_name = "Chỉ số mô hình"
        verbose_name_plural = "Chỉ số mô hình"
        indexes = [models.Index(fields=["run", "metric"])]

    def __str__(self):
        return f"{self.algorithm}/{self.scope}: {self.metric}={self.value:.4g}"


class SalesForecast(models.Model):
    class Kind(models.TextChoices):
        BACKTEST = "backtest", "Backtest (có số thực tế)"
        FUTURE = "future", "Dự báo tương lai"

    wagtail_reference_index_ignore = True

    run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="forecasts")
    scope_type = models.CharField(max_length=12)      # TOTAL / WAREHOUSE / CATEGORY
    scope = models.CharField(max_length=80)           # TOTAL, HN, "Đồ uống"...
    algorithm = models.CharField(max_length=60)
    is_champion = models.BooleanField(default=False)
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    fold = models.PositiveSmallIntegerField(null=True, blank=True)
    actual = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    yhat = models.DecimalField(max_digits=18, decimal_places=2)
    lower = models.DecimalField(max_digits=18, decimal_places=2)
    upper = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        verbose_name = "Dự báo doanh thu"
        verbose_name_plural = "Dự báo doanh thu"
        indexes = [models.Index(fields=["run", "scope", "kind"])]


class CustomerScore(models.Model):
    """Kết quả phân khúc (K-Means) và dự báo rời bỏ cho từng khách hàng."""

    wagtail_reference_index_ignore = True

    run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="customer_scores",
                            help_text="Lần chạy dự báo rời bỏ")
    segment_run = models.ForeignKey(ModelRun, on_delete=models.SET_NULL, null=True, related_name="+",
                                    help_text="Lần chạy phân khúc")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="ai_scores")
    as_of = models.DateField()
    recency_days = models.PositiveIntegerField()
    frequency_365 = models.PositiveIntegerField()
    monetary_365 = models.DecimalField(max_digits=18, decimal_places=2)
    cluster = models.PositiveSmallIntegerField()
    segment = models.CharField("Phân khúc (K-Means)", max_length=60)
    rule_segment = models.CharField("Phân khúc (luật RFM)", max_length=60)
    churn_prob = models.FloatField("Xác suất rời bỏ 60 ngày tới", null=True, blank=True)
    risk_band = models.CharField("Mức rủi ro", max_length=40)
    driver = models.CharField("Yếu tố chính (mô hình tuyến tính)", max_length=120, blank=True)

    class Meta:
        verbose_name = "Điểm khách hàng"
        verbose_name_plural = "Điểm khách hàng"
        indexes = [models.Index(fields=["run", "risk_band"])]


class AnomalyEvent(models.Model):
    wagtail_reference_index_ignore = True

    run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="anomalies")
    date = models.DateField()
    scope = models.CharField(max_length=80)
    algorithm = models.CharField(max_length=60)
    actual = models.DecimalField(max_digits=18, decimal_places=2)
    expected = models.DecimalField(max_digits=18, decimal_places=2)
    deviation_pct = models.FloatField()
    score = models.FloatField()
    severity = models.CharField(max_length=10)

    class Meta:
        verbose_name = "Bất thường"
        verbose_name_plural = "Bất thường"


class BasketRule(models.Model):
    wagtail_reference_index_ignore = True

    run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="basket_rules")
    antecedent_sku = models.CharField(max_length=30)
    antecedent_name = models.CharField(max_length=200)
    consequent_sku = models.CharField(max_length=30)
    consequent_name = models.CharField(max_length=200)
    pair_orders = models.PositiveIntegerField()
    support = models.FloatField()
    confidence = models.FloatField()
    lift = models.FloatField()

    class Meta:
        verbose_name = "Luật mua kèm"
        verbose_name_plural = "Luật mua kèm"


class AIInsight(models.Model):
    wagtail_reference_index_ignore = True

    run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="insights")
    kind = models.CharField(max_length=30)
    title = models.CharField(max_length=200)
    body = models.TextField()
    generated_by_ai = models.BooleanField(default=False)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Nhận xét AI"
        verbose_name_plural = "Nhận xét AI"
        ordering = ["id"]
