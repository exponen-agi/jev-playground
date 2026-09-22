"""
10 · A month of bank transactions
=================================

    847 statement lines  →  Jev  →  780 filed · 67 for the bookkeeper

This is the one where calibration stops being a talking point and becomes the product.

Categorising bank lines is the monthly grind of every small business: a few hundred to a
few thousand rows of "SQ *THE BEAN 0987" and "AMZN MKTP UK*2H41J", each needing a
category, a business-or-personal call, and a note about whether a receipt is required.
It is rules-based right up until it isn't, and the exceptions are where the tax risk
lives.

What matters is not the accuracy of the model. It is that the model knows *which* rows
it is unsure about. If it does, the bookkeeper reads sixty rows instead of eight
hundred, and the eight hundred are not a leap of faith — they are the rows where a
calibrated model said it was sure, which is a statement you can audit.

So this cookbook does two things the others do not:

  1. It batches. One call per row, but rows are independent, so this is embarrassingly
     parallel — the map-reduce shape TypeSafe's docs point at. In production use
     AsyncTypeSafeClient and a semaphore; the loop below is kept simple on purpose.

  2. It checks the calibration against your own history, rather than believing the
     brochure. Take last year's categorised ledger, run it through, and bucket the
     results by reported confidence. If the 0.9+ bucket is 98% right and the 0.6 bucket
     is 65% right, the confidence number means something and you can set a floor. If
     both buckets look the same, it does not, and you should not automate any of it.
     `calibration_report()` at the bottom does exactly this, and it is the first thing
     to run before trusting a single filed row.

Run it:  python cookbooks/10_bookkeeping.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

# Your chart of accounts — the actual one, not a generic list. The answer space is
# defined before the model runs, so it cannot invent "Miscellaneous Expenses (Other)".
ACCOUNTS = {
    "cogs_stock":      "Stock and ingredients bought for resale",
    "wages":           "Salaries, contractors, payroll services",
    "rent_utilities":  "Rent, electricity, gas, water, waste",
    "equipment":       "Tools, machines, furniture, anything capitalised",
    "software":        "Subscriptions, hosting, licences, domains",
    "marketing":       "Advertising, print, sponsorship, agency fees",
    "travel":          "Fuel, fares, parking, accommodation, mileage",
    "professional":    "Accountancy, legal, insurance, bank charges",
    "repairs":         "Maintenance and repair of premises or equipment",
    "sales_income":    "Money in from customers",
    "owner_drawings":  "Money taken out by the owner personally",
    "transfer":        "Movement between the business's own accounts",
}

LEDGER = {
    "category": Choice(
        instructions="Which account this transaction belongs to",
        criteria=ACCOUNTS,
    ),
    "is_business": Noul(
        instructions="This is a business expense rather than a personal one",
        criteria={
            "true": "Plausibly incurred wholly for the business",
            "false": "Groceries, personal shopping, anything that looks like household spending",
        },
    ),
    "receipt_required": Noul(
        instructions="A receipt must be attached before this can be claimed",
        criteria={
            "true": "Expense over the receipt threshold, or any entertainment or travel",
            "false": "Recurring known direct debits, bank charges, transfers",
        },
    ),
    "recurring": Noul(
        instructions="This looks like a regular subscription or standing payment rather "
                     "than a one-off"),
    "needs_a_human": Noul(
        instructions="A bookkeeper should look at this regardless of the category",
        criteria={
            "true": "Round-number cash, unfamiliar payee, a large amount out of pattern, "
                    "anything that could be a duplicate",
            "false": "An ordinary transaction with a recognisable counterparty",
        },
    ),
}

# The floor is the whole design. Set it from calibration_report(), not from taste.
AUTO_FILE_FLOOR = 0.85
PERSONAL_FLOOR = 0.30     # below this on is_business, it is not going in the accounts
REVIEW_ANYWAY = 0.50      # needs_a_human overrides a confident category


def categorise(row: dict) -> dict:
    """One row, five judgements, one round trip.

    Rows are independent, which is what makes a month's statement a parallel problem
    rather than a sequential one. Nothing here depends on the row before it.
    """
    a = jev().system_one(
        # Only the columns the questions need. The bank's 40-character reference is the
        # signal; the internal transaction UUID is padding.
        state={
            "description": row["description"],
            "amount": row["amount"],
            "date": row["date"],
            "method": row.get("method", "card"),
        },
        questions=LEDGER,
    ).answers
    return {
        "row": row,
        "category": a["category"].choice,
        "confidence": a["category"].confidence,
        "is_business": a["is_business"].noul,
        "receipt_required": a["receipt_required"].noul,
        "recurring": a["recurring"].noul,
        "needs_a_human": a["needs_a_human"].noul,
    }


def file_or_flag(result: dict) -> tuple[str, str]:
    """The only three outcomes: filed, flagged, or kept out of the accounts entirely."""
    if result["is_business"] < PERSONAL_FLOOR:
        return "owner's drawings", "Not a business expense. Out of the P&L, into drawings."

    if result["needs_a_human"] > REVIEW_ANYWAY:
        return "review queue", "Flagged on its own merits, whatever the category confidence."

    if result["confidence"] < AUTO_FILE_FLOOR:
        return "review queue", (
            f"{result['confidence']:.2f} on {result['category']} — under the floor. "
            "This is the model saying it does not know, which is the useful part."
        )

    receipt = " · receipt needed" if result["receipt_required"] > 0.6 else ""
    return f"filed: {result['category']}{receipt}", "Nobody read this row."


def run_month(rows: list[dict]) -> list[dict]:
    """Map over the statement. In production: AsyncTypeSafeClient, bounded concurrency."""
    return [categorise(row) for row in rows]


def month_end_note(flagged: list[dict]) -> str:
    """The frontier model writes one paragraph, about sixty rows, once a month.

    Not one call per row. That distinction is the entire cost argument.
    """
    if not flagged:
        return "Nothing needed a human this month."
    summary = "\n".join(
        f"{f['row']['date']}  {f['row']['description']}  {f['row']['amount']:.2f}  "
        f"(best guess {f['category']}, {f['confidence']:.2f})"
        for f in flagged
    )
    system = (
        "You write the month-end note for a small business owner who is not an "
        "accountant. Given the transactions their bookkeeping could not file "
        "confidently, group them into themes and say plainly what you need from them. "
        "No jargon. Under 120 words."
    )
    try:
        if have("OPENAI_API_KEY"):
            return openai_text(system, summary)
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system, summary)
    except MissingKey:
        pass
    return "[no frontier-model key set — the month-end note about the flagged rows would go here]"


# ------------------------------------------------------------------ the calibration check


def calibration_report(labelled: list[dict]) -> str:
    """Run last year's already-categorised ledger through and bucket by confidence.

    This is the step that decides whether AUTO_FILE_FLOOR is a real number or a wish.
    Monotonically rising accuracy across the buckets means the confidence is load-
    bearing. A flat table means it is not, and nothing should be filed automatically.
    """
    buckets: dict[str, list[bool]] = defaultdict(list)
    for row in labelled:
        result = categorise(row)
        band = f"{int(result['confidence'] * 10) / 10:.1f}"
        buckets[band].append(result["category"] == row["true_category"])

    lines = ["  confidence   rows   agreed with the ledger"]
    for band in sorted(buckets, reverse=True):
        hits = buckets[band]
        lines.append(
            f"  {band}–{float(band) + 0.1:.1f}      {len(hits):>4}   "
            f"{sum(hits) / len(hits):.0%}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------------- demo

STATEMENT = [
    {"date": "2026-09-02", "description": "BOOKER WHOLESALE 4471", "amount": -612.40},
    {"date": "2026-09-02", "description": "STRIPE PAYOUT 88213", "amount": 4820.15},
    {"date": "2026-09-03", "description": "GOOGLE *GSUITE_piercafe", "amount": -28.80},
    {"date": "2026-09-04", "description": "TESCO STORES 3411", "amount": -74.22},
    {"date": "2026-09-05", "description": "SQ *COASTAL COLD CHAIN", "amount": -295.00},
    {"date": "2026-09-08", "description": "CASH WITHDRAWAL ATM 200.00", "amount": -200.00,
     "method": "cash"},
    {"date": "2026-09-09", "description": "AMZN MKTP UK*2H41J9", "amount": -139.99},
    {"date": "2026-09-11", "description": "NORTHBRIDGE BANK CHARGES", "amount": -12.50},
    {"date": "2026-09-12", "description": "SHELL FORECOURT A38", "amount": -68.40},
    {"date": "2026-09-15", "description": "TRANSFER TO SAVINGS 8871", "amount": -1000.00},
]

# A slice of last year's ledger, already categorised by a human. The calibration check
# needs nothing more exotic than this.
LABELLED = [
    {"date": "2025-09-02", "description": "BOOKER WHOLESALE 3301", "amount": -540.10,
     "true_category": "cogs_stock"},
    {"date": "2025-09-04", "description": "GOOGLE *GSUITE_piercafe", "amount": -28.80,
     "true_category": "software"},
    {"date": "2025-09-07", "description": "AMZN MKTP UK*9J12K", "amount": -84.20,
     "true_category": "equipment"},
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    rule("A month of statement lines")
    try:
        results = run_month(STATEMENT)
    except Exception as exc:  # noqa: BLE001 — demo ergonomics
        print(f"  could not reach Jev: {exc}")
        return

    flagged = []
    for result in results:
        outcome, note = file_or_flag(result)
        row = result["row"]
        show(f"{row['date']}  {row['description'][:26]:<26}",
             f"{row['amount']:>10.2f}   {outcome}",
             f"{result['confidence']:.2f}")
        if outcome == "review queue":
            flagged.append(result)

    rule("What a person actually has to read")
    show("rows on the statement", len(results))
    show("filed without a human", len(results) - len(flagged))
    show("in the review queue", len(flagged))
    print(wrap(month_end_note(flagged), indent="     "))

    rule("Is the confidence number load-bearing?")
    print(calibration_report(LABELLED))
    print(wrap(
        "Run this against a few hundred rows of your own history before you trust a "
        "single automatically filed transaction. If accuracy does not climb with "
        "confidence, the floor is decoration and everything belongs in the queue.",
        indent="  ",
    ))


if __name__ == "__main__":
    main()
