"""Provider registry. Add new providers (Codex, Gemini, ...) here."""

from __future__ import annotations

from ai_usage.providers.base import Provider


def get_provider(name: str = "claude") -> Provider:
    if name == "claude":
        from ai_usage.providers.claude import ClaudeProvider

        return ClaudeProvider()
    raise ValueError(f"unknown provider: {name!r}")


PROVIDERS = ("claude",)
