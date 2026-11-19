"""Cliente do Ollama (modelo local — os dados não saem da sua máquina)."""

from __future__ import annotations

import json

from leadfinder.net import HttpClient


class OllamaClient:
    def __init__(self, base_url: str, model: str, client: HttpClient | None = None, timeout: float = 240) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client or HttpClient(timeout=10, min_interval=0)
        self.timeout = timeout
        self._available: bool | None = None

    def models(self) -> list[str]:
        res, data = self.client.get_json(f"{self.base_url}/api/tags")
        if not res.ok or not isinstance(data, dict):
            return []
        return [m.get("name") for m in data.get("models", []) if m.get("name")]

    def available(self) -> bool:
        if self._available is None:
            self._available = self.model in self.models() or any(
                m.split(":")[0] == self.model.split(":")[0] for m in self.models()
            )
        return self._available

    def _chat(self, system: str, user: str, fmt: object | None) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.1},
        }
        if fmt is not None:
            payload["format"] = fmt
        res, data = self.client.post_json(f"{self.base_url}/api/chat", payload, timeout=self.timeout)
        if not res.ok or not isinstance(data, dict):
            return None
        return ((data.get("message") or {}).get("content") or "").strip() or None

    def chat_json(self, system: str, user: str, schema: dict | None = None) -> dict | None:
        content = self._chat(system, user, schema or "json")
        if content is None and schema is not None:
            content = self._chat(system, user, "json")  # versões antigas sem saída estruturada
        if not content:
            return None
        try:
            data = json.loads(content)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def chat_text(self, system: str, user: str) -> str | None:
        return self._chat(system, user, None)


def make_ollama(settings: dict) -> OllamaClient:
    return OllamaClient(settings.get("ollama_url", "http://localhost:11434"), settings.get("ollama_model", "qwen2.5:7b"))
