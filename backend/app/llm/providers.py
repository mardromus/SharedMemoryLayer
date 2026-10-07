"""Configurable LLM providers for the memory-augmented agent runtime.

``LLM_PROVIDER``:
    none       - no LLM: deterministic *extractive* synthesis over the retrieved
                 memories (clearly labelled as such in every response)
    ollama     - local models served by Ollama (``OLLAMA_BASE_URL``)
    openai     - any OpenAI-compatible chat-completions endpoint
    anthropic  - Anthropic Messages API

No API key is ever hard-coded; they are read from the environment.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class ContextMemory:
    ref: str  # "M1"
    title: str
    content: str
    team: str
    memory_type: str
    similarity: float
    confidence: float
    verified: bool


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str | None
    fallback_reason: str | None = None
    usage: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


SYSTEM_TEMPLATE = """You are {agent_name}, an autonomous AI agent working in the {team} team.
{persona}
You have access to a shared organisational memory written by agents of several teams.
Rules:
- Ground your answer in the shared memories below when they are relevant and cite them like [M1].
- If memories disagree, say so explicitly.
- If the memories do not cover the task, say that clearly and give your best independent answer.
- Be concise and practical.
- Finish with one line starting with "LEARNING:" containing a single reusable lesson."""


def build_user_prompt(task: str, memories: list[ContextMemory]) -> str:
    if memories:
        blocks = [
            f"[{m.ref}] ({m.team} team, {m.memory_type}, similarity {m.similarity:.2f}, confidence {m.confidence:.2f}"
            f"{', verified' if m.verified else ''})\nTitle: {m.title}\n{m.content}"
            for m in memories
        ]
        context = "\n\n".join(blocks)
    else:
        context = "(no relevant shared memories were found)"
    return f"Task:\n{task}\n\nShared memories:\n{context}"


class ExtractiveProvider:
    """Deterministic synthesis used when no LLM is configured.

    It does not invent content: it selects the sentences of the retrieved
    memories that best overlap with the task and presents them with citations.
    """

    name = "extractive"
    model = None
    _WORD = re.compile(r"[a-z0-9]{3,}")

    def _best_sentence(self, task_words: set[str], content: str) -> str:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if s.strip()]
        if not sentences:
            return content[:300]
        return max(sentences, key=lambda s: len(task_words & set(self._WORD.findall(s.lower()))))[:400]

    def complete(self, system: str, task: str, memories: list[ContextMemory]) -> LLMResult:
        if not memories:
            text = (
                "No relevant shared memory was found for this task, so it has to be solved from scratch. "
                "Store the outcome as a memory so other agents can reuse it.\n"
                "LEARNING: No prior organisational knowledge existed for this task."
            )
            return LLMResult(text=text, provider=self.name, model=None)
        task_words = set(self._WORD.findall(task.lower()))
        lines = [f"Found {len(memories)} relevant shared memor{'y' if len(memories) == 1 else 'ies'}:"]
        for m in memories:
            lines.append(f"- [{m.ref}] {m.title} ({m.team}, {m.memory_type}, similarity {m.similarity:.2f}): {self._best_sentence(task_words, m.content)}")
        top = memories[0]
        teams = sorted({m.team for m in memories})
        lines.append("")
        lines.append(f"Recommendation: reuse [{top.ref}] \"{top.title}\" from the {top.team} team as the starting point" + (f"; knowledge spans {', '.join(teams)}." if len(teams) > 1 else "."))
        lines.append(f"LEARNING: {top.title} - {self._best_sentence(task_words, top.content)}")
        return LLMResult(text="\n".join(lines), provider=self.name, model=None)

    def healthy(self) -> bool:
        return True


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings):
        self.model = settings.effective_llm_model
        self.max_tokens = settings.llm_max_tokens
        self.num_thread = settings.ollama_num_thread
        self._client = httpx.Client(base_url=settings.ollama_base_url.rstrip("/"), timeout=settings.llm_timeout_seconds)

    def complete(self, system: str, task: str, memories: list[ContextMemory]) -> LLMResult:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": build_user_prompt(task, memories)}],
            "options": {"temperature": 0.2, "num_predict": self.max_tokens},
        }
        if self.num_thread:
            body["options"]["num_thread"] = self.num_thread
        try:
            r = self._client.post("/api/chat", json=body)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        return LLMResult(
            text=data["message"]["content"].strip(), provider=self.name, model=self.model,
            usage={"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")},
        )

    def healthy(self) -> bool:
        try:
            return self._client.get("/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set")
        self.model = settings.effective_llm_model
        self.max_tokens = settings.llm_max_tokens
        self._client = httpx.Client(
            base_url=settings.openai_base_url.rstrip("/"),
            timeout=settings.llm_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )

    def complete(self, system: str, task: str, memories: list[ContextMemory]) -> LLMResult:
        body = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": build_user_prompt(task, memories)}],
        }
        try:
            r = self._client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        return LLMResult(text=data["choices"][0]["message"]["content"].strip(), provider=self.name, model=self.model, usage=data.get("usage", {}))

    def healthy(self) -> bool:
        return True


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.model = settings.effective_llm_model
        self.max_tokens = settings.llm_max_tokens
        self._client = httpx.Client(
            base_url=settings.anthropic_base_url.rstrip("/"),
            timeout=settings.llm_timeout_seconds,
            headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
        )

    def complete(self, system: str, task: str, memories: list[ContextMemory]) -> LLMResult:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": build_user_prompt(task, memories)}],
        }
        try:
            r = self._client.post("/v1/messages", json=body)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return LLMResult(text=text.strip(), provider=self.name, model=self.model, usage=data.get("usage", {}))

    def healthy(self) -> bool:
        return True


_provider = None


def get_llm():
    global _provider
    if _provider is None:
        settings = get_settings()
        try:
            _provider = {
                "none": lambda: ExtractiveProvider(),
                "ollama": lambda: OllamaProvider(settings),
                "openai": lambda: OpenAICompatibleProvider(settings),
                "anthropic": lambda: AnthropicProvider(settings),
            }[settings.llm_provider]()
        except LLMError as exc:
            logger.error("LLM provider %s misconfigured (%s); using extractive synthesis", settings.llm_provider, exc)
            _provider = ExtractiveProvider()
    return _provider


def complete_with_fallback(system: str, task: str, memories: list[ContextMemory]) -> LLMResult:
    provider = get_llm()
    try:
        return provider.complete(system, task, memories)
    except LLMError as exc:
        logger.warning("LLM call failed, falling back to extractive synthesis: %s", exc)
        result = ExtractiveProvider().complete(system, task, memories)
        result.fallback_reason = str(exc)[:300]
        return result
