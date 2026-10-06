"""
11 · Sequential pipeline with a gate at every handoff
=====================================================

    claim ─▶ intake agent ─▶ Jev ─▶ assessor agent ─▶ Jev ─▶ letter agent ─▶ Jev ─▶ send
                              │                        │                       │
                              └── retry · a person ────┴── retry · a person ───┴── retry · a person

A chain of LLM agents fails quietly. Stage two trusts whatever stage one handed it, so a
repair quote the intake agent invented becomes a confident paragraph in the assessor's
decision, and then a sentence in a letter to the customer. Nobody downstream ever saw the
original email.

The usual fix is an LLM judge at every handoff. That doubles the model calls and adds
seconds per stage, which is why most pipelines skip it and hope.

Here every handoff goes through one Jev call: is the output grounded in what this stage was
given, is it complete enough for the next stage, and a couple of questions that only matter
at one particular stage — asked at every gate anyway, because extra questions are nearly free.
Code turns the answers into one of three verdicts:

    pass       hand the output to the next agent
    retry      run the same agent again, told exactly which check failed — once
    a person   stop the line; this claim needs someone who can pick up the phone

Two things to notice:

  · The gate sees the stage's INPUT as well as its output. "Grounded" is a question about
    the pair. A gate that only reads the output can judge tone, never truth.
  · The retry feedback is built in code from the failing check's name. The agent is told
    "your summary includes an amount the email does not contain", not "please try harder".

Run it:  python cookbooks/11_sequential_pipeline.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Noul, Score

from _shared import Agent, jev, rule, show, wrap

GATE = {
    "grounded": Noul(
        instructions="Every fact in `output` is supported by `input`",
        criteria={
            "true": "Names, dates, amounts and events in the output all appear in the input",
            "false": "The output adds an amount, date, event or detail the input does not contain",
        },
    ),
    "complete": Score(
        instructions="How fully `output` provides what `next_stage_needs` lists",
        criteria=[
            "Missing something the next stage cannot work without",
            "Usable, with minor gaps",
            "Everything the next stage needs",
        ],
    ),
    # Only meaningful at intake. Asked at every gate, because asking costs almost nothing.
    "inconsistent_story": Noul(
        instructions="The claimant's own account in `input` contradicts itself on when, "
                     "where or how the damage happened",
    ),
    # Only meaningful at the letter stage. Asked anyway.
    "cold_or_blaming": Noul(
        instructions="`output` is addressed to the customer and assigns blame to them, "
                     "or reads as cold or dismissive",
    ),
}

# One bar per check, sitting next to the verdict it drives.
GROUNDED_BAR = 0.80        # below this, the stage invented something: retry it
COMPLETE_BAR = 1.2         # past "usable, with minor gaps"
INCONSISTENT_BAR = 0.60    # a contradictory story goes to a claims investigator, not a retry
COLD_BAR = 0.50
MAX_RETRIES = 1            # a second failure means the agent cannot fix it: stop the line

PASS, RETRY, PERSON = "pass", "retry", "a person"

# What the agent is told on a retry, keyed by the check that failed. Written in code, so it
# is specific, reviewable, and the same every time.
FEEDBACK = {
    "grounded": "Your output includes a fact the input does not contain. Remove anything "
                "you cannot point to in the input.",
    "complete": "Your output is missing something the next stage needs: {needs}.",
    "cold_or_blaming": "Your letter reads as cold or blames the customer. Keep the decision; "
                       "change the tone.",
}


@dataclass
class Stage:
    name: str
    agent: Agent
    next_stage_needs: str
    checks: tuple[str, ...]          # which of the GATE answers this stage is judged on


def gate(stage: Stage, given: str, output: str) -> tuple[str, str, dict]:
    """One Jev call per handoff. Returns (verdict, failing_check, raw_answers)."""
    a = jev().system_one(
        state={
            "stage": stage.name,
            "input": given,
            "output": output,
            "next_stage_needs": stage.next_stage_needs,
        },
        questions=GATE,
    ).answers

    if "inconsistent_story" in stage.checks and a["inconsistent_story"].noul > INCONSISTENT_BAR:
        return PERSON, "inconsistent_story", a
    if a["grounded"].noul < GROUNDED_BAR:
        return RETRY, "grounded", a
    if a["complete"].score < COMPLETE_BAR:
        return RETRY, "complete", a
    if "cold_or_blaming" in stage.checks and a["cold_or_blaming"].noul > COLD_BAR:
        return RETRY, "cold_or_blaming", a
    return PASS, "", a


def run_pipeline(stages: list[Stage], claim: str) -> tuple[str, list[str]]:
    """Runs the chain. Returns (outcome, trace). The control flow is plain Python."""
    trace: list[str] = []
    given = claim

    for stage in stages:
        feedback = ""
        for attempt in range(MAX_RETRIES + 1):
            output, source = stage.agent.run(given + feedback)
            verdict, failed, a = gate(stage, given, output)
            trace.append(
                f"{stage.name:<9} try {attempt + 1}  grounded {a['grounded'].noul:.2f}  "
                f"complete {a['complete'].score:.2f}  → {verdict}"
                + (f" ({failed})" if failed else "") + f"   [{source}]"
            )
            if verdict == PASS:
                break
            if verdict == PERSON:
                return f"stopped at {stage.name}: {failed}", trace
            feedback = "\n\nReviewer note: " + FEEDBACK[failed].format(needs=stage.next_stage_needs)
        else:
            return f"stopped at {stage.name}: still failing '{failed}' after a retry", trace
        given = output                                  # the next agent sees only this

    return "sent", trace


# --------------------------------------------------------------------------------- demo
#
# Sample outputs are what each agent "writes" when no LLM key is set. They are scripted so
# the demo exercises every branch: a clean claim, an intake summary that invents a figure
# and is fixed on retry, and a claimant whose story changes halfway through the email.

CLAIMS = {
    "Burst pipe, clean": (
        "From: Dana Ruiz. Policy HX-2231. On 3 March a pipe burst under our kitchen sink "
        "while we were home. Water damaged the cabinet and the floor. Photos attached. "
        "A plumber fixed the pipe the same day (invoice attached, £180).",
        {
            "intake": ["Policyholder Dana Ruiz, HX-2231. Escape of water, 3 March, kitchen. "
                       "Damage: sink cabinet and floor. Pipe repaired same day, invoice £180."],
            "assessor": ["Covered under escape of water (section 4). Pay plumber invoice £180; "
                         "arrange a surveyor for cabinet and floor."],
            "letter": ["Dear Dana, we're sorry about the leak. Your claim is covered. We will "
                       "pay the £180 plumber's invoice and a surveyor will call within 3 days."],
        },
    ),
    "Invented quote, fixed on retry": (
        "From: Sam Okafor. Policy HX-4410. Storm last night took several tiles off the roof. "
        "Rain is coming into the loft. I haven't had anyone out yet.",
        {
            "intake": [
                "Policyholder Sam Okafor, HX-4410. Storm damage to roof tiles; water entering "
                "loft. Roofer's quote £2,400.",                       # invented: no quote exists
                "Policyholder Sam Okafor, HX-4410. Storm damage to roof tiles; water entering "
                "loft. No tradesperson has inspected yet.",
            ],
            "assessor": ["Covered under storm (section 2). Authorise emergency tarpaulin; send "
                         "an approved roofer to quote."],
            "letter": ["Dear Sam, your claim is covered. An approved roofer will make the roof "
                       "safe today and quote for the repair."],
        },
    ),
    "Story changes mid-email": (
        "From: Lee Park. Policy HX-7781. My laptop was stolen from my car on Tuesday while "
        "I was at the gym. Actually it was taken from the house on Monday night, I think. "
        "The car was locked. Please pay out £2,100.",
        {
            "intake": ["Policyholder Lee Park, HX-7781. Theft of laptop, location and date "
                       "unclear (car Tuesday / house Monday). Claimed £2,100."],
            "assessor": ["(not reached)"],
            "letter": ["(not reached)"],
        },
    ),
}


def build_stages(samples: dict[str, list[str]]) -> list[Stage]:
    return [
        Stage("intake", Agent(
            "intake", "Summarise this insurance claim email for an assessor: who, policy, "
                      "what happened, when, what was damaged, any costs stated. Never add "
                      "facts that are not in the email.", samples["intake"]),
            next_stage_needs="policyholder, policy number, cause, date, damaged items, stated costs",
            checks=("inconsistent_story",)),
        Stage("assessor", Agent(
            "assessor", "Given a claim summary, state whether it is covered, under which "
                        "section, and the next action. Do not invent amounts.",
                        samples["assessor"]),
            next_stage_needs="covered or not, policy section, next action, any amount to pay",
            checks=()),
        Stage("letter", Agent(
            "letter", "Write a short, warm letter to the customer explaining the decision "
                      "and what happens next.", samples["letter"]),
            next_stage_needs="a letter a customer can read without calling us",
            checks=("cold_or_blaming",)),
    ]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    gates = 0
    for title, (email, samples) in CLAIMS.items():
        rule(title)
        try:
            outcome, trace = run_pipeline(build_stages(samples), email)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return
        for line in trace:
            print("  " + line)
        gates += len(trace)
        show("→ outcome", outcome)

    rule("What the gates cost")
    show("gate calls", gates, "one Jev call per handoff, retries included")
    show("added latency", f"~{gates * 0.25:.1f}s total", f"vs ~{gates * 4}s with an LLM judge per gate")
    show("added cost", "a fraction of a cent", "output tokens are free")
    print()
    print(wrap(
        "Watch the second claim: the invented roofer's quote should stop at the intake gate "
        "and be gone after one retry. That is the whole case for a gate: catching a fact at "
        "the handoff where it was introduced costs one retry; catching it in a complaint "
        "costs a customer."))


if __name__ == "__main__":
    main()
