"""
16 · A tool-calling agent, with Jev watching the loop as well as the calls
==========================================================================

    alert ─▶ agent proposes a call ─▶ Jev ─▶ run it        ─▶ result ─▶ agent …
                                         ├─▶ ask on-call to confirm
                                         ├─▶ block, needs approval
                                         ├─▶ nudge: skip it, "you already have this, try something else"
                                         ├─▶ stop: looping      → hand the trace to on-call
                                         └─▶ stop: resolved     → LLM writes the incident note

Cookbook 03 gates a single tool call on four hazards. A real agent makes dozens of calls
in a loop, and two of the ways it goes wrong are not visible in any one call:

  · It loops. The same `kubectl logs` with the same flags, five times, each one harmless,
    each one burning a minute of an incident.
  · It does not know when to stop. The alert cleared three calls ago, and it is still
    "investigating".

A loop gets one nudge — the repeated call is skipped and the agent is told why — and a
second loop ends the run. Agents often recover from one nudge; they rarely recover from two.

So every step asks two kinds of question in ONE Jev call: hazards about the proposed call,
and questions about the loop — given the last few calls and their results. The state is
the proposed call plus a short window of history, never the whole transcript.

    destructive      Noul    deletes, restarts, overwrites, scales down
    prod_write       Noul    changes production state
    blast_radius     Score   one pod · one service · the whole cluster
    repeats          Noul    repeats a recent call without new information
    resolved         Noul    the evidence already shows the alert condition has cleared

The tool results below are simulated: nothing here talks to a real cluster.

Run it:  python cookbooks/16_tool_agent_loop.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Noul, Score

from _shared import agent_text, jev, rule, show, wrap

STEP = {
    "destructive": Noul(
        instructions="`proposed` deletes, restarts, overwrites, or scales something down",
    ),
    "prod_write": Noul(
        instructions="`proposed` changes the state of a production system",
    ),
    "blast_radius": Score(
        instructions="How much is affected if `proposed` is wrong",
        criteria=["One pod or one file", "One service", "Several services or the whole cluster"],
    ),
    "repeats": Noul(
        instructions="`proposed` repeats a call in `recent` and the results in `recent` give "
                     "no reason to expect a different answer",
    ),
    "resolved": Noul(
        instructions="The results in `recent` show the condition in `alert` has cleared",
    ),
}

DESTRUCTIVE_BAR = 0.60     # confirm with on-call
PROD_WRITE_BAR = 0.50      # block until approved
BLAST_BAR = 1.5            # past "one service"
REPEAT_BAR = 0.75
REPEATS_TO_STOP = 2        # two repeats in a row is a loop, not caution
RESOLVED_BAR = 0.80
MAX_CALLS = 15

RUN, CONFIRM, BLOCK, NUDGE = "run", "confirm", "block", "nudge"
STOP_LOOP, STOP_DONE = "stop: looping", "stop: resolved"


def judge(alert: str, proposed: dict, recent: list[dict], repeat_streak: int,
          nudged: bool) -> tuple[str, str, dict, int]:
    a = jev().system_one(
        state={"alert": alert, "proposed": proposed, "recent": recent[-4:]},
        questions=STEP,
    ).answers

    # Loop-level checks first: they end the run, whatever the next call is.
    if a["resolved"].noul > RESOLVED_BAR:
        return STOP_DONE, "the evidence shows the alert has cleared", a, 0
    repeat_streak = repeat_streak + 1 if a["repeats"].noul > REPEAT_BAR else 0
    if repeat_streak >= REPEATS_TO_STOP:
        if nudged:
            return STOP_LOOP, "looping again after a nudge", a, repeat_streak
        return NUDGE, f"{repeat_streak} repeats in a row: skip it, tell the agent", a, 0

    # Then the call itself, most expensive hazard first.
    if a["prod_write"].noul > PROD_WRITE_BAR or a["blast_radius"].score > BLAST_BAR:
        return BLOCK, "changes production or reaches beyond one service", a, repeat_streak
    if a["destructive"].noul > DESTRUCTIVE_BAR:
        return CONFIRM, "destructive", a, repeat_streak
    return RUN, "read-only or contained", a, repeat_streak


# --------------------------------------------------------------------------------- demo
#
# A scripted agent: in a real deployment the next call comes from your LLM's tool-use
# output. The script includes the classic failure — re-reading the same logs.

ALERT = "checkout-service p95 latency above 2s for 10 minutes"

SCRIPT = [
    ({"tool": "metrics", "args": {"service": "checkout", "window": "30m"}},
     "p95 2.8s since 14:02; error rate flat"),
    ({"tool": "kubectl_logs", "args": {"pod": "checkout-7f9", "tail": 200}},
     "repeated 'connection pool exhausted (db-primary)'"),
    ({"tool": "kubectl_logs", "args": {"pod": "checkout-7f9", "tail": 200}},
     "repeated 'connection pool exhausted (db-primary)'"),
    ({"tool": "kubectl_logs", "args": {"pod": "checkout-7f9", "tail": 500}},
     "repeated 'connection pool exhausted (db-primary)'"),
    ({"tool": "db_pool_stats", "args": {"db": "db-primary"}},
     "100/100 connections in use; 80 idle in transaction, all from reports-job"),
    ({"tool": "kubectl_scale", "args": {"deployment": "db-proxy", "replicas": 0}},
     "[would take the database proxy down]"),
    ({"tool": "kubectl_delete_pod", "args": {"pod": "reports-job-5c2"}},
     "pod deleted; 80 connections released"),
    ({"tool": "metrics", "args": {"service": "checkout", "window": "10m"}},
     "p95 310ms since 14:31"),
    ({"tool": "metrics", "args": {"service": "checkout", "window": "5m"}},
     "p95 290ms"),
]


def main() -> None:
    print(__doc__.split("Run it:")[0])
    rule(f"Alert: {ALERT}")

    recent: list[dict] = []
    streak, nudged = 0, False
    for call, result in SCRIPT[:MAX_CALLS]:
        try:
            verdict, why, a, streak = judge(ALERT, call, recent, streak, nudged)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return
        print(f"  {verdict:<15} {json.dumps(call)[:60]:<62} "
              f"rep {a['repeats'].noul:.2f}  res {a['resolved'].noul:.2f}  {why}")
        if verdict.startswith("stop"):
            break
        if verdict == RUN:
            recent.append({**call, "result": result})
        elif verdict == NUDGE:
            nudged = True
            recent.append({**call, "result": "[skipped: you already have this result. "
                                               "Try a different tool.]"})
        else:
            recent.append({**call, "result": f"[not run: {verdict}]"})

    if verdict == STOP_DONE:
        note, source = agent_text(
            "Write a four-line incident note: what fired, what was found, what was done.",
            json.dumps(recent), sample="[the incident note would be written here]")
        show("incident note", note[:80], source)
    elif verdict == STOP_LOOP:
        show("handed to on-call", f"{len(recent)} calls of trace attached",
             "the agent was re-reading the same logs")
    else:
        show("script ended", "no stop condition fired", "in production: the MAX_CALLS budget")

    print()
    print(wrap(
        "Each of the repeated log reads is harmless on its own, and a per-call gate passes "
        "every one. Only a question about the loop catches it, and it costs nothing extra to "
        "ask it in the same call as the hazards."))


if __name__ == "__main__":
    main()
