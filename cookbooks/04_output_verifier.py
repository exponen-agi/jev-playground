"""
04 · LLM output verifier
========================

    GPT / Claude / Gemini generates  →  Jev judges it  →  ship | revise | human

Your LLM produced a paragraph. Is it grounded in the source you gave it? Does it cite
things the source actually says? Does it promise something your policy forbids? Does it
sound like your brand?

That is four System 1 judgments. You have almost certainly been making them with a
second LLM call, an "LLM-as-judge" that costs as much as the generation and takes as
long. Or you have not been making them at all, because the latency budget said no.

Jev turns the judge into an `if`-statement. All four questions go in one request, the
verdict is a number your code thresholds, and the whole thing fits inside the time the
generation already spent.

Two design notes worth stealing:

  1. **The generator and the judge should not be the same model.** A model asked to
     grade its own output shares its own blind spots. Different architecture, different
     training objective, different failure modes.

  2. **The source document goes in the state alongside the claim.** Jev cannot look
     anything up. Grounding is only checkable if you hand it the ground.

The same shape covers marketing copy review, generated SQL review, summary faithfulness,
and support-reply QA before it reaches a customer.

Run it:  python cookbooks/04_output_verifier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

# --------------------------------------------------------------------------------
# Six judgments, one call. Each asks exactly one thing — a composite "is this good?"
# would hide all six inside a number you could not act on differently.
# --------------------------------------------------------------------------------

VERIFY = {
    "grounded": Choice(
        instructions="How well is the draft supported by `source_document`?",
        criteria={
            "fully_supported":  "Every factual claim appears in the source",
            "partly_supported": "The main claims appear, but some details do not",
            "unsupported":      "Key claims do not appear in the source at all",
            "contradicts":      "The draft states something the source contradicts",
        },
    ),
    "invents_specifics": Noul(
        instructions="The draft states a number, date, name, or policy detail that does "
                     "not appear in `source_document`",
    ),
    "makes_a_promise": Noul(
        instructions="The draft commits the company to a specific outcome, refund, "
                     "timeline, or guarantee",
    ),
    "hedging": Score(
        instructions="How much the draft hedges rather than answering",
        criteria=[
            "Direct: answers the question outright",
            "Some qualification, but the answer is clear",
            "Heavily hedged; the reader cannot tell what the answer is",
        ],
    ),
    "on_brand": Score(
        instructions="Tone fit for a support reply that is warm, brief, and plain-spoken",
        criteria=[
            "Cold, bureaucratic, or robotic",
            "Acceptable but generic",
            "Warm, specific, and plain-spoken",
        ],
    ),
    "answers_the_question": Noul(
        instructions="The draft addresses what was actually asked in `customer_question`",
    ),
}

# Thresholds, per failure, by what shipping that failure costs.
GROUNDING_FLOOR = 0.6       # below this the grounding verdict itself is unreliable
INVENTION_BAR = 0.5         # a fabricated number in a customer reply is an incident
PROMISE_BAR = 0.6           # a promise needs a human, always
HEDGE_CEILING = 1.5
BRAND_FLOOR = 1.0


def verify(source: str, question: str, draft: str) -> tuple[str, list[str], dict]:
    """Returns (verdict, reasons, raw_answers). One round trip."""
    answers = jev().system_one(
        state={
            "source_document": source,
            "customer_question": question,
            "draft": draft,
        },
        questions=VERIFY,
    ).answers

    grounded = answers["grounded"]
    reasons: list[str] = []

    # Low confidence on the grounding question is itself disqualifying. If Jev cannot
    # tell whether the draft is supported, you cannot ship it on Jev's say-so.
    if grounded.confidence < GROUNDING_FLOOR:
        reasons.append(f"grounding unclear (confidence {grounded.confidence:.2f})")
    if grounded.choice in ("unsupported", "contradicts"):
        reasons.append(f"grounding: {grounded.choice}")
    if answers["invents_specifics"].noul > INVENTION_BAR:
        reasons.append("invents a specific not in the source")
    if answers["makes_a_promise"].noul > PROMISE_BAR:
        reasons.append("commits the company to an outcome")
    if not answers["answers_the_question"].noul > 0.5:
        reasons.append("does not answer what was asked")
    if answers["hedging"].score > HEDGE_CEILING:
        reasons.append("hedges to the point of being unhelpful")
    if answers["on_brand"].score < BRAND_FLOOR:
        reasons.append("off-brand tone")

    if not reasons:
        return "ship", [], answers
    # Factual problems need a person. Style problems can go back to the generator:
    # a second sampling pass reliably changes tone, and does not reliably change facts.
    factual = ("grounding", "invents", "commits", "does not answer")
    if any(r.startswith(factual) for r in reasons):
        return "human", reasons, answers
    return "revise", reasons, answers


def generate(source: str, question: str, extra: str = "") -> str:
    """Whatever you already use. Jev does not care which."""
    system = (
        "You are a support agent. Answer the customer's question using only the policy "
        "excerpt provided. Be warm and brief. Do not promise a specific outcome." + extra
    )
    user = f"Policy excerpt:\n{source}\n\nCustomer question:\n{question}"
    try:
        if have("OPENAI_API_KEY"):
            return openai_text(system, user)
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system, user)
    except MissingKey:
        pass
    return ""


def generate_and_verify(source: str, question: str, max_revisions: int = 1) -> str:
    """The loop. Note that only *style* failures are retried — a factual failure is
    not something a second sampling pass reliably fixes, so it goes to a person."""
    draft = generate(source, question)
    for _ in range(max_revisions + 1):
        verdict, reasons, _ = verify(source, question, draft)
        if verdict == "ship":
            return draft
        if verdict == "human":
            return f"[held for human review: {'; '.join(reasons)}]"
        draft = generate(source, question,
                         extra=f" Previous draft was rejected for: {'; '.join(reasons)}.")
    return "[held after revision budget exhausted]"


# --------------------------------------------------------------------------------- demo

SOURCE = (
    "Returns policy: unworn items may be returned within 30 days of delivery for a full "
    "refund. Worn items are not eligible. Refunds are issued to the original payment "
    "method. Processing time depends on the card issuer."
)
QUESTION = "I bought boots 3 weeks ago and wore them once on a hike. Can I get a refund?"

DRAFTS = {
    "grounded, correct": (
        "Thanks for checking. Our policy covers unworn items returned within 30 days, so "
        "boots that have been worn on a hike would not be eligible for a refund. If "
        "there's a fault with them rather than normal wear, let me know and I'll take a look."
    ),
    "invents a specific": (
        "You're within our 45-day window, so you're covered. We'll process your refund "
        "within 3 business days and you'll see it back on your card by Friday."
    ),
    "makes a promise": (
        "Absolutely — I've gone ahead and approved a full refund for you. It'll be back "
        "in your account shortly."
    ),
    "hedges into uselessness": (
        "That's a great question. Refund eligibility can depend on a number of factors, "
        "and there may be circumstances in which an exception could potentially apply, "
        "so it's worth considering the various possibilities here."
    ),
}


def main() -> None:
    print(__doc__.split("Run it:")[0])
    rule("Source")
    print(wrap(SOURCE))
    rule("Question")
    print(wrap(QUESTION))

    for label, draft in DRAFTS.items():
        rule(f"Draft: {label}")
        print(wrap(draft))
        try:
            verdict, reasons, a = verify(SOURCE, QUESTION, draft)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return

        print()
        show("grounded", a["grounded"].choice, f"confidence {a['grounded'].confidence:.2f}")
        show("invents_specifics", f"{a['invents_specifics'].noul:.2f}")
        show("makes_a_promise", f"{a['makes_a_promise'].noul:.2f}")
        show("answers_the_question", f"{a['answers_the_question'].noul:.2f}")
        show("hedging", f"{a['hedging'].score:.2f}")
        show("on_brand", f"{a['on_brand'].score:.2f}")
        show("→ verdict", verdict.upper(), "; ".join(reasons))


if __name__ == "__main__":
    main()
