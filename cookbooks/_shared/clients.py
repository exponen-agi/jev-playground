"""Thin, lazy wrappers so a missing key skips one branch instead of the whole script.

The Jev half of every cookbook runs with only TYPESAFE_API_KEY set. The frontier-model
half degrades to a printed placeholder and says so.
"""

from __future__ import annotations

import os
import textwrap

try:  # optional convenience, not a requirement
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


class MissingKey(RuntimeError):
    """Raised when a cookbook branch needs a provider key that is not set."""


def have(env_var: str) -> bool:
    """True when the key is present. Cookbooks branch on this rather than crashing."""
    return bool(os.environ.get(env_var))


# --------------------------------------------------------------------------- Jev

_JEV = None


def jev():
    """A shared, version-pinned TypeSafe client.

    Pinning matters: `jev-latest` is an alias and moves when a release ships, so the
    answers behind it can change without a change on your side. Once you have tuned
    confidence thresholds against a version, pin the versioned ID and move on your own
    schedule. The response's `model` field reports which version actually answered.
    """
    global _JEV
    if _JEV is None:
        from typesafe_sdk import TypeSafeClient

        if not have("TYPESAFE_API_KEY"):
            raise MissingKey(
                "TYPESAFE_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        _JEV = TypeSafeClient(model=os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"))
    return _JEV


# ------------------------------------------------------- frontier models (optional)


def openai_text(system: str, user: str, model: str = "gpt-5.6-terra") -> str:
    if not have("OPENAI_API_KEY"):
        raise MissingKey("OPENAI_API_KEY")
    from openai import OpenAI

    response = OpenAI().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return response.choices[0].message.content or ""


def anthropic_text(system: str, user: str, model: str = "claude-opus-5") -> str:
    if not have("ANTHROPIC_API_KEY"):
        raise MissingKey("ANTHROPIC_API_KEY")
    import anthropic

    response = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def gemini_text(system: str, user: str, model: str = "gemini-3-pro") -> str:
    if not have("GOOGLE_API_KEY"):
        raise MissingKey("GOOGLE_API_KEY")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return response.text or ""


# ------------------------------------------------------------------------ printing


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * min(len(title), 72))


def show(label: str, value: object, note: str = "") -> None:
    suffix = f"   \033[2m{note}\033[0m" if note else ""
    print(f"  {label:<26} {value}{suffix}")


def wrap(text: str, indent: str = "  ") -> str:
    return textwrap.indent(textwrap.fill(text.strip(), width=86), indent)
