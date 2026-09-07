# Message Router

A small Python router that scores inbox messages for **sender trust** and **urgency language**, then chooses `immediate`, `wait`, or `muted`. Quiet hours never skip those checks.

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

`result.csv` columns: `message_id`, `action`, `reason`, `evidence`.

| Action | Meaning |
| --- | --- |
| `immediate` | Notify now |
| `wait` | Hold (quiet hours or conflicting signals) |
| `muted` | Do not notify (suspicious sender or likely scam) |

## Decision logic

Both checks run on **every** message:

1. **Business-domain trust** — look up `sender_name` in `business_accounts.csv`.
   - Legitimate: verified, matching domain, and `user_reports_30d` below 10.
   - Suspicious: missing business row (when `sender_type=business`), domain mismatch, or `verified=0` with high reports.
   - Family/group senders also count as legitimate.
2. **Urgency keywords** — scan `text` for phrases such as `urgent`, `verify now`, `suspended`, `click`, `otp`, `kyc`.

Then combine:

| Trust | Urgency / suspicion | Quiet hours | Action |
| --- | --- | --- | --- |
| Legitimate only | None | Off | `immediate` |
| Legitimate only | None | On | `wait` (`quiet hours`) |
| None | Suspicious only | Any | `muted` (`suspicious sender` or `likely scam`) |
| Legitimate **and** suspicious | Both | Any | `wait` (reason lists the conflict) |

Quiet hours is applied **after** the two signals. It can turn a clean notify into `wait`, but it does not mute scams and it does not skip trust or keyword checks.

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
