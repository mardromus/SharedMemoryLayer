"""LLM provider adapters: request formats, response parsing and graceful fallback.

Hosted providers cannot be called without API keys in CI, so their HTTP contract is
verified against an in-process transport. The Ollama provider is additionally
exercised live when an Ollama server is configured (see docs/setup.md).
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import get_settings
from app.llm import providers
from app.llm.providers import (
    AnthropicProvider,
    ContextMemory,
    ExtractiveProvider,
    LLMError,
    OllamaProvider,
    OpenAICompatibleProvider,
    complete_with_fallback,
)

MEMORIES = [
    ContextMemory(ref="M1", title="JWT refresh expiry", content="Refresh tokens expired and the API returned 500. Return 401 instead.",
                  team="Development", memory_type="learning", similarity=0.82, confidence=0.9, verified=True),
]


def _mock(provider, handler, base_url: str) -> None:
    provider._client = httpx.Client(base_url=base_url, transport=httpx.MockTransport(handler), headers=provider._client.headers)


def test_ollama_request_format():
    settings = get_settings().model_copy(update={"llm_provider": "ollama", "llm_model": "qwen2.5:0.5b", "ollama_num_thread": 4})
    provider = OllamaProvider(settings)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": " Use 401. \nLEARNING: expired tokens -> 401"}, "prompt_eval_count": 50, "eval_count": 9})

    _mock(provider, handler, "http://ollama")
    result = provider.complete("system prompt", "What status for expired tokens?", MEMORIES)
    assert seen["path"] == "/api/chat"
    assert seen["body"]["model"] == "qwen2.5:0.5b" and seen["body"]["stream"] is False
    assert seen["body"]["options"]["num_thread"] == 4
    assert seen["body"]["messages"][0] == {"role": "system", "content": "system prompt"}
    assert "[M1]" in seen["body"]["messages"][1]["content"] and "Development team" in seen["body"]["messages"][1]["content"]
    assert result.provider == "ollama" and result.text.startswith("Use 401") and result.usage["completion_tokens"] == 9


def test_openai_compatible_request_format():
    settings = get_settings().model_copy(update={"llm_provider": "openai", "llm_model": "gpt-4o-mini", "openai_api_key": "test-key"})
    provider = OpenAICompatibleProvider(settings)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["auth"], seen["body"] = request.url.path, request.headers["Authorization"], json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Return 401."}}], "usage": {"total_tokens": 42}})

    _mock(provider, handler, "https://api.openai.test/v1")
    result = provider.complete("sys", "task", MEMORIES)
    assert seen["path"] == "/v1/chat/completions" and seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "gpt-4o-mini" and seen["body"]["messages"][0]["role"] == "system"
    assert result.text == "Return 401." and result.usage == {"total_tokens": 42}


def test_anthropic_request_format():
    settings = get_settings().model_copy(update={"llm_provider": "anthropic", "llm_model": "claude-haiku-4-5-20251001", "anthropic_api_key": "test-key"})
    provider = AnthropicProvider(settings)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["headers"], seen["body"] = request.url.path, request.headers, json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Return 401"}, {"type": "text", "text": " with WWW-Authenticate."}],
                                         "usage": {"input_tokens": 80, "output_tokens": 9}})

    _mock(provider, handler, "https://api.anthropic.test")
    result = provider.complete("sys", "task", MEMORIES)
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == "test-key" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "sys" and seen["body"]["messages"][0]["role"] == "user"
    assert seen["body"]["max_tokens"] == settings.llm_max_tokens
    assert result.text == "Return 401 with WWW-Authenticate."


def test_hosted_providers_require_keys():
    with pytest.raises(LLMError):
        OpenAICompatibleProvider(get_settings().model_copy(update={"openai_api_key": None}))
    with pytest.raises(LLMError):
        AnthropicProvider(get_settings().model_copy(update={"anthropic_api_key": None}))


def test_extractive_provider_is_grounded_in_memories():
    result = ExtractiveProvider().complete("sys", "what status code for expired refresh tokens", MEMORIES)
    # every sentence it outputs is copied from a retrieved memory (here the best-overlapping one)
    assert "[M1]" in result.text and "Refresh tokens expired and the API returned 500." in result.text and "LEARNING:" in result.text
    empty = ExtractiveProvider().complete("sys", "anything", [])
    assert "solved from scratch" in empty.text


def test_failing_provider_falls_back_to_extractive(monkeypatch):
    class Broken:
        name, model = "ollama", "x"

        def complete(self, *args):
            raise LLMError("connection refused")

    monkeypatch.setattr(providers, "_provider", Broken())
    result = complete_with_fallback("sys", "task", MEMORIES)
    assert result.provider == "extractive" and "connection refused" in result.fallback_reason
