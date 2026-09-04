"""
Orchestrates the full detect -> decide -> execute -> audit loop across a
batch of failed transactions, writing one ledger entry per transaction.
"""
from __future__ import annotations

from app import ledger
from app.classifier import classify_transaction
from app.executor import execute
from app.models import (
    ActionType,
    FailureCategory,
    LedgerEntry,
    RawFailedTransaction,
)
from app.policy import decide


def _status_for(category: FailureCategory, action: ActionType, exec_success: bool) -> str:
    if category == FailureCategory.FRAUD_FLAG:
        return "escalated"
    if category == FailureCategory.UNRESOLVED_EXHAUSTED:
        return "unrecoverable"
    if action == ActionType.RETRY_CHARGE:
        return "recovered" if exec_success else "pending_retry"
    if action == ActionType.SEND_UPDATE_CARD_LINK:
        return "pending_retry"
    return "closed"


def process_transaction(txn: RawFailedTransaction, use_llm: bool = True) -> LedgerEntry:
    classification = classify_transaction(txn, use_llm=use_llm)
    decision = decide(txn, classification)
    result = execute(txn, decision)
    status = _status_for(classification.category, decision.action, result.success)

    entry = LedgerEntry(
        entry_id=ledger.new_entry_id(),
        transaction_id=txn.transaction_id,
        raw_transaction=txn,
        classification=classification,
        policy_decision=decision,
        execution_result=result,
        status=status,
    )
    ledger.append_entry(entry)
    return entry


def process_batch(transactions: list[RawFailedTransaction], use_llm: bool = True) -> list[LedgerEntry]:
    return [process_transaction(t, use_llm=use_llm) for t in transactions]
