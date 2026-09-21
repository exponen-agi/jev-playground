"""
05 · Real-time scoring in the request path
==========================================

    form submit  →  Jev (one call, ~200ms)  →  route  →  LLM only for the top tier

This is the use case class that did not previously exist: AI inside a user-facing
request, where a three-second model call was never an option.

Lead scoring at form submission. Fraud pre-screening at checkout. Content moderation
before a post renders. Dynamic routing in an IVR. In each case the latency budget is a
few hundred milliseconds end to end, and the old answer was a gradient-boosted model on
hand-engineered features that took a quarter to build and goes stale quietly.

The composite-scoring pattern fits this well, because **it keeps the weights in your
code**:

    score = (0.45 * budget + 0.25 * seniority + 0.30 * fit)

Re-weighting is now a code change with a diff and a test, not a prompt rewrite whose
effects you cannot bound. You can A/B it. You can roll it back. Your growth team can
read it.

Contrast with asking one model one question — *"rate this lead 1-10"* — which hides
three judgments and a weighting scheme inside a single number you cannot inspect or
tune. Ask about each factor separately and combine them yourself.

The LLM appears exactly once, at the end, for the only thing a System One model cannot
do: write the actual first-touch email. And only for the leads that earned one.

Run it:  python cookbooks/05_realtime_lead_scoring.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

SCORING = {
    "budget_signal": Score(
        instructions="How strongly does this indicate real budget",
        criteria=[
            "No indication",
            "Vague interest",
            "Named budget or timeline",
            "Procurement process already underway",
        ],
    ),
    "seniority": Score(
        instructions="Seniority of the person enquiring",
        criteria=["Individual contributor", "Team lead", "Director", "VP or above"],
    ),
    "fit": Score(
        instructions="Fit with a mid-market B2B software buyer",
        criteria=["Poor fit", "Adjacent", "Good fit", "Ideal customer profile"],
    ),
    "urgency": Score(
        instructions="How soon this buyer appears to need a solution",
        criteria=["No stated timeline", "Exploring for later", "This quarter", "Immediately"],
    ),
    "is_spam": Noul(
        instructions="This submission is spam, automated, or a sales pitch to us "
                     "rather than a genuine enquiry",
    ),
    # Pass the FULL list, not a shortlist. Choice takes up to 255 options and each one
    # costs a few tokens, so pre-filtering the list is a false economy. The `other`
    # option lets the model say nothing fits instead of picking the closest wrong thing.
    "territory": Choice(
        instructions="Which sales territory owns this lead, based on company location",
        criteria={
            "amer_east": "US East Coast, Eastern Canada",
            "amer_west": "US West Coast, Western Canada",
            "amer_central": "US Central, Mexico",
            "latam": "Central and South America",
            "emea_uk": "United Kingdom and Ireland",
            "emea_dach": "Germany, Austria, Switzerland",
            "emea_nordics": "Denmark, Finland, Iceland, Norway, Sweden",
            "emea_south": "France, Italy, Spain, Portugal, Greece",
            "emea_other": "Rest of Europe, Middle East, Africa",
            "apac_anz": "Australia and New Zealand",
            "apac_india": "India and South Asia",
            "apac_other": "Rest of Asia-Pacific",
            "other": "Cannot be determined from the submission",
        },
    ),
}

# The weights. This is the part that would otherwise live in a prompt.
# Each Score is divided by its top level index so every term lands in [0, 1].
WEIGHTS = {"budget_signal": 0.40, "seniority": 0.20, "fit": 0.25, "urgency": 0.15}
LEVELS = {"budget_signal": 3, "seniority": 3, "fit": 3, "urgency": 3}

SPAM_BAR = 0.8
TIER_A = 0.66
TIER_B = 0.40
TERRITORY_CONFIDENCE_FLOOR = 0.6   # a mis-routed lead is a real cost to a real rep


def score_lead(lead: dict) -> dict:
    """One call, six judgments, inside a form-submit request. Returns a plain dict."""
    started = time.perf_counter()
    response = jev().system_one(state=lead, questions=SCORING)
    elapsed_ms = (time.perf_counter() - started) * 1000
    a = response.answers

    if a["is_spam"].noul > SPAM_BAR:
        return {"decision": "drop", "reason": "spam", "score": 0.0,
                "elapsed_ms": elapsed_ms, "answers": a}

    composite = sum(
        WEIGHTS[key] * a[key].score / LEVELS[key] for key in WEIGHTS
    )

    tier = "A" if composite > TIER_A else "B" if composite > TIER_B else "nurture"

    territory = a["territory"]
    owner = (
        territory.choice
        if territory.confidence >= TERRITORY_CONFIDENCE_FLOOR and territory.choice != "other"
        else "round_robin"   # honest fallback beats a confident guess at someone's quota
    )

    return {
        "decision": "route",
        "tier": tier,
        "score": composite,
        "territory": owner,
        "territory_confidence": territory.confidence,
        "elapsed_ms": elapsed_ms,
        "answers": a,
    }


def first_touch_email(lead: dict, result: dict) -> str:
    """The one thing a System One model cannot do — and only for tier A."""
    if result["tier"] != "A":
        return f"[tier {result['tier']} — enters the automated nurture sequence, no LLM call]"

    a = result["answers"]
    system = (
        "You write first-touch sales emails. Under 90 words. No exclamation marks, no "
        "'I hope this finds you well'. Reference the specific thing the person wrote. "
        "End with one concrete question."
    )
    # Hand the LLM what Jev already established, rather than making it re-derive it.
    user = (
        f"Lead submission:\n{lead}\n\n"
        f"Established by upstream scoring — do not re-litigate these:\n"
        f"- budget signal: {a['budget_signal'].score:.1f}/3\n"
        f"- urgency: {a['urgency'].score:.1f}/3\n"
        f"- seniority: {a['seniority'].score:.1f}/3\n"
    )
    try:
        if have("OPENAI_API_KEY"):
            return openai_text(system, user)
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system, user)
    except MissingKey:
        pass
    return "[no frontier-model key set — the tier-A email would be written here]"


# --------------------------------------------------------------------------------- demo

LEADS = [
    {
        "name": "Priya Raman", "title": "VP Engineering",
        "company": "Northwind Logistics", "company_size": "600",
        "location": "Manchester, UK",
        "message": "We've budgeted for a document processing platform this quarter and "
                   "have a shortlist of three. Procurement is already involved. Can we "
                   "get a technical deep-dive next week?",
    },
    {
        "name": "Sam Whitfield", "title": "Student",
        "company": "-", "company_size": "1", "location": "Austin, TX",
        "message": "doing a class project on AI, can i get a free account",
    },
    {
        "name": "Kenji Watanabe", "title": "Data Platform Lead",
        "company": "Sakura Retail", "company_size": "2000",
        "location": "Osaka, Japan",
        "message": "Evaluating options for next fiscal year. No timeline yet but I'd like "
                   "to understand how your pricing scales past 10M documents.",
    },
    {
        "name": "ACME SEO", "title": "Growth Hacker",
        "company": "acmeseo.biz", "company_size": "3", "location": "unknown",
        "message": "Hi! We can get your site to page 1 of Google GUARANTEED. Reply for "
                   "a free audit!!!",
    },
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for lead in LEADS:
        rule(f"{lead['name']} · {lead['title']} · {lead['company']}")
        try:
            result = score_lead(lead)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return

        if result["decision"] == "drop":
            show("→", "DROPPED", result["reason"])
            show("latency", f"{result['elapsed_ms']:.0f} ms")
            continue

        a = result["answers"]
        for key in WEIGHTS:
            show(key, f"{a[key].score:.2f} / {LEVELS[key]}",
                 f"weight {WEIGHTS[key]:.2f}  confidence {a[key].confidence:.2f}")
        show("composite", f"{result['score']:.3f}", "computed in code, not by the model")
        show("territory", result["territory"],
             f"confidence {result['territory_confidence']:.2f}")
        show("→ tier", result["tier"])
        show("latency", f"{result['elapsed_ms']:.0f} ms", "inside the form-submit budget")
        print()
        print(wrap(first_touch_email(lead, result), indent="     "))

    rule("Why the weights live in code")
    print(wrap(
        "Changing 0.40 to 0.30 is a pull request. It has a diff, a reviewer, a test, and "
        "a revert. Changing 'weigh budget heavily' in a prompt is none of those things, "
        "and you cannot bound its effect on the other three factors."))


if __name__ == "__main__":
    main()
