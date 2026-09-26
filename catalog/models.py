from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from wagtail.admin.panels import FieldPanel, FieldRowPanel, MultiFieldPanel

from core.models import TimeStampedModel


class Category(TimeStampedModel):
    name = models.CharField("Tên danh mục", max_length=100, unique=True)
    parent = models.ForeignKey(
        "self",
        verbose_name="Danh mục cha",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    description = models.TextField("Mô tả", blank=True)

    panels = [FieldPanel("name"), FieldPanel("parent"), FieldPanel("description")]

    class Meta:
        verbose_name = "Danh mục"
        verbose_name_plural = "Danh mục sản phẩm"
        ordering = ["name"]

    def __str__(self):
        return f"{self.parent} / {self.name}" if self.parent else self.name


class Supplier(TimeStampedModel):
    code = models.CharField("Mã NCC", max_length=20, unique=True)
    name = models.CharField("Tên nhà cung cấp", max_length=200)
    contact_person = models.CharField("Người liên hệ", max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField("Điện thoại", max_length=30, blank=True)
    address = models.CharField("Địa chỉ", max_length=255, blank=True)
    tax_code = models.CharField("Mã số thuế", max_length=20, blank=True)
    lead_time_days = models.PositiveIntegerField(
        "Thời gian giao hàng (ngày)", default=7
    )
    rating = models.PositiveSmallIntegerField(
        "Đánh giá",
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1 (kém) đến 5 (rất tốt)",
    )
    is_active = models.BooleanField("Đang hợp tác", default=True)

    panels = [
        FieldRowPanel([FieldPanel("code"), FieldPanel("name")]),
        MultiFieldPanel(
            [
                FieldPanel("contact_person"),
                FieldRowPanel([FieldPanel("email"), FieldPanel("phone")]),
                FieldPanel("address"),
                FieldPanel("tax_code"),
            ],
            heading="Liên hệ",
        ),
        FieldRowPanel([FieldPanel("lead_time_days"), FieldPanel("rating")]),
        FieldPanel("is_active"),
    ]

    class Meta:
        verbose_name = "Nhà cung cấp"
        verbose_name_plural = "Nhà cung cấp"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Warehouse(TimeStampedModel):
    code = models.CharField("Mã kho", max_length=20, unique=True)
    name = models.CharField("Tên kho", max_length=100)
    address = models.CharField("Địa chỉ", max_length=255, blank=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Thủ kho",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_warehouses",
    )
    is_active = models.BooleanField("Đang hoạt động", default=True)

    panels = [
        FieldRowPanel([FieldPanel("code"), FieldPanel("name")]),
        FieldPanel("address"),
        FieldPanel("manager"),
        FieldPanel("is_active"),
    ]

    class Meta:
        verbose_name = "Kho"
        verbose_name_plural = "Kho hàng"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Customer(TimeStampedModel):
    code = models.CharField("Mã KH", max_length=20, unique=True)
    name = models.CharField("Tên khách hàng", max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField("Điện thoại", max_length=30, blank=True)
    address = models.CharField("Địa chỉ", max_length=255, blank=True)
    tax_code = models.CharField("Mã số thuế", max_length=20, blank=True)
    is_active = models.BooleanField("Đang giao dịch", default=True)

    panels = [
        FieldRowPanel([FieldPanel("code"), FieldPanel("name")]),
        FieldRowPanel([FieldPanel("email"), FieldPanel("phone")]),
        FieldPanel("address"),
        FieldPanel("tax_code"),
        FieldPanel("is_active"),
    ]

    class Meta:
        verbose_name = "Khách hàng"
        verbose_name_plural = "Khách hàng"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Product(TimeStampedModel):
    class Unit(models.TextChoices):
        PIECE = "cai", "Cái"
        BOX = "hop", "Hộp"
        PACK = "goi", "Gói"
        BOTTLE = "chai", "Chai"
        CAN = "lon", "Lon"
        CARTON = "thung", "Thùng"
        KG = "kg", "Kg"
        SET = "bo", "Bộ"

    sku = models.CharField("Mã SKU", max_length=30, unique=True)
    name = models.CharField("Tên sản phẩm", max_length=200)
    category = models.ForeignKey(
        Category,
        verbose_name="Danh mục",
        on_delete=models.PROTECT,
        related_name="products",
    )
    unit = models.CharField(
        "Đơn vị tính", max_length=10, choices=Unit.choices, default=Unit.PIECE
    )
    cost_price = models.DecimalField("Giá nhập", max_digits=14, decimal_places=0)
    sale_price = models.DecimalField("Giá bán", max_digits=14, decimal_places=0)
    default_supplier = models.ForeignKey(
        Supplier,
        verbose_name="NCC mặc định",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="products",
    )
    reorder_point = models.PositiveIntegerField(
        "Điểm đặt hàng lại (ROP)",
        default=0,
        help_text="Khi tồn khả dụng + đang về ≤ ROP thì cần đặt hàng. Tuần 2 sẽ tính tự động.",
    )
    safety_stock = models.PositiveIntegerField("Tồn kho an toàn", default=0)
    lead_time_days = models.PositiveIntegerField(
        "Lead time (ngày)",
        null=True,
        blank=True,
        help_text="Để trống để dùng lead time của NCC mặc định.",
    )
    image = models.ForeignKey(
        "wagtailimages.Image",
        verbose_name="Hình ảnh",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    description = models.TextField("Mô tả", blank=True)
    is_active = models.BooleanField("Đang kinh doanh", default=True)

    panels = [
        FieldRowPanel([FieldPanel("sku"), FieldPanel("name")]),
        FieldRowPanel([FieldPanel("category"), FieldPanel("unit")]),
        FieldRowPanel([FieldPanel("cost_price"), FieldPanel("sale_price")]),
        MultiFieldPanel(
            [
                FieldPanel("default_supplier"),
                FieldRowPanel(
                    [
                        FieldPanel("reorder_point"),
                        FieldPanel("safety_stock"),
                        FieldPanel("lead_time_days"),
                    ]
                ),
            ],
            heading="Chính sách tồn kho",
        ),
        FieldPanel("image"),
        FieldPanel("description"),
        FieldPanel("is_active"),
    ]

    class Meta:
        verbose_name = "Sản phẩm"
        verbose_name_plural = "Sản phẩm"
        ordering = ["sku"]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    @property
    def effective_lead_time(self):
        if self.lead_time_days is not None:
            return self.lead_time_days
        if self.default_supplier_id:
            return self.default_supplier.lead_time_days
        return 7
