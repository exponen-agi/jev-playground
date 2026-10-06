"""
14 · Agent handoff, decided on every turn
=========================================

    customer turn ─▶ Jev ─▶ stay with the current agent
                         ─▶ hand off: concierge · flights · hotels · refunds
                         ─▶ a person

A travel assistant made of specialists: a concierge that answers general questions, and
agents that can actually change flights, change hotels, and issue refunds. Each one has its
own narrow prompt and its own tools. The hard part is not any one agent. It is the moment
between turns where someone has to decide whether the conversation still belongs to the
agent that has it.

Get that wrong in one direction and the customer is passed back and forth: "what time is
check-in?" sends a flight conversation to the hotel agent, and the next message sends it
back. Get it wrong in the other and the hotel agent keeps a refund request it has no tool
for, and says something vague.

Per turn, one Jev call:

    topic           Choice over the agents — with probabilities, so code can compare the
                    current owner's probability with the challenger's
    mid_task        is the customer answering a question the current agent just asked?
    wants_human     are they asking for a person, in any words?
    frustration     Score; escalate before they have to ask

The handoff rule is the interesting part, and it is three lines of code, not a prompt:

    hand off only if  P(challenger) − P(current owner) > HANDOFF_MARGIN
                and   the customer is not mid-answer

That margin is hysteresis. It is how a thermostat avoids switching on and off around the
set point, and it works the same way here. Only a calibrated probability makes that rule
meaningful; a model that says "hotels" with no number gives you nothing to compare.

Run it:  python cookbooks/14_agent_handoff.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from typesafe_sdk import Choice, Noul, Score

from _shared import Agent, jev, rule, show, wrap

AGENTS = {
    "concierge": "General travel questions, destinations, anything not about changing a booking",
    "flights":   "Changing, rebooking or asking about flights, seats, baggage, check-in for a flight",
    "hotels":    "Changing or asking about hotel bookings, rooms, dates, hotel check-in",
    "refunds":   "Money back: refunds, duplicate charges, deposits, cancellations for a refund",
}

TURN = {
    "topic": Choice(
        instructions="Which agent should handle `message`, given `recent` for context",
        criteria=AGENTS,
    ),
    "mid_task": Noul(
        instructions="`message` answers a question the agent asked in the last line of `recent`",
    ),
    "wants_human": Noul(
        instructions="The customer asks to speak to a person, in any words",
    ),
    "frustration": Score(
        instructions="How frustrated the customer is in `message`",
        criteria=["Calm", "Impatient but civil", "Angry, or repeating themselves"],
    ),
}

HANDOFF_MARGIN = 0.15      # the challenger must beat the owner by this much
MID_TASK_BAR = 0.70        # don't take the conversation from an agent that is mid-question
WANTS_HUMAN_BAR = 0.70
FRUSTRATION_BAR = 1.6      # past "impatient but civil"

PERSON = "person"


def next_owner(owner: str, recent: list[str], message: str) -> tuple[str, str, dict]:
    a = jev().system_one(
        state={"current_agent": owner, "recent": recent[-4:], "message": message},
        questions=TURN,
    ).answers

    if a["wants_human"].noul > WANTS_HUMAN_BAR:
        return PERSON, "asked for a person", a
    if a["frustration"].score > FRUSTRATION_BAR:
        return PERSON, f"frustration {a['frustration'].score:.2f}", a

    topic = a["topic"]
    challenger = topic.choice
    if challenger == owner:
        return owner, "stays", a

    lead = topic.probabilities[challenger] - topic.probabilities.get(owner, 0.0)
    if a["mid_task"].noul > MID_TASK_BAR:
        return owner, f"stays: customer is answering {owner}", a
    if lead <= HANDOFF_MARGIN:
        return owner, f"stays: {challenger} leads by only {lead:.2f}", a
    return challenger, f"hand off: {challenger} leads by {lead:.2f}", a


# --------------------------------------------------------------------------------- demo

CONVERSATION = [
    "Hi, I need to change my flight to Lisbon next Friday.",
    "Can I go on the Thursday instead, same time?",
    "Great. Does the Thursday flight include a checked bag?",
    "Hmm, and what time is check-in?",
    "I mean for the flight — online, or at the airport?",
    "OK, done. Now my hotel: I'll need the Wednesday night too.",
    "Is it the same room type?",
    "The hotel charged my card twice for the deposit. Can I get one back?",
    "It's €120. I can see both charges on my statement.",
    "Thanks. What's the weather like in Lisbon in May?",
    "Actually, I'd rather just talk to someone.",
]

REPLY = {name: f"You are the {name} agent for a travel company. {desc}. Reply in two sentences."
         for name, desc in AGENTS.items()}


def main() -> None:
    print(__doc__.split("Run it:")[0])
    rule("One conversation, eleven turns")

    owner, recent, handoffs = "concierge", [], 0
    agents = {name: Agent(name, prompt, [f"[{name} replies]"]) for name, prompt in REPLY.items()}

    for message in CONVERSATION:
        try:
            new_owner, why, a = next_owner(owner, recent, message)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not reach Jev: {exc}")
            return
        if new_owner != owner:
            handoffs += 1
        owner = new_owner
        print(f"\n  customer: {message}")
        show(f"  → {owner}", why, f"topic {a['topic'].choice} {a['topic'].confidence:.2f}")
        if owner == PERSON:
            break
        reply, _ = agents[owner].run(message)
        recent += [f"customer: {message}", f"{owner}: {reply}"]

    rule("What it cost")
    show("handoffs", handoffs)
    show("routing decisions", len(recent) // 2 + (owner == PERSON), "one Jev call per turn")
    print()
    print(wrap(
        "Turn four is the trap: 'check-in' is a hotel word, but the customer is still on the "
        "flight. A margin of zero hands it to hotels and turn five hands it straight back. "
        "Turn eight is the opposite: refunds should win, even though the word 'hotel' is in it."))


if __name__ == "__main__":
    main()
