"""Generate realistic demo data by simulating N days of operations.

Every stock change goes through the real services (receive_po, confirm_so,
ship_so, ...), so the resulting data is consistent and the services get
exercised end-to-end. Demand has category seasonality, weekday effects and
a growth trend, which gives the forecasting / AI features in week 2 something
meaningful to work with.
"""

import math
import random
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from faker import Faker
from wagtail.signal_handlers import disable_reference_index_auto_update

from catalog.models import Category, Customer, Product, Supplier, Warehouse
from core.exceptions import InsufficientStockError
from inventory import services as stock
from inventory.models import StockLevel, StockMovement
from purchasing import services as purchasing
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales import services as sales
from sales.models import SalesOrder, SalesOrderLine

U = Product.Unit

# Monthly demand multipliers (Jan..Dec)
SEASONALITY = {
    "Đồ uống": [1.2, 1.1, 1.0, 1.2, 1.4, 1.5, 1.5, 1.4, 1.1, 1.0, 0.9, 1.1],
    "Bánh kẹo": [2.0, 1.2, 0.8, 0.8, 0.8, 0.8, 0.8, 0.9, 1.3, 0.9, 1.0, 1.6],
    "Hóa mỹ phẩm": [1.2, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1],
    "Văn phòng phẩm": [0.7, 0.6, 0.9, 1.0, 1.0, 0.9, 1.0, 1.8, 1.5, 1.0, 1.0, 0.9],
    "Gia dụng": [1.3, 1.0, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.3],
    "Thực phẩm khô": [1.3, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.2, 1.2, 1.2],
}
CUSTOM_SEASONALITY = {
    "MUT-TET": [3.0, 0.8, 0, 0, 0, 0, 0, 0, 0, 0, 0.5, 3.0],
    "BTT-4B": [0, 0, 0, 0, 0, 0, 0.3, 2.5, 3.0, 0.2, 0, 0],
    "GD-QUAT": [0.3, 0.4, 0.8, 1.5, 2.0, 2.0, 1.8, 1.4, 0.8, 0.4, 0.3, 0.3],
}
WEEKDAY = [1.05, 1.0, 1.0, 1.05, 1.1, 0.8, 0.5]  # Mon..Sun
YEARLY_GROWTH = 0.15
SLOW_MOVERS = {"GD-CHAO", "GD-KHAN", "VPP-MTINH"}

