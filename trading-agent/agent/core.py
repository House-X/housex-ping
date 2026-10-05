"""The agent loop: streams Claude's reply, runs tools, gates money-moving actions on approval."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

import anthropic

from .config import settings
from .prompts import SYSTEM_PROMPT
from .tools import ALL_TOOLS, ToolExecutor, validate_input

MAX_STEPS = 25  # safety cap on tool round-trips per user turn


class TradingAgent:
    def __init__(self, broker, confirm: Callable[[str, dict], bool] | None = None,
                 on_text: Callable[[str], None] = lambda t: print(t, end="", flush=True),
                 on_tool: Callable[[str, dict], None] = lambda n, a: None,
                 approval_mode: str = "prompt"):
        self.client = anthropic.Anthropic()
        self.executor = ToolExecutor(broker, confirm, approval_mode)
        self.notes: list[str] = []  # e.g. approval outcomes, delivered with the next user message
        self.on_text = on_text
        self.on_tool = on_tool
        self.messages: list[dict] = []

    def _request(self):
        with self.client.beta.messages.stream(
            model=settings.model,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            tools=ALL_TOOLS,
            messages=self.messages,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for text in stream.text_stream:
                self.on_text(text)
            return stream.get_final_message()

    def ask(self, user_text: str) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        # The timestamp goes in the user turn, not the system prompt, so the prompt cache stays valid.
        notes = "".join(f"[{n}]\n" for n in self.notes)
        self.notes.clear()
        self.messages.append({"role": "user", "content": f"[{now}]\n{notes}{user_text}"})

        for _ in range(MAX_STEPS):
            response = self._request()
            # Append the full content (thinking, server-tool and fallback blocks included) unchanged.
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return "\n[The request was declined by the model's safety system.]"
            if response.stop_reason == "pause_turn":
                continue  # long server-side search; re-send to let it finish
            if response.stop_reason == "max_tokens":
                return "\n[Response hit the token limit.]"
            if response.stop_reason != "tool_use":
                return "".join(b.text for b in response.content if b.type == "text")

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                results.append(self._run_tool(block))
            # All results in one user message so Claude keeps making parallel calls.
            self.messages.append({"role": "user", "content": results})

        return "\n[Stopped: too many tool steps in one turn.]"

    def _run_tool(self, block) -> dict:
        args = block.input
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": "INVALID_JSON: re-issue the call with valid JSON input."}
        error = validate_input(block.name, args)
        if error:
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"Invalid input: {error}"}
        self.on_tool(block.name, args)
        try:
            return {"type": "tool_result", "tool_use_id": block.id,
                    "content": self.executor.run(block.name, args)}
        except Exception as e:
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"{type(e).__name__}: {e}"}

    def reset(self) -> None:
        self.messages = []
