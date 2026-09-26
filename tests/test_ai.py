import pytest

from ai import client
from ai.assistant import ask, run_tool
from ai.models import AICallLog, AISettings
from ai.services import apply_guardrail, explain_reorder
from forecast.models import ReorderSuggestion
from inventory import services as stock

from .factories import ProductFactory, WarehouseFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def suggestions():
    wh = WarehouseFactory(code="HCM")
    rows = []
    for qty in (100, 50, 20):
        p = ProductFactory()
        rows.append(ReorderSuggestion.objects.create(product=p, warehouse=wh, supplier=p.default_supplier,
                                                     available=5, on_order=0, reorder_point=30, suggested_qty=qty))
    return rows


def test_unavailable_without_key(no_openai):
    assert not client.is_enabled()
    with pytest.raises(client.AIUnavailable):
        client.chat("report", [{"role": "user", "content": "hi"}])
    assert not AICallLog.objects.exists()


def test_disabled_in_settings(fake_openai):
    cfg = AISettings.load()
    cfg.enable_report = False
    cfg.save()
    with pytest.raises(client.AIUnavailable):
        client.chat_json("report", "sys", {}, {"type": "object"}, feature_flag="enable_report")


def test_chat_json_sends_schema_and_logs(fake_openai):
    fake_openai.queue_json({"ok": True})
    result = client.chat_json("report", "system prompt", {"x": 1}, {"type": "object"}, schema_name="s")
    assert result == {"ok": True}
    req = fake_openai.requests[0]
    assert req["response_format"]["type"] == "json_schema"
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["messages"][0]["content"] == "system prompt"
    log = AICallLog.objects.get()
    assert log.success and (log.input_tokens, log.output_tokens) == (100, 50)


def test_invalid_json_and_api_errors_are_logged(fake_openai):
    fake_openai.queue_text("not json")
    with pytest.raises(client.AIError):
        client.chat_json("report", "s", {}, {"type": "object"})
    fake_openai.queue_error(RuntimeError("rate limited"))
    with pytest.raises(client.AIError):
        client.chat_json("report", "s", {}, {"type": "object"})
    logs = list(AICallLog.objects.order_by("id"))
    assert [log.success for log in logs] == [False, False]
    assert "rate limited" in logs[1].error


def test_guardrail_accepts_small_adjustments_and_flags_large_ones(suggestions):
    s = suggestions[0]  # suggested 100, band 30%
    apply_guardrail(s, {"action": "order", "adjusted_qty": 125, "confidence": "high", "reason": "Tăng trưởng"}, 30)
    assert (s.ai_flagged, s.recommended_qty) == (False, 125)
    apply_guardrail(s, {"action": "order", "adjusted_qty": 200, "confidence": "high", "reason": "x"}, 30)
    assert s.ai_flagged and s.ai_action == "review" and s.recommended_qty == 100
    assert "Guardrail" in s.ai_reason
    apply_guardrail(s, {"action": "wait", "adjusted_qty": 0, "confidence": "medium", "reason": "Hết mùa"}, 30)
    assert not s.ai_flagged and s.recommended_qty == 100  # human decides; default stays system qty


def test_explain_reorder_updates_matching_items_only(fake_openai, suggestions):
    a, b, c = suggestions
    fake_openai.queue_json({"items": [
        {"id": a.pk, "action": "order", "adjusted_qty": 110, "confidence": "high", "reason": "Nhu cầu tăng"},
        {"id": b.pk, "action": "wait", "adjusted_qty": 0, "confidence": "low", "reason": "Sắp hết mùa"},
        {"id": 999999, "action": "order", "adjusted_qty": 1, "confidence": "low", "reason": "không tồn tại"},
    ]})
    assert explain_reorder(suggestions) == 2
    a.refresh_from_db(), b.refresh_from_db(), c.refresh_from_db()
    assert (a.ai_action, a.ai_adjusted_qty, a.recommended_qty) == ("order", 110, 110)
    assert b.ai_action == "wait"
    assert c.ai_action == ""  # untouched -> keeps rule-based reason
    payload = fake_openai.requests[0]["messages"][1]["content"]
    assert a.product.sku in payload and '"suggested_qty": 100' in payload


# ---------------------------------------------------------------- assistant

def test_tool_get_stock_returns_system_numbers():
    p, wh = ProductFactory(name="Nước khoáng test"), WarehouseFactory(code="HN")
    stock.stock_in(p, wh, 12, 1000)
    stock.reserve(p, wh, 2)
    rows = run_tool("get_stock", '{"product_query": "khoáng"}')
    assert rows == [{"sku": p.sku, "name": p.name, "warehouse": "HN", "on_hand": 12, "reserved": 2,
                     "available": 10, "reorder_point": p.reorder_point, "status": "Sắp hết"}]
    assert "error" in run_tool("drop_table", "{}")
    assert "error" in run_tool("get_stock", "not json")


def test_assistant_runs_tool_then_answers(fake_openai):
    p, wh = ProductFactory(), WarehouseFactory(code="HN")
    stock.stock_in(p, wh, 7, 1000)
    fake_openai.queue_tool_call("get_stock", {"product_query": p.sku})
    fake_openai.queue_text(f"Tồn kho {p.sku} tại HN: 7.")
    answer, tools = ask(f"Còn bao nhiêu {p.sku}?")
    assert answer == f"Tồn kho {p.sku} tại HN: 7." and tools == ["get_stock"]
    second = fake_openai.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "get_stock"
    assert second[-1]["role"] == "tool" and '"available": 7' in second[-1]["content"]
    assert fake_openai.requests[0]["tools"]


def test_assistant_stops_after_too_many_tool_rounds(fake_openai):
    for i in range(5):
        fake_openai.queue_tool_call("low_stock_items", {}, call_id=f"c{i}")
    with pytest.raises(client.AIError):
        ask("?")


def test_ai_selftest_command(fake_openai, suggestions, capsys):
    from django.core.management import call_command

    s = suggestions[0]
    fake_openai.queue_json({"items": [{"id": s.pk, "action": "order", "adjusted_qty": 100, "confidence": "high",
                                       "reason": "OK"}]})
    fake_openai.queue_json({"summary": "Ổn định", "actions": ["Theo dõi"], "risk_level": "low"})
    fake_openai.queue_json({"summary": "Doanh thu tuần trước", "highlights": [], "risks": [], "recommendations": []})
    fake_openai.queue_text("Không có mặt hàng hết hàng.")
    call_command("ai_selftest")
    out = capsys.readouterr().out
    assert out.count("] OK") == 4 and "Tất cả tính năng AI hoạt động" in out
