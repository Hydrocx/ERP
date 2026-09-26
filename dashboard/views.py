from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ai.client import AIError, AIUnavailable
from catalog.models import Category, Product, Warehouse
from forecast import services as forecast_services
from forecast.models import ReorderSuggestion
from inventory.models import StockLevel, StockMovement
from purchasing.models import PurchaseOrder
from reports.kpis import inventory_snapshot, monthly_revenue, revenue_by, sales_summary, top_products
from reports.models import AIReportPage
from reports.services import generate_report, previous_week
from sales.models import SalesOrder

ASSISTANT_SESSION_KEY = "assistant_history"


def _pct_change(current, previous):
    return round((current - previous) / previous * 100, 1) if previous else None


@login_required
def home(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_same_day = min(prev_start + (today - month_start), month_start - timedelta(days=1))
    current = sales_summary(month_start, today)
    previous = sales_summary(prev_start, prev_same_day)
    last30 = today - timedelta(days=29)
    inventory = inventory_snapshot()
    context = {
        "today": today,
        "sales": current,
        "revenue_change": _pct_change(current["revenue"], previous["revenue"]),
        "inventory": inventory,
        "pending_suggestions": ReorderSuggestion.objects.filter(status=ReorderSuggestion.Status.PENDING).count(),
        "po_open": PurchaseOrder.objects.filter(status__in=PurchaseOrder.OPEN_STATUSES).count(),
        "so_open": SalesOrder.objects.filter(status__in=[SalesOrder.Status.DRAFT,
                                                         SalesOrder.Status.CONFIRMED]).count(),
        "top": top_products(last30, today, 10),
        "latest_report": AIReportPage.objects.live().order_by("-period_end").first(),
        "chart_data": {
            "monthly": monthly_revenue(12, today),
            "categories": revenue_by(last30, today, "product__category__name", "category"),
            "warehouses": [{"warehouse": k, "value": v}
                           for k, v in inventory["inventory_value_by_warehouse"].items()],
        },
    }
    return render(request, "dashboard/home.html", context)


@login_required
def inventory(request):
    levels = StockLevel.objects.select_related("product__category", "warehouse").filter(product__is_active=True)
    wh, cat, status, q = (request.GET.get(k, "") for k in ("warehouse", "category", "status", "q"))
    if wh:
        levels = levels.filter(warehouse__code=wh)
    if cat:
        levels = levels.filter(product__category_id=cat)
    if q:
        levels = levels.filter(product__name__icontains=q) | levels.filter(product__sku__icontains=q)
    rows = list(levels.order_by("warehouse__code", "product__sku"))
    if status:
        rows = [lv for lv in rows if lv.status_code == status]
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "dashboard/inventory.html", {
        "page": page, "warehouses": Warehouse.objects.filter(is_active=True),
        "categories": Category.objects.all(), "filters": {"warehouse": wh, "category": cat, "status": status, "q": q},
        "querystring": query.urlencode(), "total_value": sum(float(lv.stock_value) for lv in rows),
    })


