from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from wagtail.admin.panels import FieldPanel, FieldRowPanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseGenericSetting, register_setting


@register_setting(icon="cogs")
class AISettings(BaseGenericSetting):
    """Editable in Wagtail admin: Settings > Cấu hình AI & dự báo.

    The OpenAI API key is never stored here - it only comes from the environment.
    """

    enabled = models.BooleanField("Bật tính năng AI", default=True)
    model = models.CharField(
        "Model OpenAI", max_length=100, blank=True,
        help_text="Để trống để dùng biến môi trường OPENAI_MODEL.",
    )
    temperature = models.DecimalField(
        "Temperature", max_digits=3, decimal_places=2, null=True, blank=True, default=Decimal("0.2"),
        validators=[MinValueValidator(0), MaxValueValidator(2)],
        help_text="Để trống nếu model không hỗ trợ tham số temperature.",
    )
    enable_reorder = models.BooleanField("AI giải thích đề xuất nhập hàng", default=True)
    enable_report = models.BooleanField("AI viết báo cáo", default=True)
    enable_assistant = models.BooleanField("Trợ lý hỏi đáp dữ liệu", default=True)
    enable_invoice = models.BooleanField("Đọc hóa đơn NCC từ ảnh", default=True)
    max_adjust_pct = models.PositiveIntegerField(
        "Biên điều chỉnh tối đa của AI (%)", default=30,
        validators=[MaxValueValidator(100)],
        help_text="AI chỉ được đổi số lượng đề xuất trong biên này; vượt quá thì giữ số của hệ thống và gắn cờ.",
    )

    service_level = models.DecimalField(
        "Mức phục vụ mục tiêu", max_digits=4, decimal_places=3, default=Decimal("0.950"),
        validators=[MinValueValidator(Decimal("0.5")), MaxValueValidator(Decimal("0.999"))],
        help_text="0.95 = 95% chu kỳ không hết hàng (z ≈ 1.65).",
    )
    review_period_days = models.PositiveIntegerField("Chu kỳ xem xét đặt hàng (ngày)", default=7)
    forecast_window_days = models.PositiveIntegerField(
        "Số ngày lịch sử dùng để dự báo", default=90, validators=[MinValueValidator(28)]
    )

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("enabled"),
                FieldRowPanel([FieldPanel("model"), FieldPanel("temperature")]),
                FieldRowPanel([FieldPanel("enable_reorder"), FieldPanel("enable_report"),
                               FieldPanel("enable_assistant"), FieldPanel("enable_invoice")]),
                FieldPanel("max_adjust_pct"),
            ],
            heading="AI",
        ),
        MultiFieldPanel(
            [FieldRowPanel([FieldPanel("service_level"), FieldPanel("review_period_days"),
                            FieldPanel("forecast_window_days")])],
            heading="Dự báo & chính sách tồn kho",
        ),
    ]

    class Meta:
        verbose_name = "Cấu hình AI & dự báo"

    @property
    def model_name(self):
        return self.model or settings.OPENAI_MODEL


class AICallLog(models.Model):
    class Feature(models.TextChoices):
        REORDER = "reorder", "Đề xuất nhập hàng"
        PRODUCT = "product", "Phân tích sản phẩm"
        REPORT = "report", "Báo cáo"
        ASSISTANT = "assistant", "Trợ lý hỏi đáp"
        INVOICE = "invoice", "Đọc hóa đơn"

    wagtail_reference_index_ignore = True

    feature = models.CharField("Tính năng", max_length=20, choices=Feature.choices)
    model = models.CharField("Model", max_length=100)
    success = models.BooleanField("Thành công", default=True)
    error = models.TextField("Lỗi", blank=True)
    input_tokens = models.PositiveIntegerField("Token vào", default=0)
    output_tokens = models.PositiveIntegerField("Token ra", default=0)
    latency_ms = models.PositiveIntegerField("Thời gian (ms)", default=0)
    request_excerpt = models.TextField("Trích yêu cầu", blank=True)
    response_excerpt = models.TextField("Trích phản hồi", blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Người dùng", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField("Thời điểm", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Nhật ký gọi AI"
        verbose_name_plural = "Nhật ký gọi AI"
        ordering = ["-created_at"]
        permissions = [
            ("use_assistant", "Có thể dùng trợ lý AI"),
            ("generate_report", "Có thể tạo báo cáo AI"),
            ("run_forecast", "Có thể chạy dự báo nhập hàng"),
        ]

    def __str__(self):
        return f"{self.get_feature_display()} @ {self.created_at:%d/%m/%Y %H:%M}"
