import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def vnd(value):
    """1234567 -> 1.234.567 ₫"""
    try:
        return f"{float(value):,.0f} ₫".replace(",", ".")
    except (TypeError, ValueError):
        return "–"


@register.filter
def vnd_short(value):
    """1234567890 -> 1,23 tỷ ; 830000000 -> 830 tr"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "–"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.2f} tỷ".replace(".", ",")
    if abs(value) >= 1e6:
        return f"{value / 1e6:.0f} tr"
    return f"{value:,.0f} ₫".replace(",", ".")


@register.filter
def pct(value):
    if value is None:
        return "–"
    return f"{float(value):+.1f}%".replace(".", ",")


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if hasattr(mapping, "get") else None


def _inline(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", text)


@register.filter
def simple_markdown(text):
    """Tiny, safe markdown subset for assistant answers: bold, code, bullets, numbered lists, tables."""
    lines = escape(text or "").splitlines()
    html, in_list, table = [], None, []

    def close_list():
        nonlocal in_list
        if in_list:
            html.append(f"</{in_list}>")
            in_list = None

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not re.fullmatch(r"\|?[\s:\-|]+\|?", r)]
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        out = ["<table class='md-table'>"]
        for i, row in enumerate(cells):
            tag = "th" if i == 0 else "td"
            out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in row) + "</tr>")
        out.append("</table>")
        html.append("".join(out))
        table.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            close_list()
            table.append(stripped)
            continue
        flush_table()
        bullet = re.match(r"^[-*•]\s+(.*)", stripped)
        number = re.match(r"^\d+[.)]\s+(.*)", stripped)
        if bullet or number:
            kind = "ul" if bullet else "ol"
            if in_list != kind:
                close_list()
                html.append(f"<{kind}>")
                in_list = kind
            html.append(f"<li>{_inline((bullet or number).group(1))}</li>")
        elif stripped:
            close_list()
            heading = re.match(r"^#{1,4}\s+(.*)", stripped)
            html.append(f"<p><strong>{_inline(heading.group(1))}</strong></p>" if heading
                        else f"<p>{_inline(stripped)}</p>")
        else:
            close_list()
    close_list()
    flush_table()
    return mark_safe("".join(html))


@register.simple_tag
def icon(name, size=None, cls=""):
    """<svg> referencing the sprite in dashboard/_icons.html."""
    style = f' style="width:{size}px;height:{size}px"' if size else ""
    return mark_safe(f'<svg class="icon {cls}"{style} aria-hidden="true"><use href="#i-{escape(name)}"></use></svg>')
