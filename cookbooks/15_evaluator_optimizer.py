"""
15 · Evaluator–optimizer, with Jev as the evaluator
===================================================

    spec sheet ─▶ writer agent ─▶ Jev rubric ─▶ publish
                       ▲              │
                       └── feedback ──┤  (built in code, from the checks that failed)
                                      └─▶ a person   (round budget spent, or a banned claim twice)

A writer LLM drafts a product listing. Something has to judge the draft, say what is wrong,
and decide when to stop. The usual answer is a second LLM acting as critic. It writes
paragraphs of feedback, it rarely says "this is done", and the loop runs until the round
limit because nobody defined what done means.

Here the rubric is five Jev questions, each with its own bar:

    accurate        Noul   every claim is supported by the spec sheet
    banned_claim    Noul   a health, safety, or "best"/"guaranteed" claim the brand forbids
    on_voice        Score  how closely the draft matches the brand voice
    clear           Score  how fast a shopper gets what this is and who it is for
    has_next_step   Noul   ends with something the shopper can do

"Done" is not a sixth question. It is a line of code: every check is past its bar. That is
the difference that makes the loop terminate for a reason you can read.

The feedback for the next round is built in code from the names of the checks that failed,
so the writer gets "remove any claim the spec sheet does not support" instead of a
paragraph it can interpret however it likes. And a banned claim that survives two rounds
stops the loop: the writer is not going to talk itself out of it, and a person should see
why it keeps appearing.

Run it:  python cookbooks/15_evaluator_optimizer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Noul, Score

from _shared import Agent, jev, rule, show, wrap

RUBRIC = {
    "accurate": Noul(
        instructions="Every product claim in `draft` is supported by `spec_sheet`",
    ),
    "banned_claim": Noul(
        instructions="`draft` makes a claim listed in `banned`",
    ),
    "on_voice": Score(
        instructions="How closely `draft` matches `brand_voice`",
        criteria=["Off-brand", "Close, with a few lines that jar", "Reads as ours"],
    ),
    "clear": Score(
        instructions="How quickly a shopper understands what this is and who it is for",
        criteria=["Confusing", "Clear after a re-read", "Clear at a glance"],
    ),
    "has_next_step": Noul(
        instructions="`draft` ends with a clear next step for the shopper",
    ),
}

# Each check: (passes?, what the writer is told when it does not). All of it in code.
QUALITY_BAR = 1.4          # for the two Scores: most of the way to the top level
CHECKS = {
    "accurate":      (lambda a: a["accurate"].noul > 0.85,
                      "Remove or correct any claim the spec sheet does not support."),
    "banned_claim":  (lambda a: a["banned_claim"].noul < 0.20,
                      "Remove the banned claim. Do not rephrase it."),
    "on_voice":      (lambda a: a["on_voice"].score > QUALITY_BAR,
                      "Match the brand voice more closely: plain, warm, no hype."),
    "clear":         (lambda a: a["clear"].score > QUALITY_BAR,
                      "Say what it is and who it is for in the first sentence."),
    "has_next_step": (lambda a: a["has_next_step"].noul > 0.70,
                      "End with one clear next step for the shopper."),
}
MAX_ROUNDS = 4


def improve(product: dict, writer: Agent) -> tuple[str, str, list[str]]:
    """Returns (outcome, final_draft, log). The loop's exit conditions are all in code."""
    feedback, log, banned_streak, draft = "", [], 0, ""

    for round_ in range(1, MAX_ROUNDS + 1):
        draft, source = writer.run(
            f"Spec sheet:\n{product['spec']}\n\nWrite a 60-word product listing.{feedback}")
        a = jev().system_one(
            state={"draft": draft, "spec_sheet": product["spec"],
                   "brand_voice": product["voice"], "banned": product["banned"]},
            questions=RUBRIC,
        ).answers

        failed = [name for name, (passes, _) in CHECKS.items() if not passes(a)]
        log.append(f"round {round_}  failed: {', '.join(failed) or 'nothing'}   [{source}]")

        if not failed:
            return "publish", draft, log
        banned_streak = banned_streak + 1 if "banned_claim" in failed else 0
        if banned_streak >= 2:
            return "a person: the banned claim keeps coming back", draft, log
        feedback = "\n\nFix only these:\n" + "\n".join(f"- {CHECKS[n][1]}" for n in failed)

    return f"a person: still failing after {MAX_ROUNDS} rounds", draft, log


# --------------------------------------------------------------------------------- demo

VOICE = "Plain, warm, practical. Short sentences. No exclamation marks, no superlatives."
BANNED = ["medical or health benefits", "'best' or 'number one'", "'guaranteed'"]

PRODUCTS = [
    {
        "name": "Linen throw",
        "spec": "100% European linen. 130 x 170 cm. Machine wash at 40°C. Colours: oat, sage.",
        "samples": [
            "The BEST throw you'll ever own!!! Luxurious linen that transforms any room.",
            "A 100% European linen throw, 130 x 170 cm, in oat or sage. Machine washable at "
            "40°C, and softer with every wash. Pick your colour below.",
        ],
    },
    {
        "name": "Lavender pillow mist",
        "spec": "Lavender and chamomile essential oils in water. 100 ml spray.",
        "samples": [
            "A calming lavender mist clinically shown to improve sleep. Spray and drift off.",
            "Lavender and chamomile in a 100 ml spray. Proven to help you sleep better.",
            "Lavender and chamomile in a 100 ml spray, for a pillow that smells of a summer "
            "garden. Helps you sleep.",
        ],
    },
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for product in PRODUCTS:
        rule(product["name"])
        writer = Agent("writer", "You write short product listings.", product["samples"])
        product = {**product, "voice": VOICE, "banned": BANNED}
        try:
            outcome, draft, log = improve(product, writer)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return
        for line in log:
            print("  " + line)
        show("→ outcome", outcome)
        show("  final draft", draft[:72] + ("…" if len(draft) > 72 else ""))

    print()
    print(wrap(
        "The pillow mist is the case to watch. Every rewrite softens the sleep claim, and none "
        "of them removes it. Two rounds with a banned claim is the signal that the brief or the "
        "product needs a person, not that the writer needs a third try."))


if __name__ == "__main__":
    main()
