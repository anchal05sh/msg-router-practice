#!/usr/bin/env python3
"""Route inbox messages by combining independent trust and urgency signals."""

from __future__ import annotations

import csv
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parent / "dataset"
MESSAGES_PATH = DATASET_DIR / "messages.csv"
BUSINESS_PATH = DATASET_DIR / "business_accounts.csv"
OUTPUT_PATH = Path(__file__).resolve().parent / "result.csv"

HIGH_REPORTS_THRESHOLD = 10
URGENCY_KEYWORDS = (
    "urgent",
    "immediately",
    "verify now",
    "suspended",
    "suspend",
    "click",
    "otp",
    "kyc",
    "act now",
    "limited time",
    "account will",
)


def load_business_accounts(path: Path) -> dict[str, dict[str, str]]:
    accounts: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            accounts[row["business_name"].strip()] = row
    return accounts


def load_messages(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def is_high_reports(value: str) -> bool:
    try:
        return int(value) >= HIGH_REPORTS_THRESHOLD
    except (TypeError, ValueError):
        return False


def find_urgency_keywords(text: str) -> list[str]:
    lowered = text.lower()
    return [kw for kw in URGENCY_KEYWORDS if kw in lowered]


def evaluate_business_domain_trust(
    sender_name: str,
    sender_type: str,
    businesses: dict[str, dict[str, str]],
) -> tuple[list[str], list[str], list[str]]:
    """Return (legitimate_signals, suspicious_signals, evidence) for domain trust."""
    legitimate: list[str] = []
    suspicious: list[str] = []
    evidence: list[str] = []

    account = businesses.get(sender_name)
    if account is None:
        if sender_type == "business":
            suspicious.append("unlisted business sender")
            evidence.append("business_account=missing")
        return legitimate, suspicious, evidence

    official = account["official_domain"].strip().lower()
    used = account["domain_used_by_sender"].strip().lower()
    verified = account["verified"].strip()
    reports = account["user_reports_30d"].strip()
    domain_mismatch = used != official
    unverified_high_reports = verified == "0" and is_high_reports(reports)
    verified_match = (not domain_mismatch) and verified == "1" and not is_high_reports(reports)

    evidence.extend(
        [
            f"official_domain={official}",
            f"domain_used_by_sender={used}",
            f"verified={verified}",
            f"user_reports_30d={reports}",
        ]
    )

    if domain_mismatch:
        suspicious.append("domain mismatch")
        evidence.append("domain_mismatch=true")
    if unverified_high_reports:
        suspicious.append("unverified high reports")
        evidence.append("unverified_high_reports=true")
    if verified_match:
        legitimate.append("verified business sender")
        evidence.append("business_trust=verified")

    return legitimate, suspicious, evidence


def evaluate_urgency_keywords(text: str) -> tuple[list[str], list[str]]:
    """Return (suspicious_signals, evidence) from urgency-keyword detection."""
    keywords = find_urgency_keywords(text)
    if not keywords:
        return [], ["urgency_keywords=none"]
    return (
        ["urgency language"],
        ["urgency_keywords=" + ",".join(keywords)],
    )


def combine_signals(
    sender_type: str,
    quiet: bool,
    legit_trust: list[str],
    sus_trust: list[str],
    sus_urgency: list[str],
) -> tuple[str, str]:
    legitimate = list(legit_trust)
    if sender_type in {"family", "group"}:
        legitimate.append("family/group sender")

    suspicious = sus_trust + sus_urgency
    has_legit = bool(legitimate)
    has_sus = bool(suspicious)

    if has_legit and has_sus:
        action = "wait"
        reason = (
            "conflict: legitimate signals ("
            + ", ".join(legitimate)
            + ") vs suspicious signals ("
            + ", ".join(suspicious)
            + ")"
        )
        if quiet:
            reason += "; quiet hours"
        return action, reason

    if has_sus:
        if sus_trust and not sus_urgency:
            return "muted", "suspicious sender"
        if sus_urgency and not sus_trust:
            return "muted", "likely scam"
        return "muted", "suspicious sender"

    if quiet:
        return "wait", "quiet hours"

    if sender_type in {"family", "group"}:
        return "immediate", "family/group sender during non-quiet-hours"

    if "verified business sender" in legitimate:
        return "immediate", "verified business sender"

    return "wait", "unknown sender without strong signals"


def route_message(msg: dict[str, str], businesses: dict[str, dict[str, str]]) -> dict[str, str]:
    sender_type = msg["sender_type"].strip().lower()
    sender_name = msg["sender_name"].strip()
    text = msg.get("text", "")
    quiet = msg.get("quiet_hours_flag", "0").strip() == "1"

    evidence_parts = [
        f"sender_type={sender_type}",
        f"sender_name={sender_name}",
        f"quiet_hours_flag={1 if quiet else 0}",
    ]

    legit_trust, sus_trust, trust_evidence = evaluate_business_domain_trust(
        sender_name, sender_type, businesses
    )
    sus_urgency, urgency_evidence = evaluate_urgency_keywords(text)
    evidence_parts.extend(trust_evidence)
    evidence_parts.extend(urgency_evidence)

    action, reason = combine_signals(
        sender_type, quiet, legit_trust, sus_trust, sus_urgency
    )

    return {
        "message_id": msg["message_id"],
        "action": action,
        "reason": reason,
        "evidence": "; ".join(evidence_parts),
    }


def main() -> None:
    businesses = load_business_accounts(BUSINESS_PATH)
    messages = load_messages(MESSAGES_PATH)
    results = [route_message(msg, businesses) for msg in messages]

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["message_id", "action", "reason", "evidence"])
        writer.writeheader()
        writer.writerows(results)


if __name__ == "__main__":
    main()
