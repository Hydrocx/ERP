from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from catalog.models import Product, Warehouse


class StockLevel(models.Model):
    """Current quantity of one product in one warehouse.

    Never edit directly - always go through inventory.services so that every
    change has a matching StockMovement.
    """

    # High-volume rows; keep them out of Wagtail's "usage" reference index.
    wagtail_reference_index_ignore = True

    product = models.ForeignKey(
        Product, verbose_name="Sản phẩm", on_delete=models.PROTECT, related_name="stock_levels"
    )
    warehouse = models.ForeignKey(
        Warehouse, verbose_name="Kho", on_delete=models.PROTECT, related_name="stock_levels"
    )
    on_hand = models.IntegerField("Tồn thực tế", default=0)
    reserved = models.IntegerField("Đã giữ cho đơn bán", default=0)
    avg_cost = models.DecimalField(
        "Giá vốn bình quân", max_digits=14, decimal_places=2, default=0
    )
    reorder_point = models.PositiveIntegerField(
        "ROP tại kho", null=True, blank=True,
        help_text="Do lệnh run_forecast tính; để trống = dùng ROP của sản phẩm.",
    )
    safety_stock = models.PositiveIntegerField("Tồn an toàn tại kho", null=True, blank=True)
    updated_at = models.DateTimeField("Cập nhật", auto_now=True)

    class Meta:
        verbose_name = "Tồn kho"
        verbose_name_plural = "Tồn kho"
        ordering = ["warehouse__code", "product__sku"]
        permissions = [("adjust_stocklevel", "Có thể điều chỉnh tồn kho / chuyển kho")]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "warehouse"], name="uniq_stock_product_warehouse"
            ),
            models.CheckConstraint(condition=Q(on_hand__gte=0), name="stock_on_hand_gte_0"),
            models.CheckConstraint(condition=Q(reserved__gte=0), name="stock_reserved_gte_0"),
            models.CheckConstraint(
                condition=Q(reserved__lte=F("on_hand")), name="stock_reserved_lte_on_hand"
            ),
        ]

    def __str__(self):
        return f"{self.product.sku} @ {self.warehouse.code}: {self.on_hand}"

    @property
    def available(self):
        return self.on_hand - self.reserved

    @property
    def stock_value(self):
        return self.on_hand * self.avg_cost

    @property
    def effective_reorder_point(self):
        return self.reorder_point if self.reorder_point is not None else self.product.reorder_point

    @property
    def status(self):
        if self.available <= 0:
            return "Hết hàng"
        if self.available <= self.effective_reorder_point:
            return "Sắp hết"
        return "Đủ hàng"

    @property
    def status_code(self):
        return {"Hết hàng": "out", "Sắp hết": "low", "Đủ hàng": "ok"}[self.status]


class StockMovement(models.Model):
    """Immutable ledger of stock changes. quantity is signed (+ in, - out)."""

    wagtail_reference_index_ignore = True

    class Type(models.TextChoices):
        IN = "IN", "Nhập kho"
        OUT = "OUT", "Xuất kho"
        ADJUST = "ADJUST", "Điều chỉnh"
        TRANSFER_IN = "TRANSFER_IN", "Chuyển kho đến"
        TRANSFER_OUT = "TRANSFER_OUT", "Chuyển kho đi"

    product = models.ForeignKey(
        Product, verbose_name="Sản phẩm", on_delete=models.PROTECT, related_name="movements"
    )
    warehouse = models.ForeignKey(
        Warehouse, verbose_name="Kho", on_delete=models.PROTECT, related_name="movements"
    )
    movement_type = models.CharField("Loại", max_length=20, choices=Type.choices)
    quantity = models.IntegerField("Số lượng (+/-)")
    balance_after = models.IntegerField("Tồn sau giao dịch")
    unit_cost = models.DecimalField(
        "Đơn giá", max_digits=14, decimal_places=2, null=True, blank=True
    )
    reference = models.CharField("Chứng từ", max_length=50, blank=True, db_index=True)
    note = models.CharField("Ghi chú", max_length=255, blank=True)
    occurred_at = models.DateTimeField("Thời điểm", default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người thực hiện",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField("Ngày ghi sổ", auto_now_add=True)

    class Meta:
        verbose_name = "Phiếu xuất nhập"
        verbose_name_plural = "Lịch sử xuất nhập"
        ordering = ["-occurred_at", "-id"]
        indexes = [models.Index(fields=["product", "warehouse", "occurred_at"])]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity:+d} {self.product.sku} @ {self.warehouse.code}"
