# Sellomize Reach — Email Automation Platform

A locally-hosted (and Streamlit Cloud deployable) cold-outreach platform built on SQLite, Streamlit, and direct SMTP dispatch.
No external spreadsheets, no AI dependencies, no per-email API fees.

### Core Model
> **The user does the research and writing; the app does the mechanical work.**
There are zero external AI/LLM API calls, zero API keys, and zero paid dependencies anywhere in the app. Personalization stems from structured research fields entered by the user + reusable client proof stories + rule-based angle suggestions and deterministic reply handling.

---

## Architecture

```
                  ┌──────────────────────────────────────────────────────────┐
                  │                    Streamlit UI (app.py)                 │
                  │  👥 Contacts       — Internal SQLite CRM (leads, tags,   │
                  │                      custom variables, pipeline status)  │
                  │  ✉️ Compose & Send — Unified single-send + multi-touch   │
                  │                      campaign flow (progressive steps)   │
                  │  📥 Review & Outbox — Triage pending/flagged drafts,     │
                  │                      approve, and view sent history      │
                  │  📊 Analytics      — Open, click, reply, bounce metrics  │
                  │  ⚙️ Settings       — Mailbox fleet, signature, keyword   │
                  │                      shield, advanced telemetry          │
                  │  ✍️ Templates      — Reusable template library (CRUD)    │
                  └──────────────────────────────┬───────────────────────────┘
                                                 │
                                                 ▼
                                ┌─────────────────────────────────┐
                                │      SQLite (email_system.db)   │
                                │  - contacts                     │
                                │  - templates                    │
                                │  - emails (queue)               │
                                │  - sequence_rules               │
                                │  - smtp_accounts                │
                                │  - system_config (key-value)    │
                                └───────────────┬─────────────────┘
                                                ▲
                                                │ Polls every 60 s
                              ┌─────────────────┴───────────────────┐
                              │    Scheduler Daemon (scheduler.py)  │
                              │  ⚡ Hostinger Direct SMTP dispatch   │
                              │     (multi-account round-robin)     │
                              │  ⏱️ Anti-spam jitter (20–90 s)      │
                              │  📧 Desktop Outlook via pywin32     │
                              │  🔄 Sequence engine: auto-generates │
                              │     follow-up drafts after delay    │
                              │  🛑 Reply guard: cancels pending    │
                              │     follow-ups on any reply         │
                              └─────────────────────────────────────┘
```

---

## Quickstart

### 1. Launch the Streamlit frontend
```powershell
streamlit run app.py
```
Opens at `http://localhost:8501`.

### 2. Launch the background scheduler (optional — already runs as a thread in Streamlit Cloud)
```powershell
python scheduler.py
```
Custom polling interval: `python scheduler.py --interval 30`  
Dry-run mode: `python scheduler.py --once --dry-run`

---

## Features

### 👥 Contacts
Internal SQLite CRM — no Excel or CSV required.
- Fields: Name, Email, Company, Tags, Pipeline Status, Custom Variables (JSON).
- Spreadsheet grid view with inline editing and bulk actions (tag, stage update, export).
- MX domain verification to detect dead inboxes before sending.
- Card view with quick-compose buttons that pre-populate the Compose & Send flow.

### ✉️ Compose & Send
Unified flow for both one-off emails and multi-touch campaigns — same UI, different scale.

**Step 1 — Recipients**
- Search and select CRM contacts **and/or** type any raw email address.
- Typed emails are automatically deduped against CRM contacts (one entry per email, always).
- Compact chip display (email only by default; expand for full contact details).
- Live count: "X recipients (Y from CRM, Z manual)."
- Optional: save manually-typed emails as new CRM contacts.

**Step 2 — Sequence**
- Once / Twice / 3 times.
- Follow-up blocks appear only when selected (progressive disclosure).
- Note displayed: "Follow-ups automatically stop if the person replies."

