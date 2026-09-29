import json
from types import SimpleNamespace

import pytest


class FakeOpenAI:
    """Stands in for openai.OpenAI; returns queued responses and records requests."""

    def __init__(self):
        self.responses = []
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def queue_json(self, payload):
        self.responses.append(_message(content=json.dumps(payload, ensure_ascii=False)))

    def queue_text(self, text):
        self.responses.append(_message(content=text))

    def queue_tool_call(self, name, arguments, call_id="call_1"):
        call = SimpleNamespace(id=call_id, type="function",
                               function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
        self.responses.append(_message(content=None, tool_calls=[call]))

    def queue_error(self, exc):
        self.responses.append(exc)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _message(content, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls, refusal=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))


@pytest.fixture
def fake_openai(monkeypatch, settings):
    settings.AI_API_KEY = "test-key"
    fake = FakeOpenAI()
    monkeypatch.setattr("ai.client._make_openai", lambda: fake)
    return fake


@pytest.fixture
def no_openai(settings):
    settings.AI_PROVIDER = "openai"
    settings.AI_API_KEY = ""
