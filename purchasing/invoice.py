"""Match data read from a supplier invoice (F4) to master data and turn it into a draft PO.

The AI only reads the image; matching and PO creation are deterministic and the user
confirms every line before anything is saved.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from django.db import transaction
from django.utils import timezone

from catalog.models import Product, Supplier
from core.exceptions import ERPError

from .models import PurchaseOrder, PurchaseOrderLine

NAME_MATCH_THRESHOLD = 0.55


def _vnd(value):
    return f"{value:,.0f}".replace(",", ".")


def _normalize(text):
    return " ".join((text or "").lower().split())


def _similarity(a, b):
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def match_supplier(name, tax_code):
    suppliers = list(Supplier.objects.filter(is_active=True))
    if tax_code:
        digits = "".join(ch for ch in tax_code if ch.isdigit())
        for s in suppliers:
            if digits and "".join(ch for ch in s.tax_code if ch.isdigit()) == digits:
                return s, 1.0
    if not name:
        return None, 0.0
    best = max(suppliers, key=lambda s: _similarity(name, s.name), default=None)
    score = _similarity(name, best.name) if best else 0.0
    return (best, score) if score >= NAME_MATCH_THRESHOLD else (None, score)


def match_product(sku, name, products):
    if sku:
        for p in products:
            if p.sku.lower() == sku.strip().lower():
                return p, 1.0
    best = max(products, key=lambda p: _similarity(name, p.name), default=None)
    score = _similarity(name, best.name) if best else 0.0
    return (best, score) if score >= NAME_MATCH_THRESHOLD else (None, score)


def _to_int(value):
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def _to_decimal(value):
    try:
        return Decimal(str(value)).quantize(Decimal("1"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def build_preview(data):
    """JSON-serializable preview (stored in the session) with matches and warnings."""
    products = list(Product.objects.filter(is_active=True))
    supplier, supplier_score = match_supplier(data.get("supplier_name"), data.get("supplier_tax_code"))
    lines, warnings = [], []
    for i, raw in enumerate(data.get("lines") or []):
        product, score = match_product(raw.get("sku"), raw.get("name", ""), products)
        qty = _to_int(raw.get("quantity"))
        price = _to_decimal(raw.get("unit_price"))
        if product is None:
            warnings.append(f"Dòng {i + 1} '{raw.get('name')}': không tìm thấy sản phẩm tương ứng, cần chọn tay.")
        if not qty:
            warnings.append(f"Dòng {i + 1}: không đọc được số lượng.")
        if product is not None and price is not None and product.cost_price and \
                abs(price - product.cost_price) > product.cost_price * Decimal("0.2"):
            warnings.append(f"Dòng {i + 1}: đơn giá {_vnd(price)} lệch >20% so với giá nhập {_vnd(product.cost_price)}.")
        lines.append({
            "name": raw.get("name", ""), "sku": raw.get("sku"),
            "product_id": product.pk if product else None, "match_score": round(score, 2),
            "quantity": qty, "unit_price": str(price if price is not None else (product.cost_price if product else 0)),
        })
    if supplier is None:
        warnings.append(f"Không khớp được nhà cung cấp '{data.get('supplier_name')}', cần chọn tay.")
    computed = sum(line["quantity"] * Decimal(line["unit_price"]) for line in lines)
    total = _to_decimal(data.get("total_amount"))
    if total and computed and abs(total - computed) > total * Decimal("0.01"):
        warnings.append(f"Tổng tiền trên hóa đơn {_vnd(total)} khác tổng các dòng {_vnd(computed)} "
                        "(có thể do VAT hoặc đọc sai).")
    return {
        "supplier_id": supplier.pk if supplier else None, "supplier_score": round(supplier_score, 2),
        "supplier_name": data.get("supplier_name"), "invoice_number": data.get("invoice_number"),
        "invoice_date": data.get("invoice_date"), "total_amount": str(total) if total else None,
        "note": data.get("note"), "lines": lines, "warnings": warnings,
    }


@transaction.atomic
def create_po_from_invoice(supplier, warehouse, lines, invoice_number="", invoice_date=None, user=None):
    """lines: [(product, qty, unit_price)] confirmed by the user. Returns a DRAFT PO."""
    lines = [(p, q, price) for p, q, price in lines if p is not None and q > 0]
    if not lines:
        raise ERPError("Không có dòng hàng hợp lệ để tạo PO.")
    try:
        order_date = date.fromisoformat(invoice_date) if invoice_date else timezone.localdate()
    except ValueError:
        order_date = timezone.localdate()
    po = PurchaseOrder(
        supplier=supplier, warehouse=warehouse, order_date=order_date,
        expected_date=order_date + timedelta(days=supplier.lead_time_days),
        source=PurchaseOrder.Source.INVOICE_OCR, created_by=user,
        note=f"Tạo từ hóa đơn NCC {invoice_number or '(không rõ số)'} (đọc bằng AI, đã được người dùng xác nhận).",
    )
    po.lines = [PurchaseOrderLine(product=p, quantity=q, unit_price=price) for p, q, price in lines]
    po.save()
    return po
