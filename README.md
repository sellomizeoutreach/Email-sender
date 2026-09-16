# Modular Local Email Automation System

A robust, locally hosted email generation and dispatch platform for Windows. The architecture eliminates external spreadsheet dependencies by integrating an internal SQLite CRM, reusable Spintax & variable templates, automated negative keyword scanning, and a decoupled Microsoft Outlook dispatch daemon.

---

## 🏛️ System Architecture

```
                  ┌─────────────────────────────────────────────────────────────┐
                  │                    Streamlit UI (app.py)                    │
                  │  - 👥 Contact Manager (Internal Lead CRM + Custom JSON Vars)│
                  │  - 📝 Template Builder (Spintax {a|b} & [Name] Variables)   │
                  │  - 🚀 Campaign Generator (Batch Resolution & Negative Scan) │
                  │  - 📥 Review Queue (Dual-Mode Editor, Red Flagged Alerts,   │
                  │     Auto-Rewrite Action & Local Time Scheduling)            │
                  │  - ⚙️ Configuration & Outbox History                        │
                  └──────────────────────────────┬──────────────────────────────┘
                                                 │
                                                 ▼
                                ┌─────────────────────────────────┐
                                │      SQLite (email_system.db)   │
                                │  - contacts table               │
                                │  - templates table              │
                                │  - system_config table          │
                                │  - smtp_accounts table          │
                                │  - emails queue table           │
                                └────────────────┬────────────────┘
                                                 ▲
                                                 │ Polls every 60s (Local System Time)
                              ┌──────────────────┴──────────────────┐
                              │    Scheduler Daemon (scheduler.py)  │
                              │  - ⚡ Hostinger Direct SMTP Dispatch │
                              │    (Multi-account round-robin)      │
                              │  - ⏱️ Anti-spam 20-45s human delay   │
                              │  - 📧 Desktop Outlook via pywin32   │
                              │  - Injects HTML Body + Signature    │
                              │  - Attaches Verification BCC        │
                              └─────────────────────────────────────┘
```

---

## 🚀 Quickstart Guide

### 1. Launch the Streamlit Frontend
In PowerShell or Terminal:
```powershell
streamlit run app.py
```
This opens the dashboard in your default web browser at `http://localhost:8501`.

### 2. Launch the Standalone Dispatch Scheduler
Run the scheduler in a separate PowerShell window or background service:
```powershell
python scheduler.py
```
- The scheduler polls every 60 seconds by default.
- Custom polling interval: `python scheduler.py --interval 30`
- Safe dry-run verification mode: `python scheduler.py --once --dry-run`

---

## 📋 Features & Dashboard Tabs

### 👥 Tab 1: Contact Manager (Internal CRM)
- Manage leads directly in SQLite without Excel/CSV uploads.
- Fields: `Full Name`, `Email Address`, `Company Name`, and `Custom Variables` (stored as JSON, e.g. `{"Role": "Founder", "Niche": "Fitness"}`).
- Live data table of saved contacts with search, variable chips, and deletion.

### 📝 Tab 2: Template Builder
- Create reusable outreach copy with dynamic variable injection and Spintax variation:
  - **Variables**: Use `[Name]`, `[Company]`, `[Email]`, or any custom variable like `[Role]`.
  - **Spintax**: Use `{word1|word2|word3}`. The engine randomly selects one option per contact to ensure unique phrasing. Nested Spintax like `{Hi|{Good morning|Hello}}` is fully supported.
- **Test Spintax & Variable Resolution**: Interactive preview button resolves template tokens against CRM leads on the fly.

### 🚀 Tab 3: Campaign Generator
- Select specific contacts or check **Select All Contacts**.
- Pick a saved outreach template.
- Optional special instructions / playbook notes.
- Click **Generate Campaign**:
  1. Injects contact variables into the template.
  2. Resolves Spintax combinations.
  3. Formats and polishes HTML copy via LiteLLM.
  4. Scans output against `negative_keywords`:
     - If clean: saves as `Pending`.
     - If restricted keyword found: saves as `Flagged` and tags the trigger word.

### 📥 Tab 4: Review Queue & Flag Handling
- Filter by `All Actionable`, `Flagged Only (Action Required)`, or `Pending Only`.
- **🚨 Flagged Emails**:
  - Prominently styled with a bold red warning card showing the exact trigger word.
  - "Approve" button is locked until negative keywords are resolved.
  - **⚡ Auto-Rewrite with AI**: Sends text to LiteLLM with: `"Rewrite this email to remove the negative keyword: [Trigger Word]"` and resets clean drafts to `Pending`.
- **Dual-Mode Editor**: Toggle between Visual WYSIWYG (`streamlit-quill`) and HTML Source Code tied to a single shared state key.
- **Local Time Scheduling**: Date & time pickers strictly aligned with Local System Time.

### ⚙️ Tab 5: Configuration & Outbox
- **⚡ Hostinger Direct SMTP Multi-Account Fleet**:
  - Connect multiple Hostinger mailboxes (`smtp.hostinger.com:465` SSL / `587` STARTTLS).
  - Real-time connection testing & credential verification directly in UI.
  - Automatic load balancing and round-robin mailbox rotation with configurable daily limits (default 80/day per mailbox).
  - Daily quota utilization tracking and automated midnight reset.
- **⏱️ Anti-Spam Human Delay Throttling**: Configurable randomized 20–45s gaps between outgoing emails to protect domain reputation and prevent spam filters.
- **Dual Outbound Engines**: Seamlessly switch between `⚡ Hostinger Direct SMTP` and `📧 Desktop Microsoft Outlook`.
- Pre-configured with Google Gemini / Vertex credentials (`AQ.Ab8RN6JyptGhhfk8w83PSpKVcFpmNJOA7aoEJtiB2BCEEiuwVw` and project `606768026327`), OpenAI, and Anthropic.
- Manage **Restricted Negative Keywords** and **Restricted Spam Words**.
- Manage Outlook fallback sender account and verification BCC address.
- Dual-mode HTML Signature Manager.
- Outbox monitor for `Approved`, `Sent`, `Flagged`, and `Account Mismatch` records with `Dispatched Via` auditing.