**Step 3 — Write**
- Per-touch: select a saved template OR write free-form.
- Variables: `[Name]`, `[Company]`, `[Email]`, any custom CRM variable.
- Spintax: `{Hi|Hello|Hey [Name]}` — engine picks one per recipient.
- Inline spam highlighting: detected trigger words highlighted **yellow inside the preview** with exact suggestions.
- Save-as-template checkbox per touch.
- **Missing variable fallback**: if a recipient has no `[Name]` value, the configurable fallback word (default: "there") is substituted. A literal `[Name]` token is never sent.

**Step 4 — Schedule**
- Market timezone selector — sends adapt to recipient business hours automatically.
- Sending window: Business Days / 24/7 / Custom.
- Batch pacing: immediate or spread across active hours.
- Live schedule preview card shows true first-slot time (outside-window sends are held to the next allowed slot — no override needed).

**Step 5 — Send**
- Button label adapts by context: "Send now" (1 recipient, 1 touch) or "Generate & Schedule."
- Disabled with reason when no recipients are selected.
- Drafts land in Review & Outbox as Pending for one-click approval.

### 📥 Review & Outbox
- Sub-tabs: Clean Drafts / Action Required (flagged) / Approved & Scheduled / Dispatched History.
- 1-click "Approve All Clean Drafts" bulk action.
- Master-detail split: list on left, full editor + preview on right.
- Flagged drafts show the exact trigger word and block the Approve button until resolved.
- Internal/BCC addresses automatically blocked and shown with a shield badge.

### 📊 Analytics
- Open, click, reply, and bounce tracking.
- Per-contact and per-campaign breakdown.
- Actionable: link from analytics directly back to re-campaign flagged or unreplied contacts.

### ✍️ Templates
- Reusable template library with full CRUD.
- Live preview with variable/spintax resolution against a sample contact.
- External AI workflow guide: prompts for converting an existing email into a template using any external AI (ChatGPT, Claude, etc.) — the platform itself contains zero AI code.
- Deliverability auditor: rule-based score (0–100) based on spam trigger word count, subject line length, link count, casing, and punctuation. **No network calls — runs entirely locally.**

### ⚙️ Settings
- **Mailbox Fleet**: Hostinger SMTP multi-account pool, daily send limits, warmup ramps, connection test.
- **Signature**: HTML signature editor with live preview.
- **Keyword Shield**: Negative keyword blocklist (flags drafts), spam trigger word library. Variable fallback default.
- **Advanced Telemetry**: Sending window enforcement, multi-country timezone, jitter, LAN IP, diagnostics, log viewer.

---

## Deliverability — Rule-Based, Zero AI

All spam and deliverability checking is **local and instant** with no external API calls:

| Check | Engine |
|---|---|
| Negative keyword scan | `scan_all_negative_keywords()` in `template_engine.py` |
| Spam trigger highlighting | `highlight_spam_triggers()` — wraps matches in `<mark>` in preview |
| Full deliverability audit (0–100 score) | `audit_email_deliverability()` — checks subject length, ALL CAPS, link count, punctuation, word count |
| Suggestions per trigger word | `COMMON_SPAM_TRIGGERS` dict in `template_engine.py` |

---

## Reply Guard & Sequence Engine

- When a prospect replies, `cancel_sequence_rules_for_contact(email)` cancels all pending follow-up rules for that email only.
- Other recipients in the same campaign are unaffected.
- The scheduler engine polls for due sequence rules every 60 s and auto-generates follow-up drafts.
- Drafts go to Review & Outbox as Pending — human approval is always required before dispatch.

---

## Deployment

Runs on **Streamlit Cloud** (see `STREAMLIT_CLOUD_DEPLOY.md`) or locally on Windows with `run_app.bat`.
Database: SQLite at `%APPDATA%\SellomizeReach\email_system.db` (frozen/deployed) or `./email_system.db` (dev).
