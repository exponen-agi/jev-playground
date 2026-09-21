"""
01 · Support triage cascade
===========================

    Jev  →  plain code  |  specialist LLM  |  human

The highest-value pattern is not replacing your LLM. It is deciding which requests
deserve one.

One Jev call classifies the message and rates how hard it is to resolve. Then your
code picks the cheapest path that can actually handle it:

  · order status      → a database lookup. No model in this path at all.
  · product question  → a *narrow* specialist prompt. A narrow prompt beats a general one.
  · return / exchange → a different narrow specialist.
  · complaint         → a specialist, unless it's genuinely hard, in which case a human.
  · unsure            → a human, honestly, instead of guessing.

The business case, using TypeSafe's published per-case figures: a million tickets
through this shape costs roughly $6,480 against $30,400 for routing everything through
a frontier model, with around 800,000 of them answered in under half a second instead
of ten.

The saving is real, but it is the *second*-order benefit. The first-order benefit is
that the escalation path is driven by a calibrated number instead of a heuristic, so
the tickets a human sees are the ones a human is actually needed for.

Run it:  python cookbooks/01_triage_cascade.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

# --------------------------------------------------------------------------------
# The questions. Note there is no prompt here — there is a declared answer space.
#
# Everything is asked in ONE call. Adding a question barely changes response time and
# costs only the tokens for that question. Two of these are speculative: `order_id_present`
# only matters on the order_status branch, `wants_refund` only on returns. Ask anyway.
# --------------------------------------------------------------------------------

TRIAGE = {
    "intent": Choice(
        instructions="Primary intent of this customer message",
        criteria={
            "order_status":    "Asking about the status or location of an existing order",
            "product_question": "Asking about a product's features, sizing, or availability",
            "return_exchange": "Wants to return, exchange, or refund an item",
            "complaint":       "Unhappy about something and wants it resolved",
            "other":           "None of the above",
        },
    ),
    "complexity": Score(
        instructions="How complex is this to resolve",
        criteria=[
            "Simple lookup or standard procedure",
            "Requires judgment or multiple steps",
            "Unusual edge case, escalation needed",
        ],
    ),
    # Speculative. Only read on the order_status branch.
    "order_id_present": Noul(
        instructions="The message contains an order number or order identifier"),
    # Speculative. Only read on the return_exchange branch.
    "wants_refund": Noul(
        instructions="The customer explicitly asks for money back rather than a replacement"),
    # Read on every branch. This one guards the whole cascade.
    "needs_human_tone": Noul(
        instructions="The message describes harm, a legal threat, or a safety issue"),
}

SPECIALISTS = {
    "product_question": (
        "You are a product specialist for an online shoe store. Answer only from the "
        "catalogue facts you are given. If a fact is missing, say so and offer to check. "
        "Two sentences maximum."
    ),
    "return_exchange": (
        "You are a returns specialist. State the applicable policy, then the single next "
        "action the customer should take. Do not promise a refund amount. Three sentences maximum."
    ),
    "complaint": (
        "You are a complaint resolution specialist. Acknowledge the specific problem in the "
        "customer's own terms, state what you are doing about it, and give a timeline. "
        "Do not apologise more than once. Four sentences maximum."
    ),
}

# Thresholds live here, next to the actions they guard, in a file with a git history —
# not in a prompt a non-engineer edited last quarter. One threshold per action, scaled
# to what being wrong costs.
CONFIDENCE_FLOOR = 0.5      # below this the model is saying it does not know
COMPLAINT_CEILING = 1.0     # a complaint harder than "standard procedure" goes to a person


def triage(message: str) -> dict:
    """One round trip. Five judgments. A fraction of a cent."""
    response = jev().system_one(state=message, questions=TRIAGE)
    return {"answers": response.answers, "model": response.model}


def handle(message: str) -> tuple[str, str]:
    """Returns (path_taken, reply). All control flow stays in code."""
    result = triage(message)
    a = result["answers"]

    intent, complexity = a["intent"], a["complexity"]

    # The floor. Catches anything the model reports as genuinely uncertain, before
    # any branch gets a chance to act on a coin flip.
    if intent.confidence < CONFIDENCE_FLOOR:
        return "human (low confidence)", _to_human(message, a)

    # An absolute signal, not a relative one. A Noul can be high even when the Choice
    # is confident about something else entirely.
    if a["needs_human_tone"].noul > 0.6:
        return "human (safety/legal)", _to_human(message, a)

    if intent.choice == "order_status":
        # No model in this path at all. This is the branch that pays for the whole design.
        if a["order_id_present"].noul > 0.5:
            return "code (order lookup)", lookup_order(message)
        return "code (ask for order id)", "Happy to check — what is your order number?"

    if intent.choice in SPECIALISTS:
        if intent.choice == "complaint" and (
            complexity.score > COMPLAINT_CEILING or complexity.confidence < CONFIDENCE_FLOOR
        ):
            return "human (complex complaint)", _to_human(message, a)

        hint = ""
        if intent.choice == "return_exchange" and a["wants_refund"].noul > 0.7:
            # Jev already told us which way this is going. Say so in the prompt rather
            # than making the LLM re-derive it from the same text.
            hint = "\n\nThe customer is asking for a refund, not a replacement."

        return f"llm ({intent.choice} specialist)", _specialist(
            SPECIALISTS[intent.choice], message + hint
        )

    return "human (unclassified)", _to_human(message, a)


# ------------------------------------------------------------------ the cheap branches


def lookup_order(message: str) -> str:
    """Your actual database call. Deterministic, sub-millisecond, no model involved."""
    return "Order A-104 shipped Tuesday and is out for delivery today."


def _to_human(message: str, answers) -> str:
    return "Routed to a human agent with Jev's full probability distribution attached."


def _specialist(system_prompt: str, message: str) -> str:
    """Whichever frontier model you prefer. The routing decision already happened."""
    try:
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system_prompt, message)
        if have("OPENAI_API_KEY"):
            return openai_text(system_prompt, message)
    except MissingKey:
        pass
    return "[no frontier-model key set — this is where the specialist reply would go]"


# --------------------------------------------------------------------------------- demo

SAMPLES = [
    "Where is order A-104? It was supposed to arrive Monday.",
    "Do the trail runners come in a wide fit? I usually take a 10 EE.",
    "These arrived scuffed. I want my money back, not another pair.",
    "This is the fourth time I've written. My daughter tripped on the loose sole and "
    "hurt her wrist. I've spoken to a lawyer.",
    "hi",
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for message in SAMPLES:
        rule(f'"{message[:70]}{"..." if len(message) > 70 else ""}"')
        try:
            result = triage(message)
        except Exception as exc:  # noqa: BLE001 — demo ergonomics
            print(f"  could not reach Jev: {exc}")
            return

        a = result["answers"]
        show("intent", f"{a['intent'].choice}", f"confidence {a['intent'].confidence:.2f}")
        show("complexity", f"{a['complexity'].score:.2f}",
             f"confidence {a['complexity'].confidence:.2f}")
        show("order_id_present", f"{a['order_id_present'].noul:.2f}")
        show("wants_refund", f"{a['wants_refund'].noul:.2f}")
        show("needs_human_tone", f"{a['needs_human_tone'].noul:.2f}")

        path, reply = handle(message)
        show("→ path", path)
        print(wrap(reply, indent="     "))


if __name__ == "__main__":
    main()