# (sku, name, unit, cost_price)
PRODUCTS = {
    "Đồ uống": [
        ("DU-NKHOANG", "Nước khoáng 500ml (thùng 24 chai)", U.CARTON, 85000),
        ("DU-NGOT", "Nước ngọt có ga 330ml (thùng 24 lon)", U.CARTON, 180000),
        ("DU-TRAXANH", "Trà xanh 455ml (thùng 24 chai)", U.CARTON, 160000),
        ("DU-TANGLUC", "Nước tăng lực 250ml (thùng 24 lon)", U.CARTON, 220000),
        ("DU-CFSUA", "Cà phê sữa lon 180ml (thùng 24 lon)", U.CARTON, 190000),
        ("DU-SUATUOI", "Sữa tươi tiệt trùng 180ml (thùng 48 hộp)", U.CARTON, 320000),
        ("DU-EPCAM", "Nước ép cam 1L", U.BOTTLE, 32000),
        ("DU-BIA", "Bia lon 330ml (thùng 24 lon)", U.CARTON, 290000),
        ("DU-TRASUA", "Trà sữa đóng chai 350ml (thùng 24 chai)", U.CARTON, 200000),
        ("DU-CF3IN1", "Cà phê hòa tan 3in1 (hộp 20 gói)", U.BOX, 55000),
    ],
    "Bánh kẹo": [
        ("BK-QUYBO", "Bánh quy bơ hộp thiếc 454g", U.BOX, 95000),
        ("BK-KEODEO", "Kẹo dẻo trái cây 100g", U.PACK, 12000),
        ("BK-XOP", "Bánh xốp 200g", U.PACK, 22000),
        ("BK-SOCOLA", "Socola thanh 40g", U.PACK, 18000),
        ("BK-GAO", "Bánh gạo 150g", U.PACK, 20000),
        ("BK-SNACK", "Snack khoai tây 52g", U.PACK, 9000),
        ("MUT-TET", "Mứt Tết thập cẩm hộp 500g", U.BOX, 120000),
        ("BK-HATDIEU", "Hạt điều rang muối 500g", U.BOX, 150000),
        ("BTT-4B", "Bánh trung thu hộp 4 bánh", U.BOX, 250000),
    ],
    "Hóa mỹ phẩm": [
        ("HM-NGIAT", "Nước giặt 3.6kg", U.BOTTLE, 140000),
        ("HM-RUACHEN", "Nước rửa chén 750g", U.BOTTLE, 28000),
        ("HM-DAUGOI", "Dầu gội 650g", U.BOTTLE, 110000),
        ("HM-SUATAM", "Sữa tắm 900g", U.BOTTLE, 125000),
        ("HM-KEMDR", "Kem đánh răng 180g", U.BOX, 30000),
        ("HM-BOTGIAT", "Bột giặt 3kg", U.PACK, 115000),
        ("HM-LAUSAN", "Nước lau sàn 1L", U.BOTTLE, 35000),
        ("HM-GIAYVS", "Giấy vệ sinh 10 cuộn", U.PACK, 55000),
    ],
    "Văn phòng phẩm": [
        ("VPP-A4", "Giấy A4 70gsm (thùng 5 ram)", U.CARTON, 330000),
        ("VPP-BUTBI", "Bút bi xanh (hộp 20 cây)", U.BOX, 60000),
        ("VPP-VO200", "Vở 200 trang (lốc 10 cuốn)", U.PACK, 95000),
        ("VPP-BIAHS", "Bìa hồ sơ nhựa (gói 10)", U.PACK, 35000),
        ("VPP-KEP", "Kẹp giấy (hộp 100)", U.BOX, 8000),
        ("VPP-BANGKEO", "Băng keo trong 5cm (cây 6 cuộn)", U.PACK, 70000),
        ("VPP-DAQUANG", "Bút dạ quang (hộp 10)", U.BOX, 55000),
        ("VPP-MTINH", "Máy tính cầm tay", U.PIECE, 280000),
    ],
    "Gia dụng": [
        ("GD-NOICOM", "Nồi cơm điện 1.8L", U.PIECE, 550000),
        ("GD-BINHGN", "Bình giữ nhiệt 500ml", U.PIECE, 120000),
        ("GD-HOPNHUA", "Bộ hộp nhựa đựng thực phẩm", U.SET, 150000),
        ("GD-CHAO", "Chảo chống dính 26cm", U.PIECE, 180000),
        ("GD-QUAT", "Quạt đứng", U.PIECE, 420000),
        ("GD-AMDUN", "Ấm siêu tốc 1.7L", U.PIECE, 210000),
        ("GD-KHAN", "Khăn tắm cotton", U.PIECE, 45000),
    ],
    "Thực phẩm khô": [
        ("TP-MITOM", "Mì tôm chua cay (thùng 30 gói)", U.CARTON, 105000),
        ("TP-PHO", "Phở ăn liền (thùng 30 gói)", U.CARTON, 160000),
        ("TP-GAO5KG", "Gạo thơm túi 5kg", U.PACK, 110000),
        ("TP-DAUAN", "Dầu ăn 1L", U.BOTTLE, 45000),
        ("TP-NUOCMAM", "Nước mắm 500ml", U.BOTTLE, 38000),
        ("TP-DUONG", "Đường tinh luyện 1kg", U.PACK, 24000),
        ("TP-HATNEM", "Hạt nêm 400g", U.PACK, 32000),
        ("TP-CHAO", "Cháo ăn liền (thùng 50 gói)", U.CARTON, 150000),
    ],
}

# (code, name, lead_time_days, rating, categories supplied)
SUPPLIERS = [
    ("NCC01", "Công ty TNHH Nước giải khát Sông Xanh", 5, 4, ["Đồ uống"]),
    ("NCC02", "Công ty CP Đồ uống Miền Nam", 7, 3, ["Đồ uống"]),
    ("NCC03", "Công ty TNHH Bánh kẹo Hương Quê", 10, 4, ["Bánh kẹo"]),
    ("NCC04", "Công ty CP Thực phẩm Đông Dương", 12, 3, ["Bánh kẹo", "Thực phẩm khô"]),
    ("NCC05", "Công ty TNHH Hóa phẩm Sạch Việt", 7, 5, ["Hóa mỹ phẩm"]),
    ("NCC06", "Công ty CP Văn phòng phẩm Trí Tuệ", 6, 4, ["Văn phòng phẩm"]),
    ("NCC07", "Công ty TNHH Gia dụng An Khang", 14, 3, ["Gia dụng"]),
    ("NCC08", "Công ty CP Lương thực Phú Điền", 8, 4, ["Thực phẩm khô"]),
    ("NCC09", "Công ty TNHH Thương mại Đại Phát", 9, 2, ["Gia dụng", "Hóa mỹ phẩm"]),
    ("NCC10", "Công ty CP Phân phối Hoàn Cầu", 10, 3, ["Văn phòng phẩm", "Bánh kẹo"]),
]

