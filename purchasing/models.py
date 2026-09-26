from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from modelcluster.fields import ParentalKey
from modelcluster.models import ClusterableModel
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel
from wagtail.models import Orderable

from catalog.models import Product, Supplier, Warehouse
from core.models import TimeStampedModel
from core.utils import next_document_code


class PurchaseOrder(TimeStampedModel, ClusterableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Nháp"
        APPROVED = "APPROVED", "Đã duyệt"
        PARTIAL = "PARTIAL", "Nhận một phần"
        RECEIVED = "RECEIVED", "Đã nhận đủ"
        CANCELLED = "CANCELLED", "Đã hủy"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Thủ công"
        AI = "AI", "Đề xuất AI"
        INVOICE_OCR = "INVOICE_OCR", "Đọc hóa đơn"

    OPEN_STATUSES = (Status.APPROVED, Status.PARTIAL)

    code = models.CharField("Số PO", max_length=30, unique=True, blank=True, editable=False)
    supplier = models.ForeignKey(
        Supplier, verbose_name="Nhà cung cấp", on_delete=models.PROTECT, related_name="purchase_orders"
    )
    warehouse = models.ForeignKey(
        Warehouse, verbose_name="Kho nhận", on_delete=models.PROTECT, related_name="purchase_orders"
    )
    status = models.CharField(
        "Trạng thái", max_length=20, choices=Status.choices, default=Status.DRAFT, editable=False
    )
    source = models.CharField(
        "Nguồn tạo", max_length=20, choices=Source.choices, default=Source.MANUAL, editable=False
    )
    order_date = models.DateField("Ngày đặt", default=timezone.localdate)
    expected_date = models.DateField("Ngày dự kiến nhận", null=True, blank=True)
    received_date = models.DateField("Ngày nhận đủ", null=True, blank=True, editable=False)
    total_amount = models.DecimalField(
        "Tổng tiền", max_digits=16, decimal_places=0, default=0, editable=False
    )
    note = models.TextField("Ghi chú", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Người tạo", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+", editable=False,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Người duyệt", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+", editable=False,
    )
    approved_at = models.DateTimeField("Ngày duyệt", null=True, blank=True, editable=False)

    panels = [
        FieldRowPanel([FieldPanel("supplier"), FieldPanel("warehouse")]),
        FieldRowPanel([FieldPanel("order_date"), FieldPanel("expected_date")]),
        FieldPanel("note"),
        InlinePanel("lines", heading="Dòng hàng", label="Sản phẩm", min_num=1),
    ]

    class Meta:
        verbose_name = "Đơn mua hàng"
        verbose_name_plural = "Đơn mua hàng (PO)"
        ordering = ["-order_date", "-code"]
        permissions = [
            ("approve_purchaseorder", "Có thể duyệt / hủy đơn mua hàng"),
            ("receive_purchaseorder", "Có thể nhận hàng theo đơn mua"),
        ]

    def __str__(self):
        return self.code or "PO (mới)"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_document_code(PurchaseOrder, "PO", self.order_date)
        self.total_amount = sum(
            (line.quantity or 0) * (line.unit_price or 0) for line in self.lines.all()
        )
        super().save(*args, **kwargs)

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    @property
    def is_late(self):
        if not self.expected_date:
            return False
        if self.status == self.Status.RECEIVED:
            return bool(self.received_date and self.received_date > self.expected_date)
        return self.status in self.OPEN_STATUSES and timezone.localdate() > self.expected_date


class PurchaseOrderLine(Orderable):
    order = ParentalKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        Product, verbose_name="Sản phẩm", on_delete=models.PROTECT, related_name="purchase_lines"
    )
    quantity = models.PositiveIntegerField("Số lượng", validators=[MinValueValidator(1)])
    received_quantity = models.PositiveIntegerField("Đã nhận", default=0, editable=False)
    unit_price = models.DecimalField("Đơn giá", max_digits=14, decimal_places=0)

    panels = [
        FieldRowPanel([FieldPanel("product"), FieldPanel("quantity"), FieldPanel("unit_price")]),
    ]

    class Meta(Orderable.Meta):
        verbose_name = "Dòng PO"
        verbose_name_plural = "Dòng PO"

    def __str__(self):
        return f"{self.product.sku} x {self.quantity}"

    @property
    def remaining_quantity(self):
        return self.quantity - self.received_quantity

    @property
    def line_total(self):
        return self.quantity * self.unit_price
