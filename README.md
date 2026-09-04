# Failed Payment & Subscription Recovery Agent

**Razorpay AI Buildathon — Track 03: AI Revenue Recovery**

An agent that closes the revenue-recovery loop end to end: detect a failed
payment, diagnose why it happened, choose the correct intervention, execute
it, and prove — with real numbers, pulled live from an audit ledger — how
much revenue it recovered.

## Architecture

Five stages, each writing to a single append-only audit ledger:

| Stage | File | Function |
|---|---|---|
| 1. Ingestion | `app/data_gen.py` | Produces a normalized batch of failed payment/subscription records (synthetic, but realistic decline codes/amounts/timestamps) |
| 2. Classifier | `app/classifier.py` | Rule-based decision tree + optional LLM pass, constrained to 6 fixed categories |
| 3. Policy engine | `app/policy.py` | Deterministic lookup table: category → action + timing, with retry caps and fraud carve-out |
| 4. Executor | `app/executor.py` | Runs the action against Razorpay **test-mode** APIs (Payment Links), or a deterministic simulation if no keys are configured |
| 5. Ledger & reporting | `app/ledger.py`, `app/reporting.py` | SQLite audit trail; aggregates into recovery-rate metrics + exception list |

`app/pipeline.py` orchestrates all five stages per transaction and writes one
ledger row per pass. `app/main.py` is the FastAPI service exposing it all,
with `static/dashboard.html` as a lightweight live dashboard.

### Failure categories & policy

| Category | Action | Timing |
|---|---|---|
| Insufficient funds | Retry once | 3 days out |
| Card expired | Send update-card link | Immediate, no retry |
| Bank timeout / network error | Retry, up to 2 attempts | 1 hour |
| Soft decline (do-not-honor) | Retry once, notify if it fails again | 24 hours |
| Fraud flag | Escalate to human | Never auto-retried |
| Unresolved after 3 attempts | Close as unrecoverable | No further action |

This table (`POLICY_TABLE` in `app/policy.py`) is the entire decision
surface — every action a transaction receives traces back to exactly one of
these six rules, which is what makes the agent auditable rather than a
black box.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in keys, or leave blank — see below
```

### Running without any credentials (fully self-contained demo)

Leave `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` and `ANTHROPIC_API_KEY` blank
in `.env`. The executor automatically falls back to **simulation mode**
(deterministic, plausible success rates per category) and the classifier
falls back to the **rule-based** path only. The full pipeline, API, and
dashboard all work with zero external accounts.

### Running with real Razorpay test-mode keys

Get test-mode keys from the [Razorpay dashboard](https://dashboard.razorpay.com/app/keys)
(toggle to Test Mode) and set them in `.env`. Retries and update-card actions
will then create real Razorpay Payment Links in test mode instead of being
simulated.

### Running with LLM classification

Set `ANTHROPIC_API_KEY` in `.env`. The classifier only calls the LLM for the
genuinely ambiguous cases (unmapped decline code with no keyword match) —
everything else stays on the deterministic rule path. The LLM's output is
still validated against the fixed 6-category enum before being accepted, so
a malformed response can never produce an out-of-policy action.

## Run it

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` for the dashboard, or `http://localhost:8000/docs`
for the interactive API docs.

## Demo script (for the 5-minute video)

1. Open the dashboard. Click **Run batch** (defaults: 62 transactions, seed 42
   — mirrors the target scenario in the proposal).
2. Point out the **live summary statement** at the top — it's generated from
   `reporting.build_report()` reading straight out of the SQLite ledger, not
   hardcoded.
3. Scroll the **transaction ledger** table — every row shows category, action
   taken, amount, and final status.
4. Click into the **exception list** — the transactions the agent could not
   recover, reported honestly alongside the recovery rate.
5. Hit `GET /api/transactions/{id}` in `/docs` on any transaction to show the
   full audit trail: raw record → classification → policy decision →
   execution result, all timestamped.

## API reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/run-batch` | Body: `{count, seed, use_llm}`. Generates a batch and runs the full pipeline. |
| `GET` | `/api/report` | Current aggregate metrics + exception list. |
| `GET` | `/api/transactions` | Latest ledger row per transaction. |
| `GET` | `/api/transactions/{id}` | Full audit trail for one transaction. |
| `POST` | `/api/reset` | Clears the ledger (demo convenience). |

## Project layout

```
app/
  models.py       Pydantic models shared across all stages
  data_gen.py      Stage 1 — synthetic batch generator
  classifier.py    Stage 2 — rule-based + optional LLM classification
  policy.py        Stage 3 — deterministic policy table
  executor.py      Stage 4 — Razorpay test-mode API calls / simulation
  ledger.py        Stage 5 — SQLite audit ledger
  reporting.py     Aggregation + live summary statement
  pipeline.py      Orchestrates stages 2–5 per transaction
  main.py          FastAPI app
static/
  dashboard.html   Live dashboard (vanilla HTML/JS, no build step)
data/
  ledger.db        SQLite database (created on first run)
requirements.txt
.env.example
```

## Design choices worth calling out in review

- **Determinism over cleverness.** The policy engine is a lookup table, not
  a model call — every action is explainable back to a named rule
  (`rule_id` + `rule_description` on every ledger row).
- **Fraud is a hard boundary, not a soft preference.** It's checked before
  the retry-cap logic and can never be overridden by attempt count or LLM
  output.
- **Honest reporting.** The exception list (unrecoverable + escalated +
  still-pending) is always computed and shown alongside the recovery rate —
  there's no code path that reports recovery rate without it.
- **Graceful degradation.** Every external dependency (Razorpay API, LLM
  API) has a deterministic fallback, so the system is always demoable and
  never crashes the pipeline on a network or auth failure.