# (code, name, address, demand scale)
WAREHOUSES = [
    ("HN", "Kho Hà Nội", "KCN Quang Minh, Mê Linh, Hà Nội", 1.0),
    ("HCM", "Kho TP. Hồ Chí Minh", "KCN Tân Bình, Q. Tân Phú, TP.HCM", 1.3),
    ("DN", "Kho Đà Nẵng", "KCN Hòa Khánh, Q. Liên Chiểu, Đà Nẵng", 0.6),
]

CUSTOMER_PREFIXES = ["Cửa hàng", "Tạp hóa", "Siêu thị mini", "Đại lý", "Nhà phân phối", "Văn phòng phẩm"]


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


class Command(BaseCommand):
    help = "Tạo dữ liệu mẫu bằng cách mô phỏng hoạt động kinh doanh trong N ngày."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=400, help="Số ngày mô phỏng (mặc định 400, đủ để có dữ liệu cùng kỳ năm trước)")
        parser.add_argument("--seed", type=int, default=42, help="Random seed để tái lập dữ liệu")
        parser.add_argument("--customers", type=int, default=80)
        parser.add_argument("--reset", action="store_true", help="Xóa toàn bộ dữ liệu ERP trước khi tạo")
        parser.add_argument("--admin", action="store_true",
                            help="Tạo superuser admin/admin123 (chỉ dùng cho môi trường dev)")

    def handle(self, *args, **opts):
        self.rng = random.Random(opts["seed"])
        self.fake = Faker("vi_VN")
        self.fake.seed_instance(opts["seed"])

        if not opts["reset"] and Product.objects.exists():
            raise CommandError("Đã có dữ liệu. Dùng --reset để xóa và tạo lại.")

        # Updating Wagtail's reference index on every save is the main cost here,
        # so it is disabled during the simulation and rebuilt once at the end.
        with disable_reference_index_auto_update(), transaction.atomic():
            if opts["reset"]:
                self.reset()
            if opts["admin"]:
                self.create_admin()
            self.create_master_data(opts["customers"])
            self.simulate(opts["days"])
        self.stdout.write("Cập nhật reference index...")
        call_command("rebuild_references_index", verbosity=0)
        self.print_summary()

    # ------------------------------------------------------------------ setup

    def reset(self):
        self.stdout.write("Xóa dữ liệu cũ...")
        from forecast.models import DemandForecast, ReorderSuggestion

        for model in (ReorderSuggestion, DemandForecast, StockMovement, StockLevel, SalesOrderLine, SalesOrder, PurchaseOrderLine,
                      PurchaseOrder, Product, Category, Supplier, Customer, Warehouse):
            model.objects.all().delete()

    def create_admin(self):
        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", "admin123")
            self.stdout.write(self.style.WARNING("Đã tạo superuser admin / admin123 (chỉ dùng dev)"))

    def create_master_data(self, n_customers):
        rng, fake = self.rng, self.fake
        self.user = get_user_model().objects.filter(is_superuser=True).first()

        self.warehouses = []
        self.wh_scale = {}
        for code, name, address, scale in WAREHOUSES:
            wh = Warehouse.objects.create(code=code, name=name, address=address, manager=self.user)
            self.warehouses.append(wh)
            self.wh_scale[wh.pk] = scale

        suppliers_by_cat = defaultdict(list)
        for code, name, lead, rating, cats in SUPPLIERS:
            sup = Supplier.objects.create(
                code=code, name=name, lead_time_days=lead, rating=rating,
                contact_person=fake.name(), phone=fake.phone_number(),
                email=f"sales@{code.lower()}.example.com", address=fake.address().replace("\n", ", "),
                tax_code=f"0{rng.randint(100000000, 999999999)}",
            )
            for cat in cats:
                suppliers_by_cat[cat].append(sup)

        self.products = []
        self.base_demand = {}
        self.seasonality = {}
        for cat_name, items in PRODUCTS.items():
            category = Category.objects.create(name=cat_name)
            for sku, name, unit, cost in items:
                supplier = rng.choice(suppliers_by_cat[cat_name])
                base = min(12.0, max(0.3, 250000 / cost)) * rng.uniform(0.7, 1.3)
                if sku in SLOW_MOVERS:
                    base = 0.15
                lead = supplier.lead_time_days
                margin = Decimal(str(rng.choice([1.2, 1.25, 1.3, 1.35, 1.4])))
                product = Product.objects.create(
                    sku=sku, name=name, category=category, unit=unit,
                    cost_price=cost, sale_price=(Decimal(cost) * margin / 1000).quantize(Decimal("1")) * 1000,
                    default_supplier=supplier,
                    safety_stock=math.ceil(base * 3),
                    reorder_point=math.ceil(base * (lead + 3)),
                )
                self.products.append(product)
                self.base_demand[product.pk] = base
                self.seasonality[product.pk] = CUSTOM_SEASONALITY.get(sku, SEASONALITY[cat_name])

        self.customers = []
        for i in range(1, n_customers + 1):
            person = fake.name()
            self.customers.append(Customer.objects.create(
                code=f"KH{i:04d}", name=f"{rng.choice(CUSTOMER_PREFIXES)} {person}",
                phone=fake.phone_number(), email=f"kh{i:04d}@example.com",
                address=fake.address().replace("\n", ", "),
            ))
        # A few large customers place most orders (Pareto-like)
        self.customer_weights = [1 / (i + 1) ** 0.8 for i in range(len(self.customers))]
        self.stdout.write(f"Master data: {len(self.warehouses)} kho, {len(SUPPLIERS)} NCC, "
                          f"{len(self.products)} sản phẩm, {len(self.customers)} khách hàng")

    # ------------------------------------------------------------- simulation

    def at(self, day, hour=None):
        hour = hour if hour is not None else self.rng.randint(8, 17)
        return timezone.make_aware(datetime.combine(day, time(hour, self.rng.randint(0, 59))))

    def simulate(self, days):
        rng = self.rng
        today = timezone.localdate()
        start = today - timedelta(days=days)
        self.available = {}          # (product_id, warehouse_id) -> available qty (mirror of DB)
        self.on_order = defaultdict(int)
        self.arrivals = []            # (arrival_date, po, partial_first)

        for wh in self.warehouses:
            scale = self.wh_scale[wh.pk]
            for p in self.products:
                qty = max(5, math.ceil(p.reorder_point * scale * 2))
                stock.adjust_stock(p, wh, qty, reason="Tồn đầu kỳ", unit_cost=p.cost_price,
                                   user=self.user, occurred_at=self.at(start, 7))
                self.available[(p.pk, wh.pk)] = qty

        day = start + timedelta(days=1)
        while day <= today:
            last_days = (today - day).days
            self.receive_arrivals(day)
            for wh in self.warehouses:
                self.sell(day, wh, leave_open=last_days <= 1)
                self.replenish(day, wh, approve=last_days > 0)
            if day.day == 1 or day == today:
                self.stdout.write(f"  {day:%Y-%m-%d}: {SalesOrder.objects.count()} SO, "
                                  f"{PurchaseOrder.objects.count()} PO")
            day += timedelta(days=1)

    def receive_arrivals(self, day):
        pending = []
        for arrival, po, partial_first in self.arrivals:
            if arrival > day:
                pending.append((arrival, po, partial_first))
                continue
            if partial_first:
                qtys = {line.pk: max(1, line.quantity * 7 // 10) for line in po.lines.all()}
                purchasing.receive_po(po, qtys, user=self.user, at=self.at(day))
                pending.append((day + timedelta(days=2), po, False))
                received = qtys
            else:
                received = {line.pk: line.remaining_quantity for line in po.lines.all()}
                purchasing.receive_po(po, user=self.user, at=self.at(day))
            for line in po.lines.all():
                key = (line.product_id, po.warehouse_id)
                qty = received.get(line.pk, 0)
                self.available[key] += qty
                self.on_order[key] -= qty
        self.arrivals = pending

    def sell(self, day, wh, leave_open):
        rng = self.rng
        scale = self.wh_scale[wh.pk]
        growth = 1 + YEARLY_GROWTH * (1 - (timezone.localdate() - day).days / 365)
        factor = scale * WEEKDAY[day.weekday()] * growth

        n_orders = max(1, poisson(rng, 6 * factor))
        baskets = [defaultdict(int) for _ in range(n_orders)]
        for p in self.products:
            lam = self.base_demand[p.pk] * self.seasonality[p.pk][day.month - 1] * factor
            demand = poisson(rng, lam)
            key = (p.pk, wh.pk)
            demand = min(demand, self.available[key])  # stock-out: lost sales
            if demand <= 0:
                continue
            parts = [demand] if demand < 10 or rng.random() < 0.5 else [demand // 2, demand - demand // 2]
            for part in parts:
                baskets[rng.randrange(n_orders)][p] += part
            self.available[key] -= demand

        for basket in baskets:
            if not basket:
                continue
            customer = rng.choices(self.customers, weights=self.customer_weights)[0]
            so = SalesOrder(customer=customer, warehouse=wh, order_date=day, created_by=self.user)
            so.lines = [SalesOrderLine(product=p, quantity=q, unit_price=p.sale_price)
                        for p, q in basket.items()]
            so.save()
            roll = rng.random()
            if leave_open and roll < 0.25:
                self.return_to_available(so)  # stays DRAFT, nothing reserved
                continue
            try:
                sales.confirm_so(so, user=self.user, at=self.at(day))
            except InsufficientStockError:
                self.return_to_available(so)
                sales.cancel_so(so)
                continue
            if roll > 0.98:
                sales.cancel_so(so)
                self.return_to_available(so)
            elif not (leave_open and roll < 0.6):
                sales.ship_so(so, user=self.user, at=self.at(day, 18))

    def return_to_available(self, so):
        for line in so.lines.all():
            self.available[(line.product_id, so.warehouse_id)] += line.quantity

    def replenish(self, day, wh, approve):
        """Simple (s, S) buying policy: order up to ~3 weeks of demand when at/below ROP."""
        rng = self.rng
        scale = self.wh_scale[wh.pk]
        by_supplier = defaultdict(list)
        for p in self.products:
            key = (p.pk, wh.pk)
            position = self.available[key] + self.on_order[key]
            rop = math.ceil(p.reorder_point * scale)
            if position > rop:
                continue
            target = rop + math.ceil(self.base_demand[p.pk] * scale * 21)
            qty = max(10, math.ceil((target - position) / 10) * 10)
            by_supplier[p.default_supplier].append((p, qty))

        for supplier, items in by_supplier.items():
            expected = day + timedelta(days=supplier.lead_time_days)
            po = PurchaseOrder(supplier=supplier, warehouse=wh, order_date=day, expected_date=expected,
                               created_by=self.user)
            po.lines = [PurchaseOrderLine(product=p, quantity=q, unit_price=p.cost_price) for p, q in items]
            po.save()
            for p, q in items:
                self.on_order[(p.pk, wh.pk)] += q
            if not approve:
                continue  # today's POs stay as drafts waiting for approval
            purchasing.approve_po(po, user=self.user, at=self.at(day))
            delay = rng.choice([-1, 0, 0, 0, 0, 1, 2, 4]) + (3 - supplier.rating)
            self.arrivals.append((expected + timedelta(days=max(-2, delay)), po, rng.random() < 0.1))

    # ---------------------------------------------------------------- summary

    def print_summary(self):
        self.stdout.write(self.style.SUCCESS("Hoàn tất tạo dữ liệu mẫu:"))
        for label, model in (("Sản phẩm", Product), ("Khách hàng", Customer), ("Đơn bán", SalesOrder),
                             ("Đơn mua", PurchaseOrder), ("Phiếu xuất nhập", StockMovement)):
            self.stdout.write(f"  {label}: {model.objects.count()}")
        for status, label in SalesOrder.Status.choices:
            self.stdout.write(f"    SO {label}: {SalesOrder.objects.filter(status=status).count()}")
        for status, label in PurchaseOrder.Status.choices:
            self.stdout.write(f"    PO {label}: {PurchaseOrder.objects.filter(status=status).count()}")
