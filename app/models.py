"""
Core data models for the Failed Payment & Subscription Recovery Agent.

These are deliberately simple, JSON-serializable dataclasses/Pydantic models
so every stage of the pipeline (ingestion -> classifier -> policy ->
executor -> ledger) can pass the same shapes around and log them verbatim
to the audit ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FailureCategory(str, Enum):
    """The six fixed categories every failed transaction is classified into."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_EXPIRED = "card_expired"
    BANK_TIMEOUT = "bank_timeout_or_network_error"
    SOFT_DECLINE = "soft_decline_do_not_honor"
    FRAUD_FLAG = "fraud_flag"
    UNRESOLVED_EXHAUSTED = "unresolved_after_three_attempts"


class ActionType(str, Enum):
    RETRY_CHARGE = "retry_charge"
    SEND_UPDATE_CARD_LINK = "send_update_card_link"
    NOTIFY_CUSTOMER = "notify_customer"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    CLOSE_UNRECOVERABLE = "close_unrecoverable"
    NO_ACTION = "no_action"


class TransactionSource(str, Enum):
    ONE_TIME_PAYMENT = "one_time_payment"
    SUBSCRIPTION = "subscription"


class RawFailedTransaction(BaseModel):
    """Stage 1 output shape: a normalized failed payment/subscription record."""

    transaction_id: str
    source: TransactionSource
    customer_id: str
    customer_email: str
    amount_paise: int  # amount in the smallest currency unit (paise for INR)
    currency: str = "INR"
    decline_code: str
    decline_reason_raw: str
    failed_at: str
    attempt_number: int = 1
    razorpay_payment_id: Optional[str] = None
    razorpay_subscription_id: Optional[str] = None

    @property
    def amount_rupees(self) -> float:
        return self.amount_paise / 100


class ClassificationResult(BaseModel):
    """Stage 2 output shape."""

    transaction_id: str
    category: FailureCategory
    confidence: float
    method: str  # "rule_based" or "llm" or "llm+rule_fallback"
    rationale: str


class PolicyDecision(BaseModel):
    """Stage 3 output shape."""

    transaction_id: str
    category: FailureCategory
    action: ActionType
    scheduled_for: str  # ISO timestamp; "now" actions still get a concrete ts
    retry_attempts_used: int
    retry_attempts_cap: int
    rule_id: str
    rule_description: str


class ExecutionResult(BaseModel):
    """Stage 4 output shape."""

    transaction_id: str
    action: ActionType
    success: bool
    amount_recovered_paise: int = 0
    provider_reference: Optional[str] = None
    detail: str = ""
    executed_at: str = Field(default_factory=utcnow_iso)
    simulated: bool = True


class LedgerEntry(BaseModel):
    """A single append-only audit record covering one pipeline pass for one txn."""

    entry_id: str
    transaction_id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    raw_transaction: RawFailedTransaction
    classification: ClassificationResult
    policy_decision: PolicyDecision
    execution_result: ExecutionResult
    status: str  # "recovered" | "pending_retry" | "escalated" | "unrecoverable" | "closed"
