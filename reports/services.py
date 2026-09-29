import logging
import re
from datetime import timedelta

from django.utils import timezone
from django.utils.html import escape
from django.utils.text import slugify
from wagtail.models import Page, PageViewRestriction, Site
from wagtail.rich_text import RichText

from ai.client import AIError, AIUnavailable

from .kpis import compute_kpis
from .models import AIReportPage, ReportIndexPage

logger = logging.getLogger(__name__)


def previous_week(today=None):
    """Monday..Sunday of the last full week."""
    today = today or timezone.localdate()
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def get_report_index():
    """Return the report index page, creating it (login-only) if needed."""
    index = ReportIndexPage.objects.first()
    if index:
        return index
    site = Site.objects.filter(is_default_site=True).first()
    parent = site.root_page if site else Page.get_first_root_node()
    index = ReportIndexPage(title="Báo cáo", slug="bao-cao",
                            intro="<p>Báo cáo định kỳ do hệ thống tổng hợp và AI diễn giải.</p>")
    parent.add_child(instance=index)
    index.save_revision().publish()
    restrict_to_logged_in(index)
    return index


def restrict_to_logged_in(page):
    """Business reports are private: require login for the index and all report pages below it."""
    PageViewRestriction.objects.get_or_create(page=page, restriction_type=PageViewRestriction.LOGIN)


# ---------------------------------------------------------------- text helpers

def vnd(value):
    value = float(value or 0)
    if abs(value) >= 1e9:
        return f"{value / 1e9:,.2f} tỷ".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.0f} triệu".replace(",", ".")
    return f"{value:,.0f} ₫".replace(",", ".")


def fallback_sections(k):
    """Rule-based narrative used when AI is unavailable."""
    s, inv, pur = k["sales"], k["inventory"], k["purchasing"]
    change = k["revenue_change_pct"]
    change_txt = (f"{'tăng' if change >= 0 else 'giảm'} {abs(change):.1f}% so với kỳ trước".replace(".", ",")
                  if change is not None else "")
    summary = (
        f"Doanh thu kỳ {k['period']['start']} – {k['period']['end']} đạt {vnd(s['revenue'])} "
        f"{change_txt}, lợi nhuận gộp {vnd(s['gross_profit'])}"
        + (f" (biên {s['gross_margin_pct']:.1f}%)".replace(".", ",") if s["gross_margin_pct"] is not None else "")
        + f". Đã giao {s['orders_shipped']} đơn. Giá trị tồn kho hiện tại {vnd(inv['inventory_value'])}, "
        f"{inv['below_reorder_point']} vị trí dưới điểm đặt hàng và {inv['out_of_stock']} vị trí hết hàng."
    )
    highlights = [f"{p['name']}: {vnd(p['revenue'])} ({p['qty']} đơn vị)" for p in k["top_products"][:3]]
    highlights += [f"Kho {w['warehouse']}: doanh thu {vnd(w['revenue'])}" for w in k["revenue_by_warehouse"][:2]]
    risks = []
    if inv["out_of_stock"]:
        risks.append(f"{inv['out_of_stock']} vị trí sản phẩm đang hết hàng.")
    if pur["po_late_rate_pct"]:
        risks.append(f"Tỷ lệ PO giao trễ {pur['po_late_rate_pct']:.1f}%".replace(".", ",")
                     + f" ({pur['po_received_late']}/{pur['po_received']}).")
    if k["slow_movers"]:
        total = sum(r["stock_value"] for r in k["slow_movers"])
        risks.append(f"{len(k['slow_movers'])} mặt hàng không bán trong 60 ngày, giá trị tồn {vnd(total)}.")
    recommendations = []
    if inv["below_reorder_point"] or inv["out_of_stock"]:
        recommendations.append("Duyệt danh sách đề xuất nhập hàng cho các vị trí dưới ROP.")
    if k["slow_movers"]:
        recommendations.append("Xem xét khuyến mãi / chuyển kho cho hàng chậm luân chuyển.")
    if pur["late_suppliers"]:
        recommendations.append(f"Làm việc với NCC giao trễ nhiều nhất: {pur['late_suppliers'][0]['supplier']}.")
    return {
        "summary": summary,
        "highlights": highlights or ["Chưa có dữ liệu bán hàng trong kỳ."],
        "risks": risks or ["Không phát hiện rủi ro lớn."],
        "recommendations": recommendations or ["Duy trì kế hoạch hiện tại."],
    }


