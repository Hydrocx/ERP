from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel
from wagtail.models import Orderable

from catalog.models import Customer, Product, Warehouse
from core.models import TimeStampedModel
from core.utils import next_document_code


class SalesOrder(TimeStampedModel, ClusterableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Nháp"
        CONFIRMED = "CONFIRMED", "Đã xác nhận"
        SHIPPED = "SHIPPED", "Đã giao"
        CANCELLED = "CANCELLED", "Đã hủy"

    code = models.CharField("Số SO", max_length=30, unique=True, blank=True, editable=False)
    customer = models.ForeignKey(
        Customer, verbose_name="Khách hàng", on_delete=models.PROTECT, related_name="sales_orders"
    )
    warehouse = models.ForeignKey(
        Warehouse, verbose_name="Kho xuất", on_delete=models.PROTECT, related_name="sales_orders"
    )
    status = models.CharField(
        "Trạng thái", max_length=20, choices=Status.choices, default=Status.DRAFT, editable=False
    )
    order_date = models.DateField("Ngày đặt", default=timezone.localdate)
    confirmed_at = models.DateTimeField("Ngày xác nhận", null=True, blank=True, editable=False)
    shipped_at = models.DateTimeField("Ngày giao", null=True, blank=True, editable=False, db_index=True)
    total_amount = models.DecimalField(
        "Tổng tiền", max_digits=16, decimal_places=0, default=0, editable=False
    )
    note = models.TextField("Ghi chú", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Người tạo", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+", editable=False,
    )

    panels = [
        FieldRowPanel([FieldPanel("customer"), FieldPanel("warehouse")]),
        FieldPanel("order_date"),
        FieldPanel("note"),
        InlinePanel("lines", heading="Dòng hàng", label="Sản phẩm", min_num=1),
    ]

    class Meta:
        verbose_name = "Đơn bán hàng"
        verbose_name_plural = "Đơn bán hàng (SO)"
        ordering = ["-order_date", "-code"]
        permissions = [
            ("confirm_salesorder", "Có thể xác nhận / hủy đơn bán hàng"),
            ("ship_salesorder", "Có thể xuất kho giao đơn bán hàng"),
        ]

    def __str__(self):
        return self.code or "SO (mới)"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_document_code(SalesOrder, "SO", self.order_date)
        lines = list(self.lines.all())
        for line in lines:
            if line.unit_price is None and line.product_id:
                line.unit_price = line.product.sale_price
        self.total_amount = sum((line.quantity or 0) * (line.unit_price or 0) for line in lines)
        super().save(*args, **kwargs)

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT


class SalesOrderLine(Orderable):
    order = ParentalKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        Product, verbose_name="Sản phẩm", on_delete=models.PROTECT, related_name="sales_lines"
    )
    quantity = models.PositiveIntegerField("Số lượng", validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        "Đơn giá", max_digits=14, decimal_places=0, blank=True,
        help_text="Để trống để lấy giá bán của sản phẩm.",
    )

    panels = [
        FieldRowPanel([FieldPanel("product"), FieldPanel("quantity"), FieldPanel("unit_price")]),
    ]

    class Meta(Orderable.Meta):
        verbose_name = "Dòng SO"
        verbose_name_plural = "Dòng SO"

    def __str__(self):
        return f"{self.product.sku} x {self.quantity}"

    def save(self, *args, **kwargs):
        if self.unit_price is None:
            self.unit_price = self.product.sale_price
        super().save(*args, **kwargs)

    @property
    def line_total(self):
        return self.quantity * self.unit_price
