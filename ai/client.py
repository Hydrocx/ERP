"""Thin wrapper around the OpenAI Chat Completions API.

- Every call is logged to AICallLog (tokens, latency, errors).
- Raises AIUnavailable when AI is switched off or no API key is configured,
  and AIError when the call fails or returns unusable output. Callers are
  expected to catch both and fall back to a rule-based result.
"""

import json
import re
import time

from django.conf import settings

from .models import AICallLog, AISettings

EXCERPT_CHARS = 4000
DATA_URL_RE = re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+")


class AIUnavailable(Exception):
    pass


class AIError(Exception):
    pass


def _make_openai():
    from openai import OpenAI

    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=60, max_retries=2)


def get_settings():
    return AISettings.load()


def is_enabled(feature_flag=None):
    cfg = get_settings()
    if not (settings.OPENAI_API_KEY and cfg.enabled):
        return False
    return getattr(cfg, feature_flag) if feature_flag else True


def chat(feature, messages, **kwargs):
    """Call the model and return the first choice's message object."""
    return _call(feature, messages, **kwargs)[0]


def _call(feature, messages, *, response_schema=None, schema_name="result", tools=None, user=None,
          feature_flag=None):
    if not is_enabled(feature_flag):
        raise AIUnavailable("AI chưa được cấu hình (thiếu OPENAI_API_KEY) hoặc đang tắt.")
    cfg = get_settings()
    kwargs = {"model": cfg.model_name, "messages": messages}
    if cfg.temperature is not None:
        kwargs["temperature"] = float(cfg.temperature)
    if response_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": response_schema},
        }
    if tools:
        kwargs["tools"] = tools

    log = AICallLog(feature=feature, model=cfg.model_name, user=user if getattr(user, "pk", None) else None,
                    request_excerpt=_excerpt(messages[-1]))
    started = time.monotonic()
    try:
        response = _make_openai().chat.completions.create(**kwargs)
        message = response.choices[0].message
    except Exception as exc:  # network, auth, rate limit, bad request...
        log.success = False
        log.error = f"{type(exc).__name__}: {exc}"[:2000]
        raise AIError(log.error) from exc
    else:
        usage = getattr(response, "usage", None)
        log.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        log.output_tokens = getattr(usage, "completion_tokens", 0) or 0
        log.response_excerpt = (message.content or _describe_tool_calls(message))[:EXCERPT_CHARS]
        return message, log
    finally:
        log.latency_ms = int((time.monotonic() - started) * 1000)
        log.save()


def _excerpt(message):
    """Request excerpt for the log, without embedded images (base64 data URLs)."""
    text = json.dumps(message, ensure_ascii=False, default=str)
    return DATA_URL_RE.sub("data:<đã lược bỏ ảnh>", text)[:EXCERPT_CHARS]


def chat_json(feature, system_prompt, payload, schema, *, schema_name="result", user=None,
              feature_flag=None):
    """Send payload as JSON and parse a JSON response that follows schema."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    return chat_json_messages(feature, messages, schema, schema_name=schema_name, user=user,
                              feature_flag=feature_flag)


def chat_json_messages(feature, messages, schema, *, schema_name="result", user=None, feature_flag=None):
    """Like chat_json but with caller-built messages (e.g. text + image parts)."""
    message, log = _call(feature, messages, response_schema=schema, schema_name=schema_name, user=user,
                         feature_flag=feature_flag)
    error = None
    if getattr(message, "refusal", None):
        error = f"Model từ chối: {message.refusal}"
    else:
        try:
            return json.loads(message.content or "")
        except (TypeError, json.JSONDecodeError):
            error = "Phản hồi của AI không phải JSON hợp lệ"
    AICallLog.objects.filter(pk=log.pk).update(success=False, error=error)
    raise AIError(error)


def _describe_tool_calls(message):
    calls = getattr(message, "tool_calls", None) or []
    return "; ".join(f"tool:{c.function.name}({c.function.arguments})" for c in calls)
