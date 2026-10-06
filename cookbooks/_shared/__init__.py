"""Shared plumbing for the cookbooks. Nothing clever lives here."""

from .agents import Agent, agent_text, run_parallel
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
    "Agent",
    "agent_text",
    "run_parallel",
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
