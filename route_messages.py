#!/usr/bin/env python3

"""Route inbox messages by combining independent trust and urgency signals.
Output includes a confidence score in [0.0, 1.0] based on how many signals
agree, whether they conflict, and how strong each signal is.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - handled at runtime
    load_dotenv = None

try:
    from google import genai
except ImportError:  # pragma: no cover - handled at runtime
    genai = None

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
        messages = list(csv.DictReader(fh))
        return messages


def parse_report_count(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_high_reports(value: str) -> bool:
    reports = parse_report_count(value)
    return reports is not None and reports >= HIGH_REPORTS_THRESHOLD


def reports_evidence_strength(reports: int) -> float:
    """Saturating strength: 3 reports is weak, 62 is strong."""
    if reports <= 0:
        return 0.0
    return reports / (reports + 10.0)


def find_urgency_keywords(text: str) -> list[str]:
    lowered = text.lower()
    return [kw for kw in URGENCY_KEYWORDS if kw in lowered]



def judge_urgency_with_llm(text: str) -> tuple[bool, float, str]:
    """Judge message urgency using Gemini, with a safe fallback if the API is unavailable."""
    if load_dotenv is not None:
        load_dotenv()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or genai is None:
        return (
            False,
            0.0,
            "LLM judge unavailable; defaulted to not urgent because the Gemini SDK or API key is not configured.",
        )

    raw_response: str | None = None
    response = None

    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            "You are evaluating whether a message is urgent or scam-like. "
            "Return ONLY valid JSON with keys: is_urgent (boolean), confidence (float between 0 and 1), "
            "and explanation (string). "
            f"Message text: {text}"
        )
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        parsed = getattr(response, "parsed", None)
        if parsed is None:
            raw_response = getattr(response, "text", None)
            if raw_response is None:
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    parts = getattr(candidates[0].content, "parts", []) or []
                    raw_response = "".join(
                        part.text for part in parts if getattr(part, "text", None)
                    )

            if not raw_response:
                raise ValueError("Gemini returned an empty response body.")

            cleaned_response = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                raw_response.strip(),
                flags=re.IGNORECASE | re.MULTILINE,
            )
            parsed = json.loads(cleaned_response)

        is_urgent = bool(parsed.get("is_urgent", False))
        confidence = float(parsed.get("confidence", 0.0))
        explanation = str(parsed.get("explanation", "Gemini provided no explanation."))

        confidence = max(0.0, min(1.0, confidence))
        return is_urgent, round(confidence, 2), explanation

    except Exception:
        return (
            False,
            0.0,
            "LLM judge failed; defaulted to not urgent because Gemini returned an invalid response.",
        )


def evaluate_business_domain_trust(
    sender_name: str,
    sender_type: str,
    businesses: dict[str, dict[str, str]],
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], list[str]]:
    """Return (legitimate, suspicious, evidence) with per-signal strength in [0, 1]."""
    legitimate: list[tuple[str, float]] = []
    suspicious: list[tuple[str, float]] = []
    evidence: list[str] = []

    account = businesses.get(sender_name)
    if account is None:
        if sender_type == "business":
            suspicious.append(("unlisted business sender", 0.70))
            evidence.append("business_account=missing")
        return legitimate, suspicious, evidence

    official = account["official_domain"].strip().lower()
    used = account["domain_used_by_sender"].strip().lower()
    verified = account["verified"].strip()
    reports_raw = account["user_reports_30d"].strip()
    reports = parse_report_count(reports_raw) or 0
    domain_mismatch = used != official
    unverified_high_reports = verified == "0" and is_high_reports(reports_raw)
    verified_match = (not domain_mismatch) and verified == "1" and not is_high_reports(reports_raw)

    evidence.extend(
        [
            f"official_domain={official}",
            f"domain_used_by_sender={used}",
            f"verified={verified}",
            f"user_reports_30d={reports_raw}",
        ]
    )

    if domain_mismatch:
        suspicious.append(("domain mismatch", 0.88))
        evidence.append("domain_mismatch=true")
    if unverified_high_reports:
        suspicious.append(("unverified high reports", 0.55 + 0.40 * reports_evidence_strength(reports)))
        evidence.append("unverified_high_reports=true")
    if verified_match:
        
        # Low report counts support trust; they do not count as suspicion.
        
        clean = 1.0 - reports_evidence_strength(reports)
        legitimate.append(("verified business sender", 0.72 + 0.18 * clean))
        evidence.append("business_trust=verified")

    return legitimate, suspicious, evidence


def evaluate_urgency_keywords(text: str) -> tuple[list[tuple[str, float]], list[str]]:
    """Return (suspicious_signals, evidence) using the LLM urgency judge."""
    is_urgent, confidence, explanation = judge_urgency_with_llm(text)

    if not is_urgent:
        return [], ["urgency_keywords=none", f"llm_explanation={explanation}"]

    return (
        [("urgency language", confidence)],
        [f"urgency_keywords=llm", f"llm_explanation={explanation}", f"llm_confidence={confidence:.2f}"],
    )


def combine_signals(
    sender_type: str,
    quiet: bool,
    legit_trust: list[tuple[str, float]],
    sus_trust: list[tuple[str, float]],
    sus_urgency: list[tuple[str, float]],
) -> tuple[str, str, list[tuple[str, float]], list[tuple[str, float]]]:
    legitimate = list(legit_trust)
    if sender_type in {"family", "group"}:
        legitimate.append(("family/group sender", 0.84))

    suspicious = sus_trust + sus_urgency
    legit_names = [name for name, _ in legitimate]
    sus_names = [name for name, _ in suspicious]
    has_legit = bool(legitimate)
    has_sus = bool(suspicious)

    if has_legit and has_sus:
        action = "wait"
        reason = (
            "conflict: legitimate signals ("
            + ", ".join(legit_names)
            + ") vs suspicious signals ("
            + ", ".join(sus_names)
            + ")"
        )
        if quiet:
            reason += "; quiet hours"
        return action, reason, legitimate, suspicious

    if has_sus:
        if sus_trust and not sus_urgency:
            return "muted", "suspicious sender", legitimate, suspicious
        if sus_urgency and not sus_trust:
            return "muted", "likely scam", legitimate, suspicious
        return "muted", "suspicious sender", legitimate, suspicious

    if quiet:
        return "wait", "quiet hours", legitimate, suspicious
        

    if sender_type in {"family", "group"}:
        return "immediate", "family/group sender during non-quiet-hours", legitimate, suspicious

    if any(name == "verified business sender" for name, _ in legitimate):
        return "immediate", "verified business sender", legitimate, suspicious

    return "wait", "unknown sender without strong signals", legitimate, suspicious


def score_confidence(
    legit: list[tuple[str, float]],
    sus: list[tuple[str, float]],
    action: str,
) -> float:
    
    #Confidence in [0, 1] from agreement, conflict, individual signal strength,
    #and how strong a claim the action itself represents.
    
    legit_w = [w for _, w in legit]
    sus_w = [w for _, w in sus]
    if not legit_w and not sus_w:
        return 0.40

    l_mass = sum(legit_w)
    s_mass = sum(sus_w)
    total = l_mass + s_mass
    agreement = abs(l_mass - s_mass) / total
    strongest = max(legit_w + sus_w)
    n_signals = len(legit_w) + len(sus_w)
    breadth = min(1.0, (n_signals - 1) / 2.0)
    conflict = bool(legit_w) and bool(sus_w)

    if conflict:
        confidence = 0.28 + 0.22 * agreement * strongest
    else:
        confidence = min(0.99, 0.68 + 0.18 * strongest + 0.10 * breadth)

    # "wait" is a soft, reversible deferral — it shouldn't claim the same
    # confidence as a decisive immediate/muted call, even if the underlying
    # signal (e.g. sender trust) is strong. The signal explains WHY we're
    # deferring, not that we're certain deferring is correct.
    
    if action == "wait" and not conflict:
        confidence = min(confidence, 0.70)

    return round(confidence, 2)


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

    action, reason, legitimate, suspicious = combine_signals(
        sender_type, quiet, legit_trust, sus_trust, sus_urgency
    )
    
    confidence = score_confidence(legitimate, suspicious, action)
    return {
        "message_id": msg["message_id"],
        "action": action,
        "reason": reason,
        "confidence": f"{confidence:.2f}",
        "evidence": "; ".join(evidence_parts),
    }
    


def main() -> None:
    businesses = load_business_accounts(BUSINESS_PATH)
    messages = load_messages(MESSAGES_PATH)
    results = [route_message(msg, businesses) for msg in messages]

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["message_id", "action", "reason", "confidence", "evidence"]
        )
        writer.writeheader()
        writer.writerows(results)
        

if __name__ == "__main__":
    main()
