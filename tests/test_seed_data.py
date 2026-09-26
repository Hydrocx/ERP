import pytest
from django.core.management import call_command
from django.db.models import Sum

from catalog.models import Product
from inventory.models import StockLevel, StockMovement
from sales.models import SalesOrder

pytestmark = pytest.mark.django_db


def test_seed_data_is_consistent():
    call_command("seed_data", days=10, customers=10, verbosity=0)

    assert Product.objects.count() == 50
    assert SalesOrder.objects.filter(status=SalesOrder.Status.SHIPPED).exists()
    # Ledger and stock levels must agree for every product/warehouse
    for level in StockLevel.objects.all():
        total = StockMovement.objects.filter(
            product=level.product, warehouse=level.warehouse
        ).aggregate(s=Sum("quantity"))["s"]
        assert total == level.on_hand
        assert 0 <= level.reserved <= level.on_hand
