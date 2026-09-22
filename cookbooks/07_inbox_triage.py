"""
07 · The shared inbox
=====================

    info@ / sales@ / support@  →  Jev  →  the right person's queue

Almost every small company has one mailbox that three people half-watch. It holds a
purchase enquiry worth six months of revenue, a supplier chasing a payment, four
recruiters, and a newsletter. Someone skims it between other jobs, and the enquiry sits
unread until Tuesday.

This is the most universal small-business use of a System One model, and it is not
because the sorting is hard. It is because the sorting is *boring, constant, and
latency-free to get right* — forty to four hundred messages a day, each needing six
bounded judgements that a person makes in two seconds and resents making.

What makes it work in practice is the confidence floor. The owner's instinct is right:
"when in doubt, put it on my desk." A calibrated model lets you write that instinct down
as a number. Everything above the floor is filed; everything below it lands in one
queue, honestly labelled *unsure* rather than silently misfiled — which is the failure
mode that makes people stop trusting the filter and go back to reading everything.

    Cost, at the scale this actually runs: 200 messages a day is six thousand Jev calls
    a month. The frontier model is only invoked to draft replies to genuine new
    enquiries, which is a few dozen of them.

Run it:  python cookbooks/07_inbox_triage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

# --------------------------------------------------------------------------------
# Six questions, one call. Note that `intent` and `department` are deliberately
# separate: what the email *is* and who *owns* it are two different judgements, and
# you will want to change the routing table without retraining your idea of intent.
# --------------------------------------------------------------------------------

TRIAGE = {
    "intent": Choice(
        instructions="What this email is fundamentally about",
        criteria={
            "new_enquiry":      "A prospective customer asking about products, services, or prices",
            "customer_issue":   "An existing customer with a problem, question, or change request",
            "billing":          "An invoice, payment, statement, or anything about money owed either way",
            "supplier":         "A supplier or contractor about orders, stock, or deliveries",
            "recruitment":      "A job application, recruiter, or anything about hiring",
            "bulk":             "Newsletters, marketing, cold outreach, automated notifications",
            "other":            "Genuine correspondence that fits none of the above",
        },
    ),
    "department": Choice(
        instructions="Which desk should own this",
        criteria={
            "sales":      "Someone who can quote, sell, or take an order",
            "support":    "Someone who can fix a problem with something already bought",
            "accounts":   "Someone who handles invoices, payments, and statements",
            "operations": "Someone who handles stock, suppliers, scheduling, and deliveries",
            "owner":      "A decision only the owner or a manager can make",
        },
    ),
    "urgency": Score(
        instructions="How soon this needs a reply",
        criteria=[
            "No reply needed at all",
            "This week is fine",
            "Today",
            "Now — someone is blocked, angry, or about to leave",
        ],
    ),
    "is_existing_customer": Noul(
        instructions="The sender writes as an existing customer of ours",
        criteria={
            "true": "References an order, an account, a previous purchase, a contract, a ticket",
            "false": "Writes as a stranger, a prospect, a supplier, or an applicant",
        },
    ),
    # Speculative — only read on the new_enquiry branch. Asking costs one question's tokens.
    "wants_a_quote": Noul(
        instructions="The sender is asking for a price, a quote, or availability"),
    # Read on every branch. This one overrides the routing table.
    "escalate": Noul(
        instructions="This needs a human immediately regardless of category",
        criteria={
            "true": "Legal threat, safety issue, a threat to cancel, a complaint that has "
                    "already been raised before, press or regulator contact",
            "false": "Routine correspondence, however irritated the tone",
        },
    ),
}

# Where each department's mail actually goes. A dict, in a file, with a git history.
DESKS = {
    "sales":      "sales@ — Ravi",
    "support":    "support@ — Mei",
    "accounts":   "accounts@ — Tom",
    "operations": "ops@ — Tom",
    "owner":      "the owner's desk",
}

ACK = (
    "Thanks for getting in touch — we have your message and someone will come back to "
    "you within one working day."
)

QUOTE_WRITER = (
    "You write first replies for a 14-person commercial flooring company. Acknowledge "
    "exactly what they asked for in their own words, state the two things you need from "
    "them before you can quote (site address and rough area in square metres), and give "
    "a timeline. No pricing. No pleasantries beyond one line. Four sentences maximum."
)

# Thresholds, per action, scaled to what being wrong costs.
CONFIDENCE_FLOOR = 0.75   # below this, nobody gets it but the owner — on purpose
ESCALATE_AT = 0.55        # low bar: a false positive costs one email of the owner's attention
URGENT_AT = 2.5           # "now" territory
BULK_ARCHIVE_AT = 0.90    # high bar: archiving a real email is the expensive mistake


def read_inbox(email: dict) -> dict:
    """One round trip, six judgements, for a fraction of a cent."""
    # Only the fields the questions need. Signature blocks and quoted threads are
    # padding, and padding costs accuracy — trim before you send, not after.
    state = {
        "from": email["from"],
        "subject": email["subject"],
        "body": email["body"],
    }
    return jev().system_one(state=state, questions=TRIAGE).answers


def route(email: dict) -> tuple[str, str]:
    """Returns (where_it_went, what_was_sent). All control flow stays in code."""
    a = read_inbox(email)

    # 1. The override. An absolute signal, checked before anything else.
    if a["escalate"].noul > ESCALATE_AT:
        return "the owner's desk — flagged", "Nothing auto-sent. Flagged as needs-a-person."

    # 2. The floor. This is the line that makes the whole thing trustworthy.
    if a["intent"].confidence < CONFIDENCE_FLOOR or a["department"].confidence < CONFIDENCE_FLOOR:
        return "the owner's desk — unsure", (
            f"Filed as unsure ({a['intent'].confidence:.2f} on intent). Better one pile of "
            "genuinely ambiguous mail than a plausible-looking wrong folder."
        )

    # 3. Bulk. Note the deliberately *higher* bar: the cost of archiving a real customer
    #    is not symmetric with the cost of leaving a newsletter in the inbox.
    if a["intent"].choice == "bulk" and a["intent"].confidence > BULK_ARCHIVE_AT:
        return "archived", "No reply. Not shown to anyone."

    desk = DESKS[a["department"].choice]
    urgent = " ⚑ today" if a["urgency"].score > URGENT_AT else ""

    # 4. The one branch worth paying a frontier model for: a stranger asking to buy
    #    something. Everything else gets a template or nothing, because everything else
    #    is a human replying properly, later.
    if a["intent"].choice == "new_enquiry" and a["wants_a_quote"].noul > 0.6:
        return f"{desk}{urgent}", _draft(QUOTE_WRITER, email["body"])

    if a["intent"].choice in ("customer_issue", "billing", "supplier"):
        return f"{desk}{urgent}", ACK

    return f"{desk}{urgent}", "No auto-reply. Routed only."


def _draft(system_prompt: str, body: str) -> str:
    """The routing decision already happened. This is the only expensive call."""
    try:
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system_prompt, body)
        if have("OPENAI_API_KEY"):
            return openai_text(system_prompt, body)
    except MissingKey:
        pass
    return "[no frontier-model key set — this is where the drafted first reply would go]"


# --------------------------------------------------------------------------------- demo

SAMPLES = [
    {
        "from": "j.okonkwo@harbourside-dev.co",
        "subject": "Flooring for a 3-floor office fit-out",
        "body": "We're fitting out a building on Dock Road, roughly 900 sqm over three "
                "floors, and need heavy-traffic vinyl. What would that come to, and "
                "could you start in March?",
    },
    {
        "from": "accounts@midland-supply.com",
        "subject": "Overdue: INV-20418",
        "body": "Invoice INV-20418 for $4,120 was due on the 14th and remains unpaid. "
                "Please advise on payment or we will place the account on hold.",
    },
    {
        "from": "dana.p@crestwood-retail.com",
        "subject": "Re: Re: still not fixed",
        "body": "This is the third time I've written about the lifting seam in the "
                "showroom. Someone was supposed to come out a fortnight ago. If it isn't "
                "resolved this week we'll be taking it further and cancelling the "
                "maintenance contract.",
    },
    {
        "from": "hello@growthspark.io",
        "subject": "Quick question about your marketing",
        "body": "Hi! I noticed your website isn't ranking for 'commercial flooring'. "
                "We help businesses like yours 10x their leads. Free audit — interested?",
    },
    {
        "from": "s.bayer@gmail.com",
        "subject": "",
        "body": "is this the right number for the thing on tuesday",
    },
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for email in SAMPLES:
        rule(f"{email['from']}  ·  {email['subject'] or '(no subject)'}")
        try:
            a = read_inbox(email)
        except Exception as exc:  # noqa: BLE001 — demo ergonomics
            print(f"  could not reach Jev: {exc}")
            return

        show("intent", a["intent"].choice, f"confidence {a['intent'].confidence:.2f}")
        show("department", a["department"].choice, f"confidence {a['department'].confidence:.2f}")
        show("urgency", f"{a['urgency'].score:.2f} / 3")
        show("existing customer", f"{a['is_existing_customer'].noul:.2f}")
        show("wants a quote", f"{a['wants_a_quote'].noul:.2f}")
        show("escalate", f"{a['escalate'].noul:.2f}")

        where, sent = route(email)
        show("→ goes to", where)
        print(wrap(sent, indent="     "))


if __name__ == "__main__":
    main()
