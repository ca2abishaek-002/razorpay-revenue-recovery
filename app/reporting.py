"""
Aggregates ledger rows into the recovery-rate metrics and exception list
described in the proposal, and generates the single defensible demo
statement live from the ledger (never scripted/hardcoded).
"""
from __future__ import annotations

from app import ledger


def build_report(db_path: str | None = None) -> dict:
    rows = ledger.fetch_all(db_path)

    # Deduplicate to the latest ledger entry per transaction (a transaction
    # may have multiple entries if retried across pipeline runs).
    latest_by_txn: dict[str, dict] = {}
    for row in rows:  # rows are ordered DESC by timestamp, so first-seen wins
        latest_by_txn.setdefault(row["transaction_id"], row)

    entries = list(latest_by_txn.values())
    total_count = len(entries)
    total_amount_paise = sum(e["amount_paise"] for e in entries)
    recovered_amount_paise = sum(
        e["amount_recovered_paise"] for e in entries if e["status"] == "recovered"
    )

    recovered = [e for e in entries if e["status"] == "recovered"]
    pending = [e for e in entries if e["status"] == "pending_retry"]
    unrecoverable = [e for e in entries if e["status"] == "unrecoverable"]
    escalated = [e for e in entries if e["status"] == "escalated"]

    recovery_rate = (recovered_amount_paise / total_amount_paise * 100) if total_amount_paise else 0.0

    by_category: dict[str, int] = {}
    for e in entries:
        by_category[e["category"]] = by_category.get(e["category"], 0) + 1

    exception_list = [
        {
            "transaction_id": e["transaction_id"],
            "status": e["status"],
            "category": e["category"],
            "amount_paise": e["amount_paise"],
            "customer_email": e["raw_transaction"].get("customer_email"),
            "detail": e["execution_result"].get("detail"),
        }
        for e in entries
        if e["status"] in ("unrecoverable", "escalated", "pending_retry")
    ]

    summary_statement = (
        f"Of {total_count} failed payments totalling INR "
        f"{total_amount_paise / 100 / 1e5:.1f} lakh, the agent recovered INR "
        f"{recovered_amount_paise / 100 / 1e5:.1f} lakh — {recovery_rate:.0f} percent — "
        f"through automated retries and update-card nudges, correctly identified "
        f"{len(unrecoverable)} transactions as unrecoverable, and escalated "
        f"{len(escalated)} fraud-flagged cases without touching them."
    )

    return {
        "total_transactions": total_count,
        "total_amount_paise": total_amount_paise,
        "total_amount_rupees": round(total_amount_paise / 100, 2),
        "recovered_amount_paise": recovered_amount_paise,
        "recovered_amount_rupees": round(recovered_amount_paise / 100, 2),
        "recovery_rate_percent": round(recovery_rate, 1),
        "counts": {
            "recovered": len(recovered),
            "pending_retry": len(pending),
            "unrecoverable": len(unrecoverable),
            "escalated": len(escalated),
        },
        "by_category": by_category,
        "exception_list": exception_list,
        "summary_statement": summary_statement,
    }
