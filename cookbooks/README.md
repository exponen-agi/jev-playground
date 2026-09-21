# Cookbooks

Six real-life patterns for using Jev *with* the models you already run — not instead of them.

Every script is runnable as-is against its built-in sample inputs, and marks the boundary
between the Jev part and the frontier-model part. The Jev half runs with only
`TYPESAFE_API_KEY` set; the other half prints a placeholder and tells you which key it
wanted.

Prefer to watch first? Each cookbook has an animated counterpart in
[`../simulations/`](../simulations) that needs no keys and no Python.

```bash
pip install -r ../requirements.txt
cp ../.env.example ../.env   # add TYPESAFE_API_KEY at minimum
python 01_triage_cascade.py
```

---

## The six

| # | Pattern | Jev's job | The LLM's job | Needs |
|---|---|---|---|---|
| **[01](./01_triage_cascade.py)** · [▶](../simulations/01-triage-cascade.html) | **Support triage cascade** | Classify intent + rate complexity, in one call | Answer, from a *narrow* specialist prompt — but only on two of five branches | `ANTHROPIC` or `OPENAI` |
| **[02](./02_rag_gatekeeper.py)** · [▶](../simulations/02-rag-gatekeeper.html) | **RAG gatekeeper** | Score every retrieved passage for relevance, contradiction, and prompt injection | Answer from the survivors, with citations | `ANTHROPIC` or `OPENAI` |
| **[03](./03_agent_guardrails.py)** · [▶](../simulations/03-agent-guardrails.html) | **Tool-call gating** | Judge every proposed tool call on four independent hazards | — (this sits *in front of* whatever agent you run) | none |
| **[04](./04_output_verifier.py)** · [▶](../simulations/04-output-verifier.html) | **LLM output verifier** | Judge the draft for grounding, invention, promises, hedging, and tone | Generate the draft, and revise it on style failures | `OPENAI` or `ANTHROPIC` |
| **[05](./05_realtime_lead_scoring.py)** · [▶](../simulations/05-lead-scoring.html) | **Real-time scoring** | Four weighted scores + spam + territory, inside a form-submit request | Write the first-touch email — tier A only | `OPENAI` or `ANTHROPIC` |
| **[06](./06_model_router.py)** · [▶](../simulations/06-model-router.html) | **Cross-provider router** | Decide what the request *needs*, then pick the tier | Whichever of GPT / Claude / Gemini the axes selected | any / all |

---

## What they all have in common

```python
# 1. CODE assembles the state. Precisely. Only what the question needs.
state = build_state(request)

# 2. JEV makes the judgments. All of them, in one call.
answers = client.system_one(state=state, questions=QUESTIONS).answers

# 3. CODE owns the control flow, the thresholds, and the side effects.
if answers["needs_generation"].noul > 0.7 and answers["route"].confidence > 0.8:
    # 4. The LLM does the one thing only an LLM can do.
    reply = llm.generate(SPECIALISTS[answers["route"].choice], state)
```

Four habits show up in every one of them, and they are the transferable part:

### 1. Ask everything up front

Adding a question barely changes response time and costs only that question's tokens.
Cookbook 01 asks two questions that are only meaningful on branches it may not take —
because asking speculatively is cheaper than a second round trip. TypeSafe calls this
**speculative fan-out**; their published measurement is **12.2× cheaper and 10.0× faster**
for a 13-question batch versus one call each.

### 2. One judgment per question

Cookbook 03 asks four separate `Noul`s rather than one "is this safe?" A composite
question hides several decisions inside a number you cannot act on differently — and it
means you cannot tune the exfiltration bar without moving the destructive bar.

### 3. One threshold per action, scaled to what being wrong costs

Not one global threshold for the system. In cookbook 03, `exfiltrates` is gated at `0.5`
and `off_task` at `0.7`, because data leaving the building is not recoverable and a
wasted tool call is. Every threshold in these files is a named constant sitting next to
the action it guards.

### 4. Keep the arithmetic in code

Cookbook 05 computes its composite score with a Python expression, not with a "rate this
lead 1-10" question. Jev is explicitly not a calculator, and — more importantly — weights
in code have a diff, a reviewer, a test, and a revert.

---

## Things these examples deliberately do *not* do

| Not doing | Why |
|---|---|
| Ask Jev to count, add, or compare dates | It reads dates as text and recognises the shape of a count rather than tallying. Extract with a `Choice` over enumerated options, compare in code. |
| Ask Jev to generate text | It is not trained to. That is what the LLM half is for. |
| Send the whole document when one field would do | Context rot: accuracy falls as the state fills with material the question doesn't need. |
| Use `jev-latest` | Aliases move. Every client here pins `jev-1.13.0` via `TYPESAFE_MODEL`. |
| Carry a threshold from a `Noul` to a `Choice` | They answer different questions. A `Choice` is relative; a `Noul` is absolute and can be low for every option. |
| Trust the state | Cookbook 02 screens retrieved passages for injected instructions. State is data, and Jev does not treat it as hostile by default. |

---

## Rough cost shape

Using TypeSafe's published figures — **$0.042 / MTok input, output free** — at a
1,000-token state:

| Volume | Jev cost | What the LLM half costs |
|---|---|---|
| 1,000 calls | ~$0.04 | depends entirely on how many you avoid |
| 1,000,000 calls | ~$42 | this is the number that changes your architecture |

For cookbook 01 specifically, the article's worked figure: a million tickets through the
cascade costs roughly **$6,480** against **$30,400** for routing everything through a
frontier model, with around 800,000 answered in under half a second instead of ten.

The saving is real, but it is the second-order benefit. The first-order benefit is that
the escalation path is driven by a calibrated number instead of a heuristic.

> These are vendor-reported figures. Run cookbook 01 in shadow mode against a week of
> your own traffic before you quote any of them internally.

---

## Extending these

- **Async.** Every example uses the synchronous client for readability. Swap in
  `AsyncTypeSafeClient` and `asyncio.gather` for the per-item loops in cookbooks 02 and 06.
- **Shadow mode.** Wrap any `handle()` / `route()` function so it logs Jev's answer and
  confidence next to the incumbent's, and returns the incumbent's. That log is how you
  build the calibration curve.
- **The calibration curve.** Bucket by confidence, measure accuracy per bucket on your own
  labelled data. It is the artefact that tells you where the automation threshold goes,
  and the one that gets sign-off from whoever owns the risk.
