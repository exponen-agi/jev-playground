"""Worker agents for the multi-agent cookbooks (11–16).

In every one of those cookbooks the LLM agents do the work — read, write, plan, call tools —
and Jev makes the decisions *between* them. This module is the worker half, kept small on
purpose so the decision half is what you read.

A worker calls whichever frontier model you have a key for. With no key set it returns a
canned sample output instead, labelled as such, so the Jev half of every cookbook still has
realistic material to judge with only TYPESAFE_API_KEY set.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .clients import MissingKey, anthropic_text, gemini_text, have, openai_text

# Tried in order. Reorder to prefer a provider; the decision half does not care which
# model wrote the text it is judging.
PROVIDERS: list[tuple[str, Callable[[str, str], str]]] = [
    ("ANTHROPIC_API_KEY", anthropic_text),
    ("OPENAI_API_KEY", openai_text),
    ("GOOGLE_API_KEY", gemini_text),
]


def agent_text(system: str, user: str, sample: str) -> tuple[str, str]:
    """Returns (text, source). `source` is "sample" when no provider key is set."""
    for env_var, call in PROVIDERS:
        if have(env_var):
            try:
                return call(system, user), env_var.split("_")[0].lower()
            except MissingKey:
                continue
    return sample, "sample"


@dataclass
class Agent:
    """One LLM worker: a name, a narrow system prompt, and sample outputs for keyless runs.

    `samples` is consumed in order, one per call, and the last one repeats. That is how the
    keyless demo scripts a retry that improves, or an agent that keeps making the same
    mistake.
    """

    name: str
    system: str
    samples: list[str] = field(default_factory=list)
    calls: int = 0

    def run(self, user: str) -> tuple[str, str]:
        sample = self.samples[min(self.calls, len(self.samples) - 1)] if self.samples else ""
        self.calls += 1
        return agent_text(self.system, user, sample or f"[{self.name} would answer here]")


def run_parallel(jobs: dict[str, Callable[[], tuple[str, str]]]) -> dict[str, tuple[str, str]]:
    """Runs independent agent calls at the same time. Wall time is the slowest one."""
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        return {name: future.result() for name, future in futures.items()}
