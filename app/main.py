"""
FastAPI app for the Failed Payment & Subscription Recovery Agent.

Endpoints:
  POST /api/run-batch        Generate (or accept) a batch of failed txns and
                              run the full 5-stage pipeline over it.
  GET  /api/report           Aggregate recovery-rate metrics + exception list.
  GET  /api/transactions     All ledger rows (most recent per txn).
  GET  /api/transactions/{transaction_id}   Full audit trail for one txn.
  POST /api/reset            Clear the ledger (demo convenience).
  GET  /                     Dashboard UI.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from app import ledger, reporting  # noqa: E402
from app.data_gen import generate_batch  # noqa: E402
from app.models import RawFailedTransaction  # noqa: E402
from app.pipeline import process_batch  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="Failed Payment & Subscription Recovery Agent",
    description="Razorpay AI Buildathon — Track 03: AI Revenue Recovery",
    version="1.0.0",
)


@app.on_event("startup")
def _startup() -> None:
    ledger.init_db()


class RunBatchRequest(BaseModel):
    count: int = 62
    seed: int | None = 42
    use_llm: bool = True


@app.post("/api/run-batch")
def run_batch(req: RunBatchRequest):
    if req.count < 1 or req.count > 5000:
        raise HTTPException(status_code=400, detail="count must be between 1 and 5000")
    transactions: list[RawFailedTransaction] = generate_batch(n=req.count, seed=req.seed)
    entries = process_batch(transactions, use_llm=req.use_llm)
    return {
        "ingested": len(transactions),
        "processed": len(entries),
        "report": reporting.build_report(),
    }


@app.get("/api/report")
def get_report():
    return reporting.build_report()


@app.get("/api/transactions")
def get_transactions():
    rows = ledger.fetch_all()
    latest: dict[str, dict] = {}
    for row in rows:
        latest.setdefault(row["transaction_id"], row)
    return {"count": len(latest), "transactions": list(latest.values())}


@app.get("/api/transactions/{transaction_id}")
def get_transaction(transaction_id: str):
    rows = ledger.fetch_by_transaction(transaction_id)
    if not rows:
        raise HTTPException(status_code=404, detail="transaction not found")
    return {"transaction_id": transaction_id, "audit_trail": rows}


@app.post("/api/reset")
def reset():
    ledger.clear_ledger()
    return {"status": "ledger cleared"}


# --- Dashboard static files ---
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def dashboard():
    index_file = static_dir / "dashboard.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Dashboard not found. See /docs for the API."}
