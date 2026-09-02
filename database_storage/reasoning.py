"""OpenAI client used only for graph-context identity and incident decisions."""

from __future__ import annotations

from openai import OpenAI

from .config import get_config


class GraphReasoner:
    def __init__(self, client=None, model: str | None = None):
        settings = get_config()["openai"]
        self.client = client or OpenAI(api_key=settings["api"])
        self.model = model or settings.get("model", "gpt-4o-mini")

    def ask(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content or ""
