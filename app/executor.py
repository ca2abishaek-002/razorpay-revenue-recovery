"""
Stage 4 — Executor.

Runs the policy's chosen action against Razorpay's TEST-MODE APIs:
  - retry_charge            -> re-attempt via Razorpay Orders/Payments API
  - send_update_card_link   -> create a Razorpay Payment Link for card update
  - notify_customer         -> (stubbed notification hook)
  - escalate_to_human       -> writes to an exception queue, no payment call
  - close_unrecoverable     -> no-op, terminal state

If RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set, every action runs in
SIMULATION MODE: no network call is made, and a realistic Razorpay-shaped
result is fabricated deterministically (retry success probability tuned per
category) so the full pipeline and demo run with zero external credentials.
"""
from __future__ import annotations

import os
import random
import uuid

import requests

from app.models import ActionType, ExecutionResult, PolicyDecision, RawFailedTransaction

RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"

# Simulated retry success rates, tuned to be plausible per failure category —
# used only when running without real Razorpay credentials.
SIMULATED_SUCCESS_RATE = {
    "insufficient_funds": 0.55,   # decent odds a few days later, near payday
    "bank_timeout_or_network_error": 0.70,  # often a transient blip
    "soft_decline_do_not_honor": 0.35,      # issuer-side, less likely to flip
}


def _razorpay_credentials() -> tuple[str, str] | None:
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if key_id and key_secret:
        return key_id, key_secret
    return None


def _simulate_retry(txn: RawFailedTransaction) -> ExecutionResult:
    category_key = None
    # crude reverse lookup by decline code family for a plausible success rate
    if "pQ01" in txn.decline_code or "LIMIT" in txn.decline_code:
        category_key = "insufficient_funds"
    elif txn.decline_code in ("GW_TIMEOUT", "NETWORK_ERR"):
        category_key = "bank_timeout_or_network_error"
    else:
        category_key = "soft_decline_do_not_honor"

    success_rate = SIMULATED_SUCCESS_RATE.get(category_key, 0.4)
    success = random.random() < success_rate

    return ExecutionResult(
        transaction_id=txn.transaction_id,
        action=ActionType.RETRY_CHARGE,
        success=success,
        amount_recovered_paise=txn.amount_paise if success else 0,
        provider_reference=f"pay_sim_{uuid.uuid4().hex[:14]}" if success else None,
        detail="Simulated retry (no Razorpay credentials configured)."
        if success else "Simulated retry failed; will re-evaluate on next attempt.",
        simulated=True,
    )


