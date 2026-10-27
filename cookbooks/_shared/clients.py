"""Thin, lazy wrappers so a missing key skips one branch instead of the whole script.

The Jev half of every cookbook runs with only TYPESAFE_API_KEY set. The frontier-model
half degrades to a printed placeholder and says so.

The "Jev half" can also be answered by an LLM instead of Jev: set AUTOJEV_BACKEND to
openai, anthropic or gemini (plus AUTOJEV_LLM_MODEL and that provider's key) and `jev()`
returns a system-one-adapter client with the same `system_one(state=, questions=)` call
and the same answer types. The cookbooks do not change. See .env.example.
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

# backend -> environment variables that may hold its key. "jev" is the default.
_BACKEND_KEYS = {
    "jev": ("TYPESAFE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def backend() -> str:
    """Which system_one backend answers: jev (default), openai, anthropic or gemini."""
    name = os.environ.get("AUTOJEV_BACKEND", "").strip().lower() or "jev"
    if name not in _BACKEND_KEYS:
        raise MissingKey(
            f"AUTOJEV_BACKEND={name!r} is not a backend; choose one of {', '.join(_BACKEND_KEYS)}."
        )
    return name


def jev():
    """A shared, version-pinned TypeSafe client.

    Pinning matters: `jev-latest` is an alias and moves when a release ships, so the
    answers behind it can change without a change on your side. Once you have tuned
    confidence thresholds against a version, pin the versioned ID and move on your own
    schedule. The response's `model` field reports which version actually answered.

    With AUTOJEV_BACKEND=openai|anthropic|gemini this is a system-one-adapter client
    instead: an LLM answers the same questions. Its probabilities are not Jev's, so
    thresholds tuned for one are not tuned for the other.
    """
    global _JEV
    if _JEV is None:
        name = backend()
        if not any(have(var) for var in _BACKEND_KEYS[name]):
            raise MissingKey(
                f"{' or '.join(_BACKEND_KEYS[name])} is not set. "
                "Copy .env.example to .env and add your key."
            )
        if name == "jev":
            from typesafe_sdk import TypeSafeClient

            _JEV = TypeSafeClient(model=os.environ.get("TYPESAFE_MODEL", "jev-1.13.0"))
        else:
            model = os.environ.get("AUTOJEV_LLM_MODEL", "").strip()
            if not model:
                raise MissingKey(
                    f"AUTOJEV_LLM_MODEL is not set. Name the {name} model to use; "
                    "there is no default because model names move."
                )
            try:
                from system_one_adapter import RetryPolicy, SystemOneAdapterClient
            except ImportError as exc:
                raise MissingKey(
                    f"pip install 'system-one-adapter[{name}]' to use the {name} backend."
                ) from exc
            _JEV = SystemOneAdapterClient(
                structured_outputs=True,
                llm_answer_mode="probabilities",
                normalize_probabilities=True,
                n_retry_malformed_structure=2,
                retry=RetryPolicy(max_retries=2),
                provider=name,
                model=model,
            )
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
