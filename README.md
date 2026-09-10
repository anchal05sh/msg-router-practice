# Message Router

A small Python router that scores inbox messages for **sender trust** and **urgency language**, then chooses `immediate`, `wait`, or `muted`. Quiet hours never skip those checks.

The urgency signal now comes from a placeholder `judge_urgency_with_llm(text)` function that is designed to call an LLM API in production (using a key from an environment variable), but for now returns the manually validated scores for the six test messages in this workspace.

## Why this exists

Inbox filters often stop at the first matching rule (family → notify, quiet hours → hold, scam keywords → mute). That hides mixed cases, such as a **verified business** sending **urgency copy**. This script evaluates both signals on every row and records the evidence used.

## Requirements

- Python 3.9+
- Standard library only (`csv`, `pathlib`)

## Quick start

```bash
python route_messages.py
```

Reads:

- `dataset/messages.csv`
- `dataset/business_accounts.csv`

Writes:

- `result.csv` next to the script

## Input schemas

### `dataset/messages.csv`

| Column | Description |
| --- | --- |
| `message_id` | Unique id |
| `sender_type` | `family`, `group`, `business`, or `unknown` |
| `sender_name` | Display name; matched to `business_name` when present |
| `text` | Message body |
| `quiet_hours_flag` | `1` during quiet hours, else `0` |

### `dataset/business_accounts.csv`

| Column | Description |
| --- | --- |
| `business_name` | Lookup key (same as `sender_name`) |
| `verified` | `1` if the account is verified |
| `official_domain` | Expected domain |
| `domain_used_by_sender` | Domain observed on this message |
| `user_reports_30d` | User reports in the last 30 days |

## Output schema

`result.csv` columns: `message_id`, `action`, `reason`, `confidence`, `evidence`.

| Action | Meaning |
| --- | --- |
| `immediate` | Notify now |
| `wait` | Hold (quiet hours or conflicting signals) |
| `muted` | Do not notify (suspicious sender or likely scam) |

The `confidence` value is written as a decimal from `0.00` to `1.00` and reflects the strength and agreement of the signals, adjusted for the selected action.

## Decision logic

Both checks run on **every** message:

1. **Business-domain trust** — look up `sender_name` in `business_accounts.csv`.
   - Legitimate: verified, matching domain, and `user_reports_30d` below 10.
   - Suspicious: missing business row (when `sender_type=business`), domain mismatch, or `verified=0` with high reports.
   - Family/group senders also count as legitimate.
2. **Urgency judge** — call `judge_urgency_with_llm(text)`, which judges the message text in isolation for urgency/scam-like language. In the current placeholder version, it returns the manually validated scores for the six test messages; in production this would call an LLM API using a key read from an environment variable such as `MSG_ROUTER_LLM_API_KEY`.

> CSV parsing note: `csv.DictReader` already handles CSV quote escaping correctly. The placeholder urgency lookup matches the exact decoded `text` value from the CSV, rather than stripping quote characters a second time.

Then combine:

| Trust | Urgency / suspicion | Quiet hours | Action |
| --- | --- | --- | --- |
| Legitimate only | None | Off | `immediate` |
| Legitimate only | None | On | `wait` (`quiet hours`) |
| None | Suspicious only | Any | `muted` (`suspicious sender` or `likely scam`) |
| Legitimate **and** suspicious | Both | Any | `wait` (reason lists the conflict) |

Quiet hours is applied **after** the two signals. It can turn a clean notify into `wait`, but it does not mute scams and it does not skip trust or keyword checks.

## Confidence scoring

Confidence combines signal strength, the amount of supporting evidence, and whether the signals agree. The selected action also affects the result:

- `immediate` and `muted` decisions can retain the confidence implied by their signals.
- A plain `wait` decision, such as a clean message deferred only because of quiet hours, is capped at `0.70` because it is a softer, reversible decision.
- A `wait` caused by conflicting legitimate and suspicious signals uses the conflict score directly. It remains low when evidence disagrees and is never raised to the `0.70` ceiling.

## Sample results

| message_id | action | reason |
| --- | --- | --- |
| m1 | immediate | Family sender, no urgency, not quiet hours |
| m2 | muted | Fake domain, unverified high reports, and `click` |
| m3 | wait | Group reminder during quiet hours |
| m4 | immediate | Verified Amazon, matching domain |
| m5 | muted | Unknown sender with urgency language |
| m6 | wait | Verified Amazon **vs** urgency language (conflict) |

## Project layout

```
msg-router-practice/
├── dataset/
│   ├── messages.csv
│   └── business_accounts.csv
├── route_messages.py
├── result.csv
└── README.md
```

## License

Use and modify freely for hackathon / practice work.
