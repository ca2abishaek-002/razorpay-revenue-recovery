"""
Stage 5 — Ledger.

Single append-only audit ledger (SQLite). Every pipeline pass for every
transaction writes one row here, storing the full raw_transaction /
classification / policy_decision / execution_result JSON blobs alongside
a few indexed summary columns, so the full decision trail is
reconstructable for any transaction at any time (per the proposal's
"Audited" requirement).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.models import LedgerEntry

DB_PATH = os.environ.get("LEDGER_DB_PATH", "./data/ledger.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    entry_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    amount_recovered_paise INTEGER NOT NULL,
    raw_transaction_json TEXT NOT NULL,
    classification_json TEXT NOT NULL,
    policy_decision_json TEXT NOT NULL,
    execution_result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_txn ON ledger(transaction_id);
CREATE INDEX IF NOT EXISTS idx_ledger_status ON ledger(status);
"""


def init_db(db_path: str | None = None) -> None:
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _connect(db_path: str | None = None):
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def append_entry(entry: LedgerEntry, db_path: str | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ledger (
                entry_id, transaction_id, timestamp, status, category, action,
                amount_paise, amount_recovered_paise,
                raw_transaction_json, classification_json,
                policy_decision_json, execution_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                entry.transaction_id,
                entry.timestamp,
                entry.status,
                entry.classification.category.value,
                entry.execution_result.action.value,
                entry.raw_transaction.amount_paise,
                entry.execution_result.amount_recovered_paise,
                entry.raw_transaction.model_dump_json(),
                entry.classification.model_dump_json(),
                entry.policy_decision.model_dump_json(),
                entry.execution_result.model_dump_json(),
            ),
        )


def new_entry_id() -> str:
    return f"ledger_{uuid.uuid4().hex[:16]}"


def fetch_all(db_path: str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM ledger ORDER BY timestamp DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def fetch_by_transaction(transaction_id: str, db_path: str | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ledger WHERE transaction_id = ? ORDER BY timestamp DESC",
            (transaction_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "entry_id": row["entry_id"],
        "transaction_id": row["transaction_id"],
        "timestamp": row["timestamp"],
        "status": row["status"],
        "category": row["category"],
        "action": row["action"],
        "amount_paise": row["amount_paise"],
        "amount_recovered_paise": row["amount_recovered_paise"],
        "raw_transaction": json.loads(row["raw_transaction_json"]),
        "classification": json.loads(row["classification_json"]),
        "policy_decision": json.loads(row["policy_decision_json"]),
        "execution_result": json.loads(row["execution_result_json"]),
    }


def clear_ledger(db_path: str | None = None) -> None:
    """Used by tests / demo reset. Not exposed on the public API by default."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM ledger")
