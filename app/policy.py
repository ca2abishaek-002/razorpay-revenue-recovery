"""
Stage 3 — Policy Engine.

Maps a FailureCategory to a fixed, predetermined intervention + timing.
This determinism (a lookup table, not a model call) is what the proposal
means by "no decision is ad hoc" — every action traces back to a named rule.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.models import (
    ActionType,
    ClassificationResult,
    FailureCategory,
    PolicyDecision,
    RawFailedTransaction,
)

MAX_RETRY_ATTEMPTS = int(os.environ.get("MAX_RETRY_ATTEMPTS", "3"))


def _now():
    return datetime.now(timezone.utc)


# rule_id -> (action, delay_from_now, description)
POLICY_TABLE: dict[FailureCategory, tuple[str, ActionType, timedelta, str]] = {
    FailureCategory.INSUFFICIENT_FUNDS: (
        "RULE_INSUFFICIENT_FUNDS_RETRY",
        ActionType.RETRY_CHARGE,
        timedelta(days=3),
        "Retry once, timed three days out, aligned to a likely payday.",
    ),
    FailureCategory.CARD_EXPIRED: (
        "RULE_CARD_EXPIRED_UPDATE_LINK",
        ActionType.SEND_UPDATE_CARD_LINK,
        timedelta(seconds=0),
        "No retry. Send a secure update-card link immediately.",
    ),
    FailureCategory.BANK_TIMEOUT: (
        "RULE_BANK_TIMEOUT_RETRY",
        ActionType.RETRY_CHARGE,
        timedelta(hours=1),
        "Retry after one hour, up to two attempts.",
    ),
    FailureCategory.SOFT_DECLINE: (
        "RULE_SOFT_DECLINE_RETRY",
        ActionType.RETRY_CHARGE,
        timedelta(hours=24),
        "Retry once after 24 hours; notify the customer if it fails again.",
    ),
    FailureCategory.FRAUD_FLAG: (
        "RULE_FRAUD_ESCALATE",
        ActionType.ESCALATE_TO_HUMAN,
        timedelta(seconds=0),
        "Never auto-retried. Escalated immediately to a human reviewer.",
    ),
    FailureCategory.UNRESOLVED_EXHAUSTED: (
        "RULE_EXHAUSTED_CLOSE",
        ActionType.CLOSE_UNRECOVERABLE,
        timedelta(seconds=0),
        "Marked unrecoverable and closed. No further action taken.",
    ),
}


def decide(txn: RawFailedTransaction, classification: ClassificationResult) -> PolicyDecision:
    category = classification.category

    # Hard bound: regardless of category, once the cap is hit we close the case.
    # Fraud is a hard bound in the other direction: never auto-retried no matter what.
    if category != FailureCategory.FRAUD_FLAG and txn.attempt_number > MAX_RETRY_ATTEMPTS:
        rule_id, action, delay, desc = POLICY_TABLE[FailureCategory.UNRESOLVED_EXHAUSTED]
    else:
        rule_id, action, delay, desc = POLICY_TABLE[category]

    scheduled_for = (_now() + delay).isoformat()

    return PolicyDecision(
        transaction_id=txn.transaction_id,
        category=category,
        action=action,
        scheduled_for=scheduled_for,
        retry_attempts_used=txn.attempt_number,
        retry_attempts_cap=MAX_RETRY_ATTEMPTS,
        rule_id=rule_id,
        rule_description=desc,
    )


def decide_batch(
    transactions: list[RawFailedTransaction],
    classifications: list[ClassificationResult],
) -> list[PolicyDecision]:
    by_id = {c.transaction_id: c for c in classifications}
    return [decide(t, by_id[t.transaction_id]) for t in transactions]
