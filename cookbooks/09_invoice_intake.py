"""
09 · The bill that just arrived
===============================

    a PDF in accounts@  →  Jev  →  filed · chased · held for approval

Accounts payable at a small company is a person opening attachments. Is this an invoice
or a statement? Have we already paid it? Does it match something we actually ordered? Is
anything missing that will make it bounce at the bank? It is twenty minutes a day of
work that nobody has ever enjoyed, and the cost of getting it wrong is paying a bill
twice.

The reason this fits a System One model rather than a chat model is that the decision
needs *your data*, not the world's. Whether an invoice matches an open purchase order is
not a language question — it is a question about a list you already have in memory. Jev
takes that list as structured program state alongside the document text and answers
"which one, if any" in the same call as five other judgements.

    This cookbook assumes OCR has already happened. Use whatever you like for that —
    it is a solved problem and not the interesting part. What matters here is the layer
    above it, where the document has been turned into text and something has to decide
    what to do with it.

Two thresholds do the real work, and they are not the same number. The bar to *file*
something automatically is high, because a mis-filed bill is a payment you cannot see.
The bar to *chase* a supplier for a missing tax ID is low, because being wrong costs one
polite email.

Run it:  python cookbooks/09_invoice_intake.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

# Your open commitments. This is the state that makes the matching question answerable.
OPEN_POS = [
    {"po": "PO-4471", "supplier": "Midland Supply Co", "description": "Paper cups and lids",
     "amount": 1840.00, "raised": "2026-08-30"},
    {"po": "PO-4472", "supplier": "Harbour Print",     "description": "Menu reprint, 2000 units",
     "amount": 612.50, "raised": "2026-09-02"},
    {"po": "PO-4480", "supplier": "Coastal Cold Chain", "description": "September chiller service",
     "amount": 295.00, "raised": "2026-09-09"},
]

# Bills already in the ledger. Duplicate detection needs this, not cleverness.
ALREADY_PAID = {"INV-20390", "HP-1188"}


def ap_questions(open_pos: list[dict]) -> dict:
    """Built from your ledger, so the answer space is always current."""
    po_choices = {
        p["po"]: f"{p['supplier']} — {p['description']}, ${p['amount']:,.2f}" for p in open_pos
    }
    return {
        "doc_type": Choice(
            instructions="What kind of document this is",
            criteria={
                "invoice":       "A demand for payment for goods or services supplied",
                "credit_note":   "A refund or reduction against a previous invoice",
                "statement":     "A summary of account activity, not itself a demand",
                "receipt":       "Proof that something has already been paid",
                "delivery_note": "A record of goods delivered, with no amounts due",
                "not_financial": "Correspondence, marketing, or anything else",
            },
        ),
        "matches_po": Choice(
            instructions="Which open purchase order this document relates to",
            criteria={**po_choices, "none": "It does not correspond to any open order listed"},
        ),
        "amount_matches": Noul(
            instructions="The total on this document agrees with the matched purchase order",
            criteria={
                "true": "Same total, or different only by tax or a stated delivery charge",
                "false": "A materially different total, or no matched order to compare against",
            },
        ),
        "complete": Noul(
            instructions="The document carries everything needed to pay it",
            criteria={
                "true": "Invoice number, supplier tax ID, bank details, a total, and a due date",
                "false": "Any one of those is absent or illegible",
            },
        ),
        "unusual": Score(
            instructions="How far this departs from a routine bill from a known supplier",
            criteria=[
                "Routine — known supplier, expected amount",
                "Slightly off — new reference format, small variance",
                "Notably off — new bank details, unusual amount, unfamiliar supplier",
                "Alarming — urgency pressure, changed payee, mismatched entity name",
            ],
        ),
        # Low bar on purpose. Invoice fraud against small businesses is overwhelmingly
        # "we've changed bank account, please update your records".
        "payee_changed": Noul(
            instructions="The document asks for payment to different bank details than usual, "
                         "or applies pressure to pay urgently"),
    }


# Thresholds, each scaled to what being wrong actually costs.
FILE_CONFIDENCE = 0.88    # auto-filing a bill wrongly hides a payment. High bar.
CHASE_CONFIDENCE = 0.55   # chasing wrongly costs one email. Low bar.
APPROVAL_LIMIT = 1000.00  # a number the owner picked, in code, with a git history
UNUSUAL_HOLD = 2.0        # "notably off" and above never auto-files
FRAUD_ALARM = 0.35        # deliberately paranoid

INVOICE_NO = re.compile(r"\b([A-Z]{2,4}-?\d{3,6})\b")
TOTAL = re.compile(r"(?:total|amount due|balance)\D{0,12}([\d,]+\.\d{2})", re.I)


def parse_document(text: str) -> dict:
    """Numbers and references come from a parser, not a model. Same split as cookbook 08."""
    ref = INVOICE_NO.search(text)
    total = TOTAL.search(text)
    return {
        "invoice_number": ref.group(1) if ref else None,
        "total": float(total.group(1).replace(",", "")) if total else None,
    }


def process(document: str) -> tuple[str, str]:
    """Returns (outcome, note). Every branch is readable, testable, and revertible."""
    parsed = parse_document(document)
    a = jev().system_one(
        state={"document": document, "open_orders": OPEN_POS, "known_suppliers":
               sorted({p["supplier"] for p in OPEN_POS})},
        questions=ap_questions(OPEN_POS),
    ).answers

    # 1. Fraud first, before anything else gets a chance to file it.
    if a["payee_changed"].noul > FRAUD_ALARM or a["unusual"].score > 2.6:
        return "HELD — verify by phone", (
            "Changed payment details or pressure to pay. Never actioned automatically. "
            "Ring the supplier on the number you already have, not one on the document."
        )

    # 2. Duplicates. A set lookup, not a judgement.
    if parsed["invoice_number"] in ALREADY_PAID:
        return "rejected — already paid", f"{parsed['invoice_number']} is in the ledger already."

    if a["doc_type"].confidence < CHASE_CONFIDENCE:
        return "→ the owner", "Could not tell what this document is. Not guessing."

    if a["doc_type"].choice in ("statement", "delivery_note", "not_financial"):
        return f"filed as {a['doc_type'].choice}", "No payment action. Nobody had to open it."

    if a["doc_type"].choice != "invoice":
        return f"→ accounts ({a['doc_type'].choice})", "Routed to a person, labelled."

    # 3. Missing fields — the cheap-to-be-wrong branch, and the only one worth an LLM.
    if a["complete"].noul < 0.5:
        return "→ chase the supplier", _chase(document)

    # 4. Matching. The question that needed your data in the state.
    if a["matches_po"].choice == "none" or a["matches_po"].confidence < FILE_CONFIDENCE:
        return "→ the owner (no matching order)", (
            f"Nothing on the open-order list fits ({a['matches_po'].confidence:.2f}). "
            "Either it is a real cost nobody raised an order for, or it is not ours."
        )

    if a["amount_matches"].noul < 0.6:
        return "→ the owner (amount variance)", (
            f"Matched {a['matches_po'].choice} but the total disagrees with it."
        )

    if a["unusual"].score > UNUSUAL_HOLD:
        return "→ the owner (unusual)", "Matched, but it does not look like a routine bill."

    # 5. Clean, matched, routine. The approval limit is the last gate — a policy number,
    #    not a model output.
    if parsed["total"] and parsed["total"] > APPROVAL_LIMIT:
        return "→ approval queue", (
            f"${parsed['total']:,.2f} is over the ${APPROVAL_LIMIT:,.0f} limit. "
            f"Matched to {a['matches_po'].choice}, so approval is one click, not ten minutes."
        )

    return "filed for payment", (
        f"Matched to {a['matches_po'].choice}, complete, routine, under the limit. "
        "Nobody opened this attachment."
    )


def _chase(document: str) -> str:
    system = (
        "You write short emails to suppliers for a small company's accounts inbox. "
        "The invoice we received is missing something needed to pay it. Name exactly "
        "what is missing, ask for a corrected copy, and say we will pay on receipt. "
        "Three sentences maximum, no pleasantries."
    )
    try:
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system, document)
        if have("OPENAI_API_KEY"):
            return openai_text(system, document)
    except MissingKey:
        pass
    return "[no frontier-model key set — the chase email to the supplier would go here]"


# --------------------------------------------------------------------------------- demo

SAMPLES = [
    """MIDLAND SUPPLY CO — INVOICE MS-20441
    To: Pier Cafe Ltd.  Your order: PO-4471
    Paper cups 8oz x 4 sleeves, lids 8oz x 4 sleeves
    Total: 1,840.00   Due: 2026-10-01   VAT reg 448 2910 55
    Bank: Northbridge 40-12-88 / 3399 2010""",

    """HARBOUR PRINT — INVOICE HP-1188
    Menu reprint, 2000 units.  Total: 612.50  Due: 2026-09-30""",

    """COASTAL COLD CHAIN
    URGENT — PLEASE NOTE OUR BANK DETAILS HAVE CHANGED
    Invoice CCC-8871, September chiller service. Total: 295.00
    Payment must be received within 24 hours to avoid service suspension.
    New account: 62-11-04 / 8871 4420""",

    """MIDLAND SUPPLY CO — STATEMENT OF ACCOUNT
    Period: August 2026. Opening 2,410.00  Payments 2,410.00  Closing 0.00
    This is not a demand for payment.""",

    """QUICKFLOW LTD — INVOICE
    Consultancy services rendered.  Total: 3,600.00
    Please remit promptly.""",
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for document in SAMPLES:
        first_line = document.strip().splitlines()[0]
        rule(first_line[:72])
        try:
            a = jev().system_one(
                state={"document": document, "open_orders": OPEN_POS,
                       "known_suppliers": sorted({p["supplier"] for p in OPEN_POS})},
                questions=ap_questions(OPEN_POS),
            ).answers
        except Exception as exc:  # noqa: BLE001 — demo ergonomics
            print(f"  could not reach Jev: {exc}")
            return

        parsed = parse_document(document)
        show("doc type", a["doc_type"].choice, f"confidence {a['doc_type'].confidence:.2f}")
        show("matches", a["matches_po"].choice, f"confidence {a['matches_po'].confidence:.2f}")
        show("amount agrees", f"{a['amount_matches'].noul:.2f}",
             f"parser read {parsed['total']}")
        show("complete", f"{a['complete'].noul:.2f}")
        show("unusual", f"{a['unusual'].score:.2f} / 3")
        show("payee changed", f"{a['payee_changed'].noul:.2f}")

        outcome, note = process(document)
        show("→ outcome", outcome)
        print(wrap(note, indent="     "))


if __name__ == "__main__":
    main()
