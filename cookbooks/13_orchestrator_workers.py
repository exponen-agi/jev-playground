"""
13 · Orchestrator–workers, with Jev as the orchestrator
=======================================================

                         ┌──────────────▶ researcher ──┐
                         │ ┌────────────▶ coder ───────┤
    shared state ─▶ Jev ─┼─┼────────────▶ tester ──────┼─▶ shared state ─▶ Jev ─▶ …
     (compact)   "who's  │ └────────────▶ reviewer ────┘
                  next?" ├─▶ finish
                         └─▶ a person

In the usual multi-agent framework a supervisor LLM reads the whole conversation on every
turn and writes a string naming the next agent. A twenty-step task pays for twenty
supervisor calls, each one slower than the last because the transcript keeps growing, and
each one able to name an agent that does not exist.

That decision is a `Choice`. Asked of Jev it comes back in a fraction of a second, it can
only be one of the options you declared, and it carries a probability you can branch on.
Three more questions ride along in the same call:

    last_output_ok          did the worker that just ran actually do its sub-task?
    needs_external_action   is the next step going to send, deploy, or pay for something?
    stuck                   are the last few steps repeating without progress?

The LLM workers still do all of the work. Jev never writes a line of code here; it decides
who writes the next one.

The supervisor reads a COMPACT state — the task, its acceptance criteria, the last three
step summaries and a step count — not the transcript. Context rot is real, and a decision
that only needs four fields should only be shown four fields.

Run it:  python cookbooks/13_orchestrator_workers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import Agent, jev, rule, show, wrap

SUPERVISOR = {
    "next_agent": Choice(
        instructions="Which agent should act next, given `task`, `acceptance` and `recent_steps`",
        criteria={
            "researcher": "Facts about the bug or the codebase are missing and must be looked up",
            "coder":      "Code must be written or fixed",
            "tester":     "A change exists that has not been tested since it was made",
            "reviewer":   "Tests pass and the change has not been reviewed",
            "finish":     "Every item in `acceptance` is met by `recent_steps`",
            "human":      "Blocked, ambiguous, or the task is outside what these agents can do",
        },
    ),
    "last_output_ok": Score(
        instructions="How well the most recent step's output did what that agent was asked",
        criteria=[
            "Wrong or unusable",
            "Partly done; usable with another pass",
            "Did exactly what was asked",
        ],
    ),
    "needs_external_action": Noul(
        instructions="The obvious next step sends something to a customer, deploys to "
                     "production, or spends money",
    ),
    "stuck": Noul(
        instructions="The last three steps repeat the same attempt without new information",
    ),
}

CONFIDENCE_FLOOR = 0.60    # below this the supervisor is guessing: ask a person
STUCK_BAR = 0.70
EXTERNAL_BAR = 0.30        # low on purpose: sending or deploying is not reversible
MAX_STEPS = 10             # a budget, in code, that no model can talk its way past

WORKERS = {
    "researcher": "You investigate bugs. Find the relevant code and explain the cause in "
                  "three sentences.",
    "coder": "You write minimal, correct patches. Output a unified diff and one line on why.",
    "tester": "You run the test suite against the latest patch and report pass/fail with "
              "the failing test names.",
    "reviewer": "You review a patch for correctness and style. Approve, or list what must "
                "change.",
}


def compact(task: dict, history: list[tuple[str, str]]) -> dict:
    """What the supervisor sees. Four fields, not the transcript."""
    return {
        "task": task["title"],
        "acceptance": task["acceptance"],
        "recent_steps": [f"{who}: {what[:240]}" for who, what in history[-3:]],
        "step": len(history),
    }


def supervise(task: dict, workers: dict[str, Agent]) -> tuple[str, list[str]]:
    history: list[tuple[str, str]] = []
    log: list[str] = []

    for step in range(MAX_STEPS):
        a = jev().system_one(state=compact(task, history), questions=SUPERVISOR).answers
        nxt = a["next_agent"]
        note = (f"step {step + 1:>2}  next={nxt.choice:<10} conf {nxt.confidence:.2f}  "
                f"ok {a['last_output_ok'].score:.2f}  stuck {a['stuck'].noul:.2f}")

        if a["stuck"].noul > STUCK_BAR:
            log.append(note + "  → a person (stuck)")
            return "a person: the agents are going round in circles", log
        if nxt.confidence < CONFIDENCE_FLOOR or nxt.choice == "human":
            log.append(note + "  → a person (unsure)")
            return "a person: the supervisor is not sure what comes next", log
        if a["needs_external_action"].noul > EXTERNAL_BAR:
            log.append(note + "  → a person (approval)")
            return "a person: the next step acts outside the system", log
        if nxt.choice == "finish":
            log.append(note + "  → finish")
            return "done", log

        output, source = workers[nxt.choice].run(
            f"Task: {task['title']}\nRecent steps:\n" + "\n".join(
                f"- {who}: {what}" for who, what in history[-3:]))
        history.append((nxt.choice, output))
        log.append(note + f"  → {nxt.choice} [{source}]")

    return f"a person: step budget of {MAX_STEPS} used up", log


# --------------------------------------------------------------------------------- demo

TASKS = [
    {
        "title": "CSV export drops rows whose customer name contains an accent",
        "acceptance": ["rows with accented names are exported", "a regression test exists",
                       "the patch is reviewed"],
        "samples": {
            "researcher": ["export.py opens the file with encoding='ascii' and errors='ignore', "
                           "so any row with a non-ASCII character is silently skipped."],
            "coder": ["- open(path, 'w', encoding='ascii', errors='ignore')\n"
                      "+ open(path, 'w', encoding='utf-8', newline='')\n"
                      "+ test_export_keeps_accented_names()"],
            "tester": ["42 passed, 0 failed (including test_export_keeps_accented_names)."],
            "reviewer": ["Approved. UTF-8 with newline='' is the documented csv idiom."],
        },
    },
    {
        "title": "Flaky test: test_checkout_total fails about one run in ten",
        "acceptance": ["the test passes 100 runs in a row"],
        "samples": {
            "researcher": ["test_checkout_total compares floats with ==."],
            "coder": ["+ assert total == pytest.approx(expected)"],
            "tester": ["Failed 3 of 100 runs: test_checkout_total.",
                       "Failed 2 of 100 runs: test_checkout_total.",
                       "Failed 3 of 100 runs: test_checkout_total."],
            "reviewer": ["Not yet: still flaky."],
        },
    },
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    decisions = 0
    for task in TASKS:
        rule(task["title"])
        workers = {name: Agent(name, prompt, task["samples"][name]) for name, prompt in WORKERS.items()}
        try:
            outcome, log = supervise(task, workers)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return
        for line in log:
            print("  " + line)
        decisions += len(log)
        show("→ outcome", outcome)

    rule("What the supervisor cost")
    show("supervisor decisions", decisions, "one Jev call each")
    show("Jev", f"~{decisions * 0.25:.1f}s", "a fraction of a cent in total")
    show("an LLM supervisor", f"~{decisions * 4}s",
         "and each call re-reads a growing transcript")
    print()
    print(wrap(
        "The second task is the one to watch: patching the same float comparison again and "
        "again is exactly what `stuck` exists for. An LLM supervisor usually keeps trying, "
        "because every individual step looks reasonable."))


if __name__ == "__main__":
    main()
