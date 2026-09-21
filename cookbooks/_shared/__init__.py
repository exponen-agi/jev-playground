"""Shared plumbing for the cookbooks. Nothing clever lives here."""

from .clients import (
    MissingKey,
    anthropic_text,
    gemini_text,
    have,
    jev,
    openai_text,
    rule,
    show,
    wrap,
)

__all__ = [
    "MissingKey",
    "anthropic_text",
    "gemini_text",
    "have",
    "jev",
    "openai_text",
    "rule",
    "show",
    "wrap",
]