def _real_retry(txn: RawFailedTransaction, key_id: str, key_secret: str) -> ExecutionResult:
    """Re-attempt the charge via Razorpay's Payment Link creation (test mode).

    In test mode Razorpay does not let you silently re-debit a saved card
    without customer action, so the realistic "retry" implementation is to
    generate a fresh payment link tied to the original order/subscription
    and email/notify the customer. We create the link here and treat
    "link created" as a successful execution step (actual payment
    completion would arrive later via webhook in a production system).
    """
    try:
        resp = requests.post(
            f"{RAZORPAY_BASE_URL}/payment_links",
            auth=(key_id, key_secret),
            json={
                "amount": txn.amount_paise,
                "currency": txn.currency,
                "description": f"Retry for failed payment {txn.transaction_id}",
                "customer": {
                    "email": txn.customer_email,
                },
                "notify": {"sms": False, "email": True},
                "reminder_enable": True,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return ExecutionResult(
                transaction_id=txn.transaction_id,
                action=ActionType.RETRY_CHARGE,
                success=True,
                amount_recovered_paise=0,  # not yet recovered until customer pays
                provider_reference=data.get("id"),
                detail=f"Retry payment link created: {data.get('short_url', '')}",
                simulated=False,
            )
        return ExecutionResult(
            transaction_id=txn.transaction_id,
            action=ActionType.RETRY_CHARGE,
            success=False,
            detail=f"Razorpay API error {resp.status_code}: {resp.text[:200]}",
            simulated=False,
        )
    except requests.RequestException as exc:
        return ExecutionResult(
            transaction_id=txn.transaction_id,
            action=ActionType.RETRY_CHARGE,
            success=False,
            detail=f"Razorpay API request failed: {exc}",
            simulated=False,
        )


def _send_update_card_link(txn: RawFailedTransaction) -> ExecutionResult:
    creds = _razorpay_credentials()
    if not creds:
        return ExecutionResult(
            transaction_id=txn.transaction_id,
            action=ActionType.SEND_UPDATE_CARD_LINK,
            success=True,
            provider_reference=f"plink_sim_{uuid.uuid4().hex[:14]}",
            detail=f"Simulated update-card link sent to {txn.customer_email}.",
            simulated=True,
        )
    key_id, key_secret = creds
    try:
        resp = requests.post(
            f"{RAZORPAY_BASE_URL}/payment_links",
            auth=(key_id, key_secret),
            json={
                "amount": txn.amount_paise,
                "currency": txn.currency,
                "description": f"Update payment method for {txn.transaction_id}",
                "customer": {"email": txn.customer_email},
                "notify": {"sms": False, "email": True},
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return ExecutionResult(
                transaction_id=txn.transaction_id,
                action=ActionType.SEND_UPDATE_CARD_LINK,
                success=True,
                provider_reference=data.get("id"),
                detail=f"Update-card link created: {data.get('short_url', '')}",
                simulated=False,
            )
        return ExecutionResult(
            transaction_id=txn.transaction_id,
            action=ActionType.SEND_UPDATE_CARD_LINK,
            success=False,
            detail=f"Razorpay API error {resp.status_code}: {resp.text[:200]}",
            simulated=False,
        )
    except requests.RequestException as exc:
        return ExecutionResult(
            transaction_id=txn.transaction_id,
            action=ActionType.SEND_UPDATE_CARD_LINK,
            success=False,
            detail=f"Razorpay API request failed: {exc}",
            simulated=False,
        )


def _notify_customer(txn: RawFailedTransaction) -> ExecutionResult:
    # Stubbed notification hook (email/SMS provider would plug in here).
    return ExecutionResult(
        transaction_id=txn.transaction_id,
        action=ActionType.NOTIFY_CUSTOMER,
        success=True,
        detail=f"Notification queued for {txn.customer_email}.",
        simulated=True,
    )


def _escalate(txn: RawFailedTransaction) -> ExecutionResult:
    return ExecutionResult(
        transaction_id=txn.transaction_id,
        action=ActionType.ESCALATE_TO_HUMAN,
        success=True,
        detail="Added to human-review exception queue. No payment action taken.",
        simulated=True,
    )


def _close(txn: RawFailedTransaction) -> ExecutionResult:
    return ExecutionResult(
        transaction_id=txn.transaction_id,
        action=ActionType.CLOSE_UNRECOVERABLE,
        success=True,
        detail="Marked unrecoverable and closed. No further action taken.",
        simulated=True,
    )


def execute(txn: RawFailedTransaction, decision: PolicyDecision) -> ExecutionResult:
    creds = _razorpay_credentials()

    if decision.action == ActionType.RETRY_CHARGE:
        if creds:
            return _real_retry(txn, *creds)
        return _simulate_retry(txn)
    if decision.action == ActionType.SEND_UPDATE_CARD_LINK:
        return _send_update_card_link(txn)
    if decision.action == ActionType.NOTIFY_CUSTOMER:
        return _notify_customer(txn)
    if decision.action == ActionType.ESCALATE_TO_HUMAN:
        return _escalate(txn)
    if decision.action == ActionType.CLOSE_UNRECOVERABLE:
        return _close(txn)

    return ExecutionResult(
        transaction_id=txn.transaction_id,
        action=ActionType.NO_ACTION,
        success=True,
        detail="No action defined.",
        simulated=True,
    )