# ----------------------------------------------------------- number checking

NUMBER_RE = re.compile(r"(?<![\d-])(\d[\d.,]*)(?![\d-])\s*(tỷ|tỉ|triệu|tr\b|nghìn|ngàn|k\b|%)?", re.IGNORECASE)
SCALE = {"tỷ": 1e9, "tỉ": 1e9, "triệu": 1e6, "tr": 1e6, "nghìn": 1e3, "ngàn": 1e3, "k": 1e3}


def _parse_number(raw):
    raw = raw.rstrip(".,")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):          # 1.234.567
        return float(raw.replace(".", ""))
    if re.fullmatch(r"\d{1,3}(,\d{3})+", raw):           # 1,234,567
        return float(raw.replace(",", ""))
    try:
        return float(raw.replace(",", "."))               # 1,25 or 1.25
    except ValueError:
        return None


def _kpi_numbers(obj, out):
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):  # numbers inside names/dates, e.g. "Hạt điều 500g", "2026-09-21"
        out.extend(float(n) for n in re.findall(r"\d+(?:\.\d+)?", obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _kpi_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _kpi_numbers(v, out)
    return out


def check_numbers(texts, kpis, tolerance=0.02):
    """Return numbers mentioned in AI text that do not match any KPI value."""
    known = _kpi_numbers(kpis, [])
    known_dates = set(re.findall(r"\d+", " ".join([kpis["period"]["start"], kpis["period"]["end"],
                                                    kpis["previous_period"]["start"],
                                                    kpis["previous_period"]["end"]])))
    warnings = []
    for text in texts:
        for raw, unit in NUMBER_RE.findall(text):
            raw = raw.rstrip(".,")
            value = _parse_number(raw)
            if value is None or raw in known_dates:
                continue
            unit = (unit or "").lower()
            if unit == "%":
                ok = any(abs(value - k) <= 0.6 for k in known)
            else:
                value *= SCALE.get(unit, 1)
                if value < 100:     # small counts ("3 sản phẩm", "2 kho") are not checked
                    continue
                ok = any(abs(value - k) <= max(abs(k) * tolerance, 1) for k in known)
            if not ok:
                warnings.append(f"Số '{raw}{(' ' + unit) if unit else ''}' không khớp KPI hệ thống")
    return sorted(set(warnings))


# ------------------------------------------------------------------ generation

def build_body(sections):
    def bullets(items):
        return [str(i) for i in items if str(i).strip()]

    return [
        ("heading", "Tóm tắt điều hành"),
        ("paragraph", RichText(f"<p>{escape(sections['summary'])}</p>")),
        ("kpi_summary", None),
        ("heading", "Điểm nổi bật"),
        ("bullets", bullets(sections["highlights"])),
        ("heading", "Rủi ro"),
        ("bullets", bullets(sections["risks"])),
        ("heading", "Khuyến nghị"),
        ("bullets", bullets(sections["recommendations"])),
    ]


def _unique_slug(base):
    slug, n = base, 2
    while AIReportPage.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def generate_report(start, end, user=None, use_ai=True, submit_for_review=True):
    """Create an unpublished AIReportPage and (optionally) submit it to the page's workflow."""
    kpis = compute_kpis(start, end)
    sections, ai_used, warnings = None, False, []
    if use_ai:
        from ai.services import write_report
        try:
            sections = write_report(kpis, user=user)
            ai_used = True
            texts = [sections["summary"], *sections["highlights"], *sections["risks"],
                     *sections["recommendations"]]
            warnings = check_numbers(texts, kpis)
        except (AIUnavailable, AIError) as exc:
            logger.info("Report AI skipped: %s", exc)
            warnings = [f"Không dùng được AI, báo cáo được tạo tự động theo mẫu: {exc}"]
    if sections is None:
        sections = fallback_sections(kpis)

    index = get_report_index()
    page = AIReportPage(
        title=f"Báo cáo {start:%d/%m} – {end:%d/%m/%Y}",
        slug=_unique_slug(slugify(f"bao-cao-{start:%Y%m%d}-{end:%Y%m%d}")),
        period_start=start, period_end=end, kpi_json=kpis, body=build_body(sections),
        generated_by_ai=ai_used, ai_warnings=warnings, live=False,
    )
    index.add_child(instance=page)
    page.save_revision(user=user, log_action=True)
    if submit_for_review:
        workflow = page.get_workflow()
        if workflow is not None and user is not None:
            workflow.start(page, user)
    return page
