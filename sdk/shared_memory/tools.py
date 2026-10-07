"""Framework-agnostic LLM tool definitions for the shared memory.

Give any function-calling model (OpenAI, Anthropic, Gemini via LiteLLM, local
models) the ability to search and write shared memory:

    from shared_memory import MemoryClient
    from shared_memory.tools import MemoryToolkit

    toolkit = MemoryToolkit(MemoryClient(url, api_key=key))
    tools = toolkit.anthropic_tools()        # or toolkit.openai_tools()
    ...
    result_json = toolkit.dispatch(tool_name, tool_arguments)   # feed back to the model
"""
from __future__ import annotations

import json
from typing import Any

from shared_memory.client import MemoryClient
from shared_memory.errors import MemoryAPIError

MEMORY_TYPES = ["knowledge", "decision", "learning", "experience", "context", "task_result", "incident", "documentation", "solution"]

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_memory",
        "description": "Search the organisation's shared memory (decisions, incidents, solutions, learnings written by agents of all teams) "
        "by meaning and exact keywords. Call this before starting a task. Returns only memories you may read.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language question or keywords (error codes work)."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "memory_types": {"type": "array", "items": {"type": "string", "enum": MEMORY_TYPES}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": "Store ONE concise, reusable learning, decision or solution in shared memory after finishing a task.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 3, "maxLength": 200},
                "content": {"type": "string"},
                "memory_type": {"type": "string", "enum": MEMORY_TYPES, "default": "learning"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "get_memory",
        "description": "Read one shared memory by id.",
        "parameters": {"type": "object", "properties": {"memory_id": {"type": "string"}}, "required": ["memory_id"]},
    },
]


class MemoryToolkit:
    def __init__(self, client: MemoryClient):
        self.client = client

    @staticmethod
    def openai_tools() -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in TOOL_SPECS]

    @staticmethod
    def anthropic_tools() -> list[dict[str, Any]]:
        return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOL_SPECS]

    def dispatch(self, name: str, arguments: dict[str, Any] | str) -> str:
        """Execute a tool call and return a JSON string for the model (errors are returned, not raised)."""
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        try:
            if name == "search_memory":
                res = self.client.search(args["query"], top_k=int(args.get("top_k", 5)), memory_types=args.get("memory_types"))
                out: Any = {"results": [
                    {"memory_id": r.memory_id, "title": r.title, "content": r.content, "team": r.team,
                     "memory_type": r.memory_type, "similarity": r.similarity, "version": r.version} for r in res
                ]}
            elif name == "remember":
                created = self.client.remember(args["title"], args["content"], args.get("memory_type", "learning"), tags=args.get("tags"))
                out = {"memory_id": created.memory.id, "warnings": created.warnings.get("message")}
            elif name == "get_memory":
                m = self.client.get(args["memory_id"])
                out = {"memory_id": m.id, "title": m.title, "content": m.content, "team": m.team["name"], "version": m.version}
            else:
                out = {"error": f"unknown tool {name}"}
        except MemoryAPIError as exc:
            out = {"error": exc.message, "status": exc.status_code}
        except (KeyError, ValueError) as exc:
            out = {"error": f"invalid arguments: {exc}"}
        return json.dumps(out)
