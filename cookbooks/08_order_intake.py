"""
08 · Orders arriving by WhatsApp
================================

    "2 crates of the 500ml, same as last time"  →  Jev  →  a draft order

For a very large number of small businesses the ordering system *is* a phone number.
Customers send a line of text, at eleven at night, with no product codes, no quantities
in a consistent place, and a fair amount of "the usual". Someone types it into the
system in the morning and occasionally gets it wrong.

This is a good System One problem for an unobvious reason: the messages are terrible.
They are short, unpunctuated, full of shorthand, and arrive mid-conversation. A chat
model handles that fine but costs a round trip of reasoning for every "ok" and "thanks"
in the thread — and most of the thread is "ok" and "thanks".

    ⚠ The discipline worth stealing from this one: Jev has three primitives and none of
      them is "extract a number". The answer space has to exist before the model runs,
      so a free-form quantity is not a question you can ask it.

      What you ask instead is whether a quantity is *present*, and whether it attaches to
      the product — both bounded, both calibrated. Then a five-line regex pulls the digits
      out, deterministically, and the two results have to agree before an order is
      created. Code does the parsing. The model does the judging. That split is the
      whole pattern, and it is far more robust than asking a chat model to return JSON
      and hoping.

The frontier model earns its place on exactly one branch: when the order is real but
something is missing, and the reply has to ask for it without sounding like a robot.

Run it:  python cookbooks/08_order_intake.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import MissingKey, anthropic_text, have, jev, openai_text, rule, show, wrap

# The catalogue is program state, not prompt text. It changes when the catalogue
# changes, and the answer space changes with it.
CATALOGUE = {
    "still_500":  "Still water, 500ml, case of 24",
    "still_1l":   "Still water, 1 litre, case of 12",
    "spark_330":  "Sparkling water, 330ml, case of 24",
    "cups_8oz":   "Paper cups, 8oz, sleeve of 1000",
    "lids_8oz":   "Cup lids, 8oz, sleeve of 1000",
}


def intake_questions(catalogue: dict[str, str]) -> dict:
    """Questions are built from the catalogue, so a new SKU is a data change."""
    return {
        "message_type": Choice(
            instructions="What this customer message is doing",
            criteria={
                "new_order":    "Asking for goods to be supplied",
                "amend_order":  "Changing or cancelling an order already placed",
                "chasing":      "Asking where an existing order has got to",
                "price_query":  "Asking what something costs or whether it is in stock",
                "complaint":    "Something was wrong with what arrived",
                "chit_chat":    "Greetings, thanks, confirmations, anything with no action in it",
            },
        ),
        "product": Choice(
            instructions="Which catalogue line this message is about",
            criteria={**catalogue, "unclear": "No single catalogue line is clearly indicated"},
        ),
        "quantity_stated": Noul(
            instructions="The message states how many of the product they want",
            criteria={
                "true": "A number, or a word like 'a dozen', 'a pallet', 'double'",
                "false": "No amount given, or only 'the usual' with no number",
            },
        ),
        "refers_to_history": Noul(
            instructions="Resolving this message requires knowing what they ordered before",
            criteria={
                "true": "'the usual', 'same as last time', 'the other one', 'like Tuesday'",
                "false": "The message stands on its own",
            },
        ),
        "urgency": Score(
            instructions="How soon they need it",
            criteria=["Whenever", "This week", "Tomorrow", "Today — they have run out"],
        ),
        "unhappy": Noul(
            instructions="The customer is annoyed, complaining, or threatening to go elsewhere"),
    }


# Thresholds. The asymmetry here is the point: creating a wrong order costs a delivery,
# a credit note and a phone call, so the bar to act without a human is high.
AUTO_CREATE = 0.88        # confidence needed on BOTH message_type and product
ASK_BACK = 0.60           # above this we know enough to ask a sensible question
UNHAPPY_AT = 0.5          # anything with heat in it goes to a person, order or not

QUANTITY = re.compile(
    r"(?<![\w.])(\d{1,4})\s*(?:x|×)?\s*"
    r"(?:cases?|crates?|boxes|sleeves?|packs?|units?)?(?![\w])",
    re.I,
)
WORD_QUANTITIES = {"a dozen": 12, "dozen": 12, "half a dozen": 6, "a pallet": 40, "pallet": 40}


def parse_quantity(message: str) -> int | None:
    """Deterministic. Testable. Free. Never asked of a model.

    Jev tells you whether a quantity is *there*; this tells you what it is. If the two
    disagree, that disagreement is a signal, and the code below treats it as one.
    """
    lowered = message.lower()
    for phrase, value in WORD_QUANTITIES.items():
        if phrase in lowered:
            return value
    found = QUANTITY.findall(message)
    return int(found[0]) if found else None


def handle(message: str, customer: dict) -> tuple[str, str]:
    """Returns (what_happened, what_we_sent_back)."""
    state = {
        "message": message,
        "customer": customer["name"],
        # Structured program state, not a paragraph about the customer. This is what
        # lets "the usual" resolve without a reasoning model.
        "last_order": customer.get("last_order"),
        "open_orders": customer.get("open_orders", []),
    }
    a = jev().system_one(state=state, questions=intake_questions(CATALOGUE)).answers

    kind, product = a["message_type"], a["product"]

    if a["unhappy"].noul > UNHAPPY_AT or kind.choice == "complaint":
        return "→ a person, now", "Nothing auto-sent. The owner sees this one."

    if kind.choice == "chit_chat" and kind.confidence > 0.8:
        # The branch that pays for the pattern. Roughly half of a real WhatsApp thread
        # is this, and none of it should reach a model that charges by the token.
        return "ignored", "No action. Nobody had to read it."

    if kind.choice != "new_order":
        return f"→ {kind.choice} queue", "Routed to a person with Jev's read attached."

    # --- a new order. Now the two sources have to agree. --------------------------
    stated = a["quantity_stated"].noul > 0.5
    parsed = parse_quantity(message)

    if product.choice == "unclear" or product.confidence < ASK_BACK:
        if a["refers_to_history"].noul > 0.6 and customer.get("last_order"):
            # The history was in the state all along, so this needs no second call.
            guess = customer["last_order"]
            return "draft order (from history)", (
                f"Repeating last order: {guess['qty']} × {CATALOGUE[guess['sku']]}. "
                "Reply CHANGE to amend."
            )
        return "→ clarify", _ask_back(message, "which product they mean")

    if stated and parsed is None:
        # Jev saw a quantity, the parser did not. Do not guess — this disagreement is
        # exactly the case a human should look at, and it is cheap to surface.
        return "→ a person (quantity unclear)", "Flagged: a quantity was implied but not parsed."

    if not stated or parsed is None:
        return "→ clarify", _ask_back(message, "how many they want")

    if kind.confidence < AUTO_CREATE or product.confidence < AUTO_CREATE:
        return "→ review before sending", (
            f"Draft: {parsed} × {CATALOGUE[product.choice]} — held for a human to confirm "
            f"({product.confidence:.2f} on product)."
        )

    # Confident, parsed, agreed. No model wrote this reply; a template did.
    return "order created", (
        f"Order confirmed: {parsed} × {CATALOGUE[product.choice]}"
        + (" — out today." if a["urgency"].score > 2.5 else " — on the next run.")
    )


def _ask_back(message: str, missing: str) -> str:
    """The one branch where a frontier model is worth the round trip."""
    system = (
        "You reply to customers on WhatsApp for a small drinks and catering supplier. "
        f"Write one short message asking {missing}. Match their register — these are "
        "regulars, not strangers. No greeting, no sign-off, under 20 words."
    )
    try:
        if have("OPENAI_API_KEY"):
            return openai_text(system, message)
        if have("ANTHROPIC_API_KEY"):
            return anthropic_text(system, message)
    except MissingKey:
        pass
    return f"[no frontier-model key set — a short message asking {missing} would go here]"


# --------------------------------------------------------------------------------- demo

CUSTOMER = {
    "name": "Pier Cafe (acct 3318)",
    "last_order": {"sku": "still_500", "qty": 4},
    "open_orders": [{"ref": "SO-9912", "status": "picking", "due": "Thursday"}],
}

SAMPLES = [
    "morning — can we get 6 cases of the 500ml still for thursday",
    "same as last time please",
    "how much are the 8oz cups now",
    "hi",
    "the sparkling came short again, 2 cases missing. third time this month.",
    "need cups asap we're out",
]


def main() -> None:
    print(__doc__.split("Run it:")[0])

    for message in SAMPLES:
        rule(f'"{message}"')
        try:
            state = {"message": message, "customer": CUSTOMER["name"],
                     "last_order": CUSTOMER["last_order"], "open_orders": CUSTOMER["open_orders"]}
            a = jev().system_one(state=state, questions=intake_questions(CATALOGUE)).answers
        except Exception as exc:  # noqa: BLE001 — demo ergonomics
            print(f"  could not reach Jev: {exc}")
            return

        show("message type", a["message_type"].choice,
             f"confidence {a['message_type'].confidence:.2f}")
        show("product", a["product"].choice, f"confidence {a['product'].confidence:.2f}")
        show("quantity stated", f"{a['quantity_stated'].noul:.2f}",
             f"regex found {parse_quantity(message)}")
        show("refers to history", f"{a['refers_to_history'].noul:.2f}")
        show("urgency", f"{a['urgency'].score:.2f} / 3")
        show("unhappy", f"{a['unhappy'].noul:.2f}")

        outcome, reply = handle(message, CUSTOMER)
        show("→ outcome", outcome)
        print(wrap(reply, indent="     "))


if __name__ == "__main__":
    main()
