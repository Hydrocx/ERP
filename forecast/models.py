from django.conf import settings
from django.db import models

from catalog.models import Product, Supplier, Warehouse
from core.models import TimeStampedModel


class DemandForecast(models.Model):
    wagtail_reference_index_ignore = True

    run_date = models.DateField("Ngày chạy", db_index=True)
    product = models.ForeignKey(Product, verbose_name="Sản phẩm", on_delete=models.CASCADE,
                                related_name="forecasts")
    warehouse = models.ForeignKey(Warehouse, verbose_name="Kho", on_delete=models.CASCADE,
                                  related_name="forecasts")
    method = models.CharField("Phương pháp", max_length=50, default="level+seasonal")
    window_days = models.PositiveIntegerField("Số ngày lịch sử")
    avg_daily = models.DecimalField("TB/ngày (cả kỳ)", max_digits=10, decimal_places=3)
    recent_daily = models.DecimalField("TB/ngày (28 ngày)", max_digits=10, decimal_places=3)
    std_daily = models.DecimalField("Độ lệch chuẩn/ngày", max_digits=10, decimal_places=3)
    seasonal_factor = models.DecimalField("Hệ số mùa vụ", max_digits=6, decimal_places=3)
    forecast_daily = models.DecimalField("Dự báo/ngày", max_digits=10, decimal_places=3)
    lead_time_days = models.PositiveIntegerField("Lead time")
    safety_stock = models.PositiveIntegerField("Tồn an toàn")
    reorder_point = models.PositiveIntegerField("ROP")
    order_up_to = models.PositiveIntegerField("Mức tồn tối đa (S)")
    available = models.IntegerField("Tồn khả dụng")
    on_order = models.IntegerField("Đang về")
    stockout_days = models.PositiveIntegerField("Số ngày hết hàng", default=0)
    weekly_sales = models.JSONField("Doanh số 12 tuần", default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dự báo nhu cầu"
        verbose_name_plural = "Dự báo nhu cầu"
        ordering = ["-run_date", "warehouse__code", "product__sku"]
        constraints = [
            models.UniqueConstraint(fields=["run_date", "product", "warehouse"], name="uniq_forecast_run")
        ]

    def __str__(self):
        return f"{self.product.sku} @ {self.warehouse.code} ({self.run_date:%d/%m/%Y})"

    @property
    def days_of_cover(self):
        if self.forecast_daily <= 0:
            return None
        return round(float(self.available) / float(self.forecast_daily), 1)


class ReorderSuggestion(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ duyệt"
        APPROVED = "APPROVED", "Đã tạo PO"
        REJECTED = "REJECTED", "Từ chối"
        EXPIRED = "EXPIRED", "Hết hiệu lực"

    class AIAction(models.TextChoices):
        NONE = "", "Chưa có AI"
        ORDER = "order", "Nên đặt"
        WAIT = "wait", "Chờ thêm"
        REVIEW = "review", "Cần xem lại"

    wagtail_reference_index_ignore = True

    forecast = models.ForeignKey(DemandForecast, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="suggestions")
    product = models.ForeignKey(Product, verbose_name="Sản phẩm", on_delete=models.CASCADE,
                                related_name="reorder_suggestions")
    warehouse = models.ForeignKey(Warehouse, verbose_name="Kho", on_delete=models.CASCADE,
                                  related_name="reorder_suggestions")
    supplier = models.ForeignKey(Supplier, verbose_name="Nhà cung cấp", on_delete=models.PROTECT,
                                 related_name="reorder_suggestions")
    available = models.IntegerField("Tồn khả dụng")
    on_order = models.IntegerField("Đang về")
    reorder_point = models.PositiveIntegerField("ROP")
    suggested_qty = models.PositiveIntegerField("SL hệ thống đề xuất")

    ai_action = models.CharField("AI khuyến nghị", max_length=10, choices=AIAction.choices, blank=True)
    ai_adjusted_qty = models.PositiveIntegerField("SL AI đề xuất", null=True, blank=True)
    ai_confidence = models.CharField("Độ tin cậy", max_length=10, blank=True)
    ai_reason = models.TextField("Lý do", blank=True)
    ai_flagged = models.BooleanField("Bị guardrail chặn", default=False)

    status = models.CharField("Trạng thái", max_length=10, choices=Status.choices, default=Status.PENDING,
                              db_index=True)
    final_qty = models.PositiveIntegerField("SL duyệt", null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="Người duyệt")
    decided_at = models.DateTimeField("Ngày duyệt", null=True, blank=True)
    purchase_order = models.ForeignKey("purchasing.PurchaseOrder", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="suggestions",
                                       verbose_name="PO")

    class Meta:
        verbose_name = "Đề xuất nhập hàng"
        verbose_name_plural = "Đề xuất nhập hàng"
        ordering = ["warehouse__code", "product__sku"]

    def __str__(self):
        return f"{self.product.sku} @ {self.warehouse.code}: {self.recommended_qty}"

    @property
    def recommended_qty(self):
        if self.ai_adjusted_qty is not None and not self.ai_flagged and self.ai_action == self.AIAction.ORDER:
            return self.ai_adjusted_qty
        return self.suggested_qty