def _weekly_series(product, weeks=52):
    today = timezone.localdate()
    start = today - timedelta(days=7 * weeks)
    sales = forecast_services.daily_sales(start, today, product=product)
    totals = [0] * weeks
    for daily in sales.values():
        for day, qty in daily.items():
            totals[(day - start).days // 7] += qty
    labels = [(start + timedelta(days=7 * i)).strftime("%d/%m") for i in range(weeks)]
    return labels, totals


@login_required
def product_detail(request, sku):
    product = get_object_or_404(Product.objects.select_related("category", "default_supplier"), sku=sku)
    labels, actual = _weekly_series(product)
    forecasts = forecast_services.latest_forecasts(product)
    future_week = round(sum(float(f.forecast_daily) for f in forecasts) * 7, 1) if forecasts else None
    today = timezone.localdate()
    future_labels = [(today + timedelta(days=7 * i)).strftime("%d/%m") for i in range(4)]
    return render(request, "dashboard/product_detail.html", {
        "product": product,
        "levels": product.stock_levels.select_related("warehouse").order_by("warehouse__code"),
        "movements": StockMovement.objects.filter(product=product).select_related("warehouse")[:30],
        "forecasts": forecasts,
        "chart_data": {
            "labels": labels + future_labels,
            "actual": actual + [None] * 4,
            "forecast": [None] * (len(actual) - 1) + [actual[-1]] + [future_week] * 4 if forecasts else [],
        },
        "sales_90d": product.sales_lines.filter(
            order__status=SalesOrder.Status.SHIPPED, order__order_date__gte=today - timedelta(days=90)
        ).aggregate(q=Sum("quantity"))["q"] or 0,
    })


@login_required
@require_POST
def product_ai(request, sku):
    product = get_object_or_404(Product, sku=sku)
    forecasts = forecast_services.latest_forecasts(product) or forecast_services.compute_forecasts(
        products=[product])
    from ai.services import analyze_product
    context = {"product": product}
    try:
        context["analysis"] = analyze_product(product, forecasts, user=request.user)
    except (AIUnavailable, AIError) as exc:
        context["error"] = str(exc)
        context["fallback"] = [
            f"Kho {f.warehouse.code}: dự báo {float(f.forecast_daily):.1f}/ngày, khả dụng {f.available}, "
            f"ROP {f.reorder_point}" + (f", đủ bán khoảng {f.days_of_cover} ngày" if f.days_of_cover else "")
            for f in forecasts
        ]
    return render(request, "dashboard/partials/product_ai.html", context)


@login_required
@permission_required("forecast.view_reordersuggestion", raise_exception=True)
def reorder(request):
    pending = ReorderSuggestion.objects.filter(status=ReorderSuggestion.Status.PENDING).select_related(
        "product", "warehouse", "supplier", "forecast")
    wh = request.GET.get("warehouse", "")
    if wh:
        pending = pending.filter(warehouse__code=wh)

    if request.method == "POST":
        if not request.user.has_perm("forecast.change_reordersuggestion"):
            messages.error(request, "Bạn không có quyền duyệt đề xuất.")
            return redirect("dashboard:reorder")
        ids = [int(i) for i in request.POST.getlist("selected") if i.isdigit()]
        selected = list(pending.filter(pk__in=ids))
        if not selected:
            messages.warning(request, "Chưa chọn đề xuất nào.")
        elif request.POST.get("action") == "reject":
            n = forecast_services.reject_suggestions(selected, user=request.user)
            messages.info(request, f"Đã từ chối {n} đề xuất.")
        else:
            decisions = []
            for s in selected:
                try:
                    qty = int(request.POST.get(f"qty_{s.pk}", s.recommended_qty))
                except ValueError:
                    qty = s.recommended_qty
                decisions.append((s, qty))
            orders = forecast_services.approve_suggestions(decisions, user=request.user)
            messages.success(request, f"Đã tạo {len(orders)} PO nháp: " + ", ".join(po.code for po in orders)
                             + ". Quản lý cần duyệt PO trong trang quản trị.")
        return redirect(request.get_full_path())

    last_run = ReorderSuggestion.objects.order_by("-created_at").values_list("created_at", flat=True).first()
    return render(request, "dashboard/reorder.html", {
        "suggestions": pending.order_by("warehouse__code", "supplier__code", "product__sku"),
        "warehouses": Warehouse.objects.filter(is_active=True), "warehouse": wh, "last_run": last_run,
        "can_approve": request.user.has_perm("forecast.change_reordersuggestion"),
        "can_run": request.user.has_perm("ai.run_forecast"),
        "po_url": reverse(PurchaseOrder.snippet_viewset.get_url_name("list")) + "?source=AI&status=DRAFT",
    })


@login_required
@permission_required("ai.run_forecast", raise_exception=True)
@require_POST
def reorder_run(request):
    result = forecast_services.run_forecast(user=request.user, use_ai=request.POST.get("use_ai") != "0")
    messages.success(request, f"Đã chạy dự báo: {result['suggestions']} đề xuất, AI giải thích "
                              f"{result['ai_explained']}.")
    if result["ai_error"]:
        messages.warning(request, f"AI không chạy, đang dùng lý do theo công thức: {result['ai_error']}")
    return redirect("dashboard:reorder")


@login_required
def reports(request):
    can_generate = request.user.has_perm("ai.generate_report")
    if request.method == "POST":
        if not can_generate:
            messages.error(request, "Bạn không có quyền tạo báo cáo.")
            return redirect("dashboard:reports")
        try:
            start = date.fromisoformat(request.POST["start"])
            end = date.fromisoformat(request.POST["end"])
        except (KeyError, ValueError):
            messages.error(request, "Ngày không hợp lệ.")
            return redirect("dashboard:reports")
        if end < start:
            messages.error(request, "Ngày kết thúc phải sau ngày bắt đầu.")
            return redirect("dashboard:reports")
        page = generate_report(start, end, user=request.user)
        messages.success(request, f"Đã tạo '{page.title}' ({'AI viết' if page.generated_by_ai else 'mẫu tự động'})"
                                  ". Báo cáo đang chờ duyệt trước khi xuất bản.")
        return redirect("dashboard:reports")

    default_start, default_end = previous_week()
    drafts = AIReportPage.objects.none()
    if can_generate:
        drafts = AIReportPage.objects.filter(live=False).order_by("-latest_revision_created_at")[:10]
    return render(request, "dashboard/reports.html", {
        "published": AIReportPage.objects.live().order_by("-period_end")[:20],
        "drafts": drafts, "can_generate": can_generate,
        "default_start": default_start, "default_end": default_end,
    })


@login_required
@permission_required("ai.use_assistant", raise_exception=True)
def assistant(request):
    history = request.session.get(ASSISTANT_SESSION_KEY, [])
    if request.method != "POST":
        return render(request, "dashboard/assistant.html", {"history": history})

    from ai.assistant import MAX_HISTORY, ask
    question = request.POST.get("question", "").strip()[:1000]
    context = {"question": question}
    if question:
        try:
            answer, tools = ask(question, history, user=request.user)
        except (AIUnavailable, AIError) as exc:
            context["error"] = str(exc)
        else:
            history = (history + [{"role": "user", "content": question},
                                  {"role": "assistant", "content": answer}])[-MAX_HISTORY:]
            request.session[ASSISTANT_SESSION_KEY] = history
            context.update(answer=answer, tools=tools)
    return render(request, "dashboard/partials/chat_turn.html", context)


@login_required
@require_POST
def assistant_reset(request):
    request.session.pop(ASSISTANT_SESSION_KEY, None)
    return redirect("dashboard:assistant")


INVOICE_SESSION_KEY = "invoice_preview"


@login_required
@permission_required("purchasing.add_purchaseorder", raise_exception=True)
def invoice(request):
    """F4: upload a supplier invoice image -> AI reads it -> user confirms -> draft PO."""
    from ai.services import INVOICE_MAX_BYTES, extract_invoice
    from catalog.models import Supplier
    from purchasing.invoice import build_preview, create_po_from_invoice

    if request.method == "POST" and request.POST.get("action") == "extract":
        upload = request.FILES.get("image")
        if not upload:
            messages.error(request, "Chưa chọn ảnh hóa đơn.")
        elif upload.size > INVOICE_MAX_BYTES:
            messages.error(request, "Ảnh vượt quá 5 MB.")
        else:
            try:
                data = extract_invoice(upload.read(), upload.content_type, user=request.user)
            except (AIUnavailable, AIError) as exc:
                messages.error(request, f"Không đọc được hóa đơn: {exc}")
            else:
                request.session[INVOICE_SESSION_KEY] = build_preview(data)
        return redirect("dashboard:invoice")

    preview = request.session.get(INVOICE_SESSION_KEY)
    if request.method == "POST" and request.POST.get("action") == "create" and preview:
        from core.exceptions import ERPError
        supplier = Supplier.objects.filter(pk=request.POST.get("supplier") or 0).first()
        warehouse = Warehouse.objects.filter(pk=request.POST.get("warehouse") or 0).first()
        products = {p.pk: p for p in Product.objects.filter(is_active=True)}
        lines = []
        for i in range(len(preview["lines"])):
            try:
                product = products.get(int(request.POST.get(f"product_{i}") or 0))
                qty = int(request.POST.get(f"qty_{i}") or 0)
                price = Decimal(request.POST.get(f"price_{i}") or "0")
            except (ValueError, ArithmeticError):
                continue
            lines.append((product, qty, price))
        if supplier is None or warehouse is None:
            messages.error(request, "Cần chọn nhà cung cấp và kho nhận.")
        else:
            try:
                po = create_po_from_invoice(supplier, warehouse, lines, preview.get("invoice_number") or "",
                                            preview.get("invoice_date"), user=request.user)
            except ERPError as exc:
                messages.error(request, str(exc))
            else:
                request.session.pop(INVOICE_SESSION_KEY, None)
                messages.success(request, f"Đã tạo PO nháp {po.code} từ hóa đơn. Quản lý duyệt rồi thủ kho nhận hàng.")
                return redirect(reverse(PurchaseOrder.snippet_viewset.get_url_name("inspect"), args=[po.pk]))
        return redirect("dashboard:invoice")

    if request.method == "POST" and request.POST.get("action") == "discard":
        request.session.pop(INVOICE_SESSION_KEY, None)
        return redirect("dashboard:invoice")

    return render(request, "dashboard/invoice.html", {
        "preview": preview,
        "suppliers": Supplier.objects.filter(is_active=True),
        "warehouses": Warehouse.objects.filter(is_active=True),
        "products": Product.objects.filter(is_active=True).order_by("name"),
    })
