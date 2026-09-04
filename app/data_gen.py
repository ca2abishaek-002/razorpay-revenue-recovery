"""
Synthetic failed-payment batch generator.

Produces realistic decline codes, amounts, and timestamps so the pipeline
can be demoed and tested without needing proprietary fraud-labeled data,
per the proposal's "data we can legitimately synthesize" argument.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from app.models import RawFailedTransaction, TransactionSource

# Decline codes loosely modeled on real payment-gateway vocabulary.
# Each maps to a realistic distribution bucket so the resulting batch
# resembles what a merchant would actually see.
DECLINE_PROFILES = [
    # (decline_code, raw_reason, weight)
    ("BAD_pQ01", "Insufficient balance in account", 22),
    ("BAD_pQ02", "Card has expired", 12),
    ("GW_TIMEOUT", "Gateway timeout while contacting issuing bank", 10),
    ("NETWORK_ERR", "Network error, please try again", 8),
    ("BAD_pQ07", "Payment declined by issuing bank (do not honor)", 20),
    ("FRAUD_SUSPECTED", "Transaction flagged for suspected fraud", 6),
    ("BAD_pQ11", "Card blocked by issuer", 9),
    ("OTP_FAILED", "3D-Secure/OTP authentication failed", 8),
    ("LIMIT_EXCEEDED", "Transaction exceeds card limit", 5),
]

FIRST_NAMES = ["Aarav", "Vivaan", "Ananya", "Diya", "Ishaan", "Priya", "Rohan",
               "Kavya", "Arjun", "Meera", "Karan", "Sneha", "Aditya", "Neha",
               "Rahul", "Pooja", "Vikram", "Anjali", "Sanjay", "Riya"]
LAST_NAMES = ["Sharma", "Verma", "Iyer", "Nair", "Patel", "Reddy", "Gupta",
              "Menon", "Rao", "Singh", "Das", "Mehta", "Kulkarni", "Joshi"]


def _weighted_choice(profiles):
    total = sum(w for _, _, w in profiles)
    r = random.uniform(0, total)
    upto = 0
    for code, reason, w in profiles:
        upto += w
        if upto >= r:
            return code, reason
    return profiles[-1][0], profiles[-1][1]


def generate_batch(n: int = 62, seed: int | None = 42) -> list[RawFailedTransaction]:
    """Generate n synthetic failed transactions.

    Default n=62 mirrors the target demo scenario in the proposal
    (62 failed payments totalling ~INR 4.1 lakh).
    """
    if seed is not None:
        random.seed(seed)

    now = datetime.now(timezone.utc)
    batch: list[RawFailedTransaction] = []

    for i in range(n):
        code, reason = _weighted_choice(DECLINE_PROFILES)
        source = random.choice(
            [TransactionSource.ONE_TIME_PAYMENT, TransactionSource.SUBSCRIPTION]
        )
        # Amount distribution: mostly small-to-mid subscription/ecommerce tickets,
        # occasional larger one-time payments. Tuned so ~62 txns land near INR 4.1L.
        if source == TransactionSource.SUBSCRIPTION:
            amount_rupees = round(random.choice([299, 499, 799, 999, 1499, 1999]) *
                                   random.uniform(0.9, 1.1))
        else:
            amount_rupees = round(random.uniform(500, 15000))

        failed_at = now - timedelta(
            hours=random.uniform(0, 96), minutes=random.uniform(0, 59)
        )
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        email_local = name.lower().replace(" ", ".")
        customer_id = f"cust_{uuid.uuid4().hex[:10]}"

        txn = RawFailedTransaction(
            transaction_id=f"txn_{uuid.uuid4().hex[:12]}",
            source=source,
            customer_id=customer_id,
            customer_email=f"{email_local}{random.randint(1,99)}@example.com",
            amount_paise=int(amount_rupees * 100),
            currency="INR",
            decline_code=code,
            decline_reason_raw=reason,
            failed_at=failed_at.isoformat(),
            attempt_number=1,
            razorpay_payment_id=f"pay_{uuid.uuid4().hex[:14]}",
            razorpay_subscription_id=(
                f"sub_{uuid.uuid4().hex[:14]}"
                if source == TransactionSource.SUBSCRIPTION else None
            ),
        )
        batch.append(txn)

    return batch


if __name__ == "__main__":
    import json

    txns = generate_batch()
    total = sum(t.amount_paise for t in txns) / 100
    print(f"Generated {len(txns)} transactions totalling INR {total:,.2f}")
    print(json.dumps([t.model_dump() for t in txns[:3]], indent=2))
