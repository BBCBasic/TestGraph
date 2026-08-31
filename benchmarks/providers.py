from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class ClassificationReply:
    type: str
    reason: str


@dataclass(frozen=True)
class ModelReply:
    model: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def audit_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelProvider(Protocol):
    name: str

    def classify(self, prompt: str) -> ModelReply: ...


def parse_classification_reply(text: str) -> ClassificationReply:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("model reply must be a JSON object containing type and reason") from exc
    if not isinstance(payload, dict):
        raise ValueError("model reply must be a JSON object")
    subject_type = str(payload.get("type") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not subject_type:
        raise ValueError("model reply is missing a non-empty type")
    if not reason:
        raise ValueError("model reply is missing a non-empty reason")
    return ClassificationReply(type=subject_type, reason=reason)


def _openai_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    if not chunks:
        raise ValueError("OpenAI response did not contain text output")
    return "\n".join(chunks)


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 90.0, base_url: str = "https://api.openai.com"):
        if not api_key:
            raise ValueError("OpenAI API key is required")
        if not model:
            raise ValueError("OpenAI model is required")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    def classify(self, prompt: str) -> ModelReply:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/responses",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "input": prompt, "max_output_tokens": 300},
            )
            response.raise_for_status()
            payload = response.json()
        usage = payload.get("usage") or {}
        return ModelReply(
            model=self.model,
            text=_openai_text(payload),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            raw=payload,
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 90.0, base_url: str = "https://api.anthropic.com"):
        if not api_key:
            raise ValueError("Anthropic API key is required")
        if not model:
            raise ValueError("Anthropic model is required")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    def classify(self, prompt: str) -> ModelReply:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
        chunks = [
            block.get("text")
            for block in payload.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        if not chunks:
            raise ValueError("Anthropic response did not contain text output")
        usage = payload.get("usage") or {}
        return ModelReply(
            model=self.model,
            text="\n".join(chunks),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            raw=payload,
        )
