"""
Stage 2 — Classifier.

Classifies each failed transaction's decline reason into exactly one of the
six fixed FailureCategory values. Rule-based matching on decline_code /
decline_reason_raw is the primary, deterministic path (this is what makes
the agent auditable per the proposal). An LLM pass is used only for the
free-text `decline_reason_raw` cases that don't cleanly match a known code,
and its output is still constrained to the same six-category enum and
double-checked with a keyword fallback so a bad LLM call can never produce
an out-of-policy category.
"""
from __future__ import annotations

import os
import re

from app.models import ClassificationResult, FailureCategory, RawFailedTransaction

# Deterministic code -> category map. This is checked first and covers the
# overwhelming majority of real-world decline codes.
CODE_MAP = {
    "BAD_pQ01": FailureCategory.INSUFFICIENT_FUNDS,
    "BAD_pQ02": FailureCategory.CARD_EXPIRED,
    "GW_TIMEOUT": FailureCategory.BANK_TIMEOUT,
    "NETWORK_ERR": FailureCategory.BANK_TIMEOUT,
    "BAD_pQ07": FailureCategory.SOFT_DECLINE,
    "FRAUD_SUSPECTED": FailureCategory.SOFT_DECLINE,  # confirmed via keyword pass below
    "BAD_pQ11": FailureCategory.SOFT_DECLINE,
    "OTP_FAILED": FailureCategory.SOFT_DECLINE,
    "LIMIT_EXCEEDED": FailureCategory.INSUFFICIENT_FUNDS,
}

# Keyword fallback for anything not in CODE_MAP, or to escalate correctly
# from SOFT_DECLINE -> FRAUD_FLAG when the raw reason clearly says so.
KEYWORD_RULES: list[tuple[re.Pattern, FailureCategory]] = [
    (re.compile(r"fraud|stolen|suspicious", re.I), FailureCategory.FRAUD_FLAG),
    (re.compile(r"expired|expiry", re.I), FailureCategory.CARD_EXPIRED),
    (re.compile(r"insufficient|balance|limit exceeded", re.I), FailureCategory.INSUFFICIENT_FUNDS),
    (re.compile(r"timeout|network|gateway error", re.I), FailureCategory.BANK_TIMEOUT),
    (re.compile(r"do not honor|declined by issuing bank|blocked|otp|3d-?secure",
                re.I), FailureCategory.SOFT_DECLINE),
]


def _rule_based_classify(txn: RawFailedTransaction) -> ClassificationResult:
    # 1. Try the exact code map first.
    category = CODE_MAP.get(txn.decline_code)
    rationale = f"decline_code '{txn.decline_code}' matched known code map."

    # 2. Keyword rules can override/confirm (e.g. fraud keyword beats a
    #    generic soft-decline code, since fraud must never be silently retried).
    for pattern, kw_category in KEYWORD_RULES:
        if pattern.search(txn.decline_reason_raw):
            if kw_category == FailureCategory.FRAUD_FLAG or category is None:
                category = kw_category
                rationale = (
                    f"raw reason '{txn.decline_reason_raw}' matched keyword rule "
                    f"-> {kw_category.value}."
                )
            break

    if category is None:
        category = FailureCategory.SOFT_DECLINE
        rationale = "No code or keyword match; defaulted to soft_decline for safety."

    # Attempt-count override: three prior attempts closes the case regardless
    # of the underlying reason.
    if txn.attempt_number > 3:
        category = FailureCategory.UNRESOLVED_EXHAUSTED
        rationale = f"attempt_number={txn.attempt_number} exceeds cap; marked exhausted."

    return ClassificationResult(
        transaction_id=txn.transaction_id,
        category=category,
        confidence=0.95,
        method="rule_based",
        rationale=rationale,
    )


def _llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _llm_classify(txn: RawFailedTransaction, rule_result: ClassificationResult) -> ClassificationResult:
    """Ask the LLM to confirm/refine ambiguous cases. Always falls back safely."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        valid_categories = [c.value for c in FailureCategory]
        prompt = (
            "You are classifying a failed payment for a payment-recovery system. "
            "Return ONLY one of these exact category strings, nothing else:\n"
            f"{valid_categories}\n\n"
            f"Decline code: {txn.decline_code}\n"
            f"Raw reason: {txn.decline_reason_raw}\n"
            f"Attempt number: {txn.attempt_number}\n"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        ).strip()

        # Guardrail: only accept the LLM's answer if it's a legal category value.
        for cat in FailureCategory:
            if cat.value == text:
                return ClassificationResult(
                    transaction_id=txn.transaction_id,
                    category=cat,
                    confidence=0.85,
                    method="llm",
                    rationale=f"LLM classified from raw reason: '{txn.decline_reason_raw}'.",
                )
        # LLM returned something unexpected -> fall back to rule result.
        return rule_result
    except Exception:
        # Any LLM/network failure -> deterministic fallback, never crash the pipeline.
        return rule_result


def classify_transaction(txn: RawFailedTransaction, use_llm: bool = True) -> ClassificationResult:
    rule_result = _rule_based_classify(txn)

    # Only bother calling the LLM for the genuinely ambiguous, low-signal cases
    # (unmapped code AND no keyword hit) — keeps cost/latency down while still
    # exercising the "agentic core" described in the proposal.
    is_ambiguous = txn.decline_code not in CODE_MAP and rule_result.method == "rule_based" \
        and "matched" not in rule_result.rationale.lower()

    if use_llm and is_ambiguous and _llm_available():
        llm_result = _llm_classify(txn, rule_result)
        if llm_result.method == "llm":
            llm_result.method = "llm+rule_fallback"
        return llm_result

    return rule_result


def classify_batch(transactions: list[RawFailedTransaction], use_llm: bool = True) -> list[ClassificationResult]:
    return [classify_transaction(t, use_llm=use_llm) for t in transactions]
