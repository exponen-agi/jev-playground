"""
12 · Parallel fan-out, with a Jev planner and a Jev merge
=========================================================

                               ┌─▶ security agent ─┐
    vendor packet ─▶ Jev plan ─┼─▶ privacy agent  ─┼─▶ Jev merge ─▶ sign · sign with conditions
                     (+ code)  ├─▶ legal agent    ─┤               · do not sign · a person
                               └─▶ finance agent ─┘

A new supplier wants a contract. Four specialist agents could review the packet: security,
privacy, legal, finance. Running all four on every vendor is the safe default, and it is
the default nobody can afford at volume: four frontier calls, four long prompts, for a
stationery supplier.

The parallel pattern has two decisions in it, and neither one needs a frontier model:

  1. PLAN — which specialists does this vendor actually need? One Jev call, one Noul per
     specialist, plus the bits of arithmetic that belong in code: a contract over $50k gets
     legal review no matter what the model thinks, because that rule is a policy, not a
     judgment.

  2. MERGE — the specialists come back with prose. Is each finding a blocker, a condition,
     or noise? Does it cite evidence from the packet, or is the agent speculating? One Jev
     call reads all of them at once — the questions are built from whichever specialists
     actually ran.

The selection bar is deliberately low (0.4). Missing a specialist costs more than running
one, and this is exactly the asymmetry a per-action threshold is for.

Run it:  python cookbooks/12_parallel_due_diligence.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Noul, Score

from _shared import Agent, jev, rule, run_parallel, show, wrap

PLAN = {
    "security": Noul(
        instructions="The vendor will store, process, or have network access to our systems "
                     "or data",
    ),
    "privacy": Noul(
        instructions="The vendor will handle personal data about our customers or employees",
    ),
    "legal": Noul(
        instructions="The contract summary departs from a standard supplier agreement: "
                     "uncapped or unusual liability, auto-renewal, exclusivity, IP assignment, "
                     "or unusual termination terms",
    ),
    "finance": Score(
        instructions="How financially fragile the vendor appears from the packet",
        criteria=[
            "Established; years of trading, audited accounts",
            "Some signs of fragility; young, small, or recently restructured",
            "Early-stage or distressed; continuity is a real question",
        ],
    ),
}

SELECT_BAR = 0.4           # low on purpose: a missed specialist costs more than a spare one
FRAGILE_BAR = 1.0
LEGAL_VALUE = 50_000       # policy, not judgment: code decides, Jev is not asked to compare numbers
FINANCE_VALUE = 100_000

SPECIALISTS = {
    "security": "You review vendors for information security. List concrete risks you can "
                "see in the packet, each with the sentence it comes from.",
    "privacy": "You review vendors for data protection. List concrete risks you can see in "
               "the packet, each with the sentence it comes from.",
    "legal": "You review supplier contracts. List terms that need negotiation, each with "
             "the clause it comes from.",
    "finance": "You review supplier financial stability. List concrete concerns, each with "
               "the evidence from the packet.",
}


def plan(packet: str, contract_value: int) -> tuple[list[str], dict]:
    """Which specialists to run. One Jev call plus two lines of policy."""
    a = jev().system_one(state=packet, questions=PLAN).answers

    chosen = [name for name in ("security", "privacy", "legal") if a[name].noul > SELECT_BAR]
    if a["finance"].score > FRAGILE_BAR:
        chosen.append("finance")

    # Arithmetic stays in code. Jev reads "$120,000" as text, not as a quantity.
    if contract_value >= LEGAL_VALUE and "legal" not in chosen:
        chosen.append("legal")
    if contract_value >= FINANCE_VALUE and "finance" not in chosen:
        chosen.append("finance")
    return chosen, a


def merge_questions(findings: dict[str, str]) -> dict:
    """Two questions per specialist that ran. Built at runtime, asked in one call."""
    questions = {}
    for name in findings:
        questions[f"{name}_severity"] = Score(
            instructions=f"How serious the most serious item in `{name}` is",
            criteria=[
                "Informational; nothing to do",
                "Acceptable with a named mitigation or contract change",
                "A blocker; do not sign as it stands",
            ],
        )
        questions[f"{name}_evidence"] = Noul(
            instructions=f"Each concern in `{name}` points to something specific in `packet`",
        )
    return questions


BLOCKER = 1.5              # past "acceptable with a mitigation"
CONDITION = 0.6
CONFIDENCE_FLOOR = 0.6
EVIDENCE_BAR = 0.5

SIGN, CONDITIONS, DO_NOT_SIGN, PERSON = "sign", "sign with conditions", "do not sign", "a person"


def merge(packet: str, findings: dict[str, str]) -> tuple[str, str, dict]:
    a = jev().system_one(
        state={"packet": packet, **findings},
        questions=merge_questions(findings),
    ).answers

    conditions = []
    for name in findings:
        severity = a[f"{name}_severity"]
        if severity.score > CONDITION and a[f"{name}_evidence"].noul < EVIDENCE_BAR:
            # An agent raising an alarm it cannot point to is a person's call, not ours.
            return PERSON, f"{name} raised a concern without evidence", a
        if severity.score > BLOCKER:
            if severity.confidence < CONFIDENCE_FLOOR:
                return PERSON, f"{name} may be a blocker (confidence {severity.confidence:.2f})", a
            return DO_NOT_SIGN, f"{name}: blocker", a
        if severity.score > CONDITION:
            conditions.append(name)

    if conditions:
        return CONDITIONS, "conditions from " + ", ".join(conditions), a
    return SIGN, "nothing material found", a


# --------------------------------------------------------------------------------- demo
#
# Sample findings are what each specialist "writes" when no LLM key is set.

VENDORS = [
    ("PaperCo — office stationery", 4_800,
     "PaperCo supplies office paper and stationery. Deliveries monthly to reception. No "
     "access to our systems. Standard supplier terms. Trading since 1987.",
     {"security": "No system access; nothing to review.",
      "privacy": "No personal data handled.",
      "legal": "Standard terms.",
      "finance": "Long-established; no concerns."}),
    ("Clinic CRM — patient booking SaaS", 120_000,
     "Clinic CRM hosts our patient appointment records and sends reminder texts. Data stored "
     "in their cloud, region unspecified. Contract auto-renews for 3 years; liability capped "
     "at one month's fees. Founded 2023, seed-funded.",
     {"security": "Hosting region unspecified ('region unspecified'); need SOC 2 report.",
      "privacy": "Patient data with no stated region ('Data stored in their cloud, region "
                 "unspecified') — a blocker until a data processing agreement names one.",
      "legal": "3-year auto-renewal and a one-month liability cap ('liability capped at one "
               "month's fees') — negotiate both.",
      "finance": "Seed-funded, founded 2023 — ask for an escrow or exit clause."}),
    ("FastFix — on-site IT repairs", 18_000,
     "FastFix technicians repair laptops on site and may take devices off-site for up to "
     "five days. Standard terms. Trading since 2015.",
     {"security": "Devices leave the building with data on them ('take devices off-site') — "
                  "require disk encryption before hand-over.",
      "privacy": "Laptops may hold employee personal data; covered if disks are encrypted.",
      "legal": "Standard terms.",
      "finance": "No concerns."}),
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for title, value, packet, samples in VENDORS:
        rule(f"{title}  (${value:,})")
        try:
            chosen, p = plan(packet, value)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return
        show("plan", ", ".join(chosen) or "nobody",
             "  ".join(f"{k} {p[k].noul:.2f}" for k in ("security", "privacy", "legal"))
             + f"  finance {p['finance'].score:.2f}")

        agents = {name: Agent(name, SPECIALISTS[name], [samples[name]]) for name in chosen}
        started = time.perf_counter()
        results = run_parallel({name: (lambda ag=ag: ag.run(packet)) for name, ag in agents.items()})
        wall = time.perf_counter() - started
        findings = {name: text for name, (text, _) in results.items()}
        for name, (text, source) in results.items():
            show(f"  {name}", text[:70] + ("…" if len(text) > 70 else ""), source)
        show("specialists run", f"{len(chosen)} of 4", f"in parallel, {wall:.2f}s wall time")

        if not findings:
            show("→ verdict", SIGN, "nothing needed review")
            continue
        verdict, reason, _ = merge(packet, findings)
        show("→ verdict", verdict, reason)

    print()
    print(wrap(
        "The stationery supplier should cost one Jev call and no specialist at all. The "
        "patient-booking vendor runs all four — two of them because the contract value "
        "crossed a line written in code, not because a model noticed the number."))


if __name__ == "__main__":
    main()
