from django.conf import settings


def app_context(request):
    """AI status shown in the app header (only for logged-in users of the /app/ pages)."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from ai.client import get_settings

    cfg = get_settings()
    provider = {"gemini": "Gemini", "openai": "OpenAI"}.get(settings.AI_PROVIDER, settings.AI_PROVIDER)
    return {"ai_status": {
        "enabled": bool(settings.AI_API_KEY and cfg.enabled),
        "provider": provider,
        "model": cfg.model_name,
    }}
