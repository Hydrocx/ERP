from decimal import Decimal

import factory

from catalog.models import Category, Customer, Product, Supplier, Warehouse
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales.models import SalesOrder, SalesOrderLine


class CategoryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Danh mục {n}")


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier

    code = factory.Sequence(lambda n: f"NCC{n:03d}")
    name = factory.Sequence(lambda n: f"Nhà cung cấp {n}")
    lead_time_days = 7


class WarehouseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Warehouse

    code = factory.Sequence(lambda n: f"K{n:02d}")
    name = factory.Sequence(lambda n: f"Kho {n}")


class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer

    code = factory.Sequence(lambda n: f"KH{n:04d}")
    name = factory.Sequence(lambda n: f"Khách hàng {n}")


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    sku = factory.Sequence(lambda n: f"SP{n:04d}")
    name = factory.Sequence(lambda n: f"Sản phẩm {n}")
    category = factory.SubFactory(CategoryFactory)
    default_supplier = factory.SubFactory(SupplierFactory)
    cost_price = Decimal("10000")
    sale_price = Decimal("15000")
    reorder_point = 10


class PurchaseOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PurchaseOrder

    supplier = factory.SubFactory(SupplierFactory)
    warehouse = factory.SubFactory(WarehouseFactory)


class PurchaseOrderLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PurchaseOrderLine

    order = factory.SubFactory(PurchaseOrderFactory)
    product = factory.SubFactory(ProductFactory)
    quantity = 10
    unit_price = Decimal("10000")


class SalesOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SalesOrder

    customer = factory.SubFactory(CustomerFactory)
    warehouse = factory.SubFactory(WarehouseFactory)


class SalesOrderLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SalesOrderLine

    order = factory.SubFactory(SalesOrderFactory)
    product = factory.SubFactory(ProductFactory)
    quantity = 5
