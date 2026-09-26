from django.utils import timezone


def next_document_code(model, prefix, date=None, field="code"):
    """Return the next code like PO-202609-0001 for the given model."""
    date = date or timezone.localdate()
    base = f"{prefix}-{date:%Y%m}-"
    last = (
        model.objects.filter(**{f"{field}__startswith": base})
        .order_by(f"-{field}")
        .values_list(field, flat=True)
        .first()
    )
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{base}{seq:04d}"
