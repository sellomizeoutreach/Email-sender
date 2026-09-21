<USER_REQUEST>
# Sellomize Reach — Complete Build Instructions for Antigravity

This is a self-contained instruction set. Work through it in the order given in **Part 8 (Work Order)**. 

**Ground rules:**
- Do not change code until you've read the whole file. Work incrementally — one change at a time, and confirm the app still runs (and the packaged `.exe` still starts) after each step.
- No marketing language in your reports ("enterprise-grade", "100% fidelity", etc.). Tell me plainly what you did, what you changed, and what you're unsure about.
- Stop reporting "all tests pass in under a second" as proof. A test only counts if it goes **red when the code is wrong** (see Part 3).
- There is one source of truth for `app.py`. The installed/dist copies are produced by the build step, never hand-edited.

---

## Part 0 — Remove the AI/LLM feature entirely

Decision made: we are **not** using in-app AI. Users will convert emails into templates externally using the master prompt in the Appendix, then paste the result into Studio. Remove the AI integration cleanly.

### 0.1 Delete these AI pieces
- The **"Auto-Rewrite with AI"** button and its custom-instructions prompt box in the Review Queue.
- Any **"AI suggestions"** copy features.
- The **AI credential fields** in the Settings sidebar (Gemini / OpenAI / Anthropic / GCP keys, and the primary/fallback model inputs).
- The **LiteLLM integration** and all LLM API call code.
- Remove `litellm` from `requirements.txt` and any AI-only modules (e.g. an `llm_engine.py` that exists only for LLM calls).

### 0.2 IMPORTANT — keep these, they are NOT AI
These are rule-based and must keep working after AI is removed. If any of them currently live in the same module as the LLM calls, separate them out first, then delete only the LLM code:
- The **Deliverability Auditor** (0–100 spam scoring, subject-length and link-count checks, spam-trigger list).
- The **Negative-keyword scanner**.
- The **Spintax parser** and **variable injection**.

### 0.3 This resolves the hardcoded API key
Since the AI is gone, the hardcoded Gemini key and GCP project ID must be removed everywhere they appear:
- `database.py` (default config), `app.py` (input fallback), `test_system.py` (the test asserting it), `README.md`, `STREAMLIT_CLOUD_DEPLOY.md`.
- After removal, grep the whole repo for the key string and the project ID `606768026327` and confirm zero matches remain.

### 0.4 Remove AI-only tests
- Delete tests that only exercise the LLM path (e.g. the LLM JSON-parser test). **Do not** delete the deliverability-auditor, negative-keyword, spintax, or variable-injection tests — those stay.

### 0.5 Surface the replacement workflow (optional but recommended)
- In **Studio & Templates**, add a small collapsed help section: "Turn a written email into a template" with the instruction to use an external AI and a copy button for the master prompt (Appendix A). This replaces the deleted in-app AI with a zero-dependency workflow.

---

## Part 1 — Security fixes

### 1.1 SMTP password storage — real encryption (not base64, not hashing)
- Do **not** store passwords in plaintext, and do **not** use base64 (trivially reversible) or hashing (one-way — you can't log in with a hash).
- Use **real reversible encryption** (`cryptography` Fernet), with the encryption key stored in the OS keychain via the `keyring` library — not hardcoded in the app.
- Windows DPAPI is acceptable as an alternative, but note it's bound to the user+machine: if the DB moves to another machine or Windows is reinstalled, stored passwords become undecryptable. Handle that by **prompting re-entry**, never by crashing.

### 1.2 HTML sanitization / XSS — use a maintained library
- Email templates, custom variables, and CSV-imported fields can contain `<script>` or `<img onerror=...>` and are currently rendered raw via `st.html()` / `unsafe_allow_html=True`.
- Sanitize with a vetted library — **`nh3`** — using an allowlist of safe email tags (`p, div, table, tr, td, span, strong, em, a, img, br, h1–h6`, etc.).
- **Keep** iframe isolation: render previews via `st.components.v1.html(sanitized, height=..., scrolling=True)` as a second layer.

### 1.3 Email header injection (CRLF)
- Add a central `sanitize_header(value)` that strips `\r`, `\n`, and `\x00`, and apply it to **subject, recipient, sender name, and BCC** in both the Hostinger SMTP and Outlook COM paths.
- Reason: a newline in any of those fields lets someone inject arbitrary SMTP headers or a hidden BCC.

### 1.4 SQL — guard the dynamic identifiers only
- DML (SELECT/INSERT/UPDATE/DELETE) is already parameterized with `?` — leave it.
- The `ALTER TABLE ... ADD COLUMN {col_name}` migrations build identifiers by string formatting. Validate column names against `^[a-zA-Z0-9_]+$` before use.

---

## Part 2 — Correctness & robustness

### 2.1 Overnight sending-window bug
- `is_within_sending_window()` blocks 24/7 when the window crosses midnight (e.g. 21:00–06:00). Fix with the wrap-around case:
  - if `start <= end`: inside = `start <= now < end`
  - else (crosses midnight): inside = `now >= start or now < end`

### 2.2 Silent `except: pass`
- Replace bare exception swallowing with specific exceptions + logging to `sellomize.log`:
  - Schema migrations: catch `sqlite3.OperationalError`, log unless it's "duplicate column name".
  - JSON parsing: catch `(json.JSONDecodeError, TypeError, ValueError)`.
  - Server boot / sockets: `logger.warning(...)`.

### 2.3 MX cache
- Replace the unbounded in-memory dict with an LRU cache (max 1000 entries) + 24h TTL.
- **Delete** the manual "Clear MX Cache" button — users shouldn't manage internal caches.

### 2.4 DNS lookup on every keystroke
- In the Review Queue left pane, DNS is resolved on every rerun/keystroke. Use the cached MX status stored on the contact record; only resolve on explicit pre-flight.

### 2.5 Hard vs. soft bounce
- Auto-quarantine (mark Do Not Contact) **only hard bounces** (e.g. 550).
- **Soft bounces** (e.g. 452 mailbox full, greylisting) → flag for retry, do **not** mark Do Not Contact. Keep an audit log of what was quarantined and why.

### 2.6 Background thread discipline
- IMAP scan and SMTP connection tests run in background workers that write progress to SQLite. They must **never** touch `st.session_state` or call any `st.*` function — the UI polls SQLite for status. (Touching Streamlit state from a thread is undefined behavior.)

### 2.7 No hardcoded absolute paths
- Remove machine-specific paths (`C:\Users\Ecomfine\...`); use relative paths / config so it runs on any machine.

---

## Part 3 — Make the tests real (do this before the refactor)

### 3.1 Write behavioral smoke tests (`test_smoke.py`)
1. **End-to-end campaign generation:** filter N contacts → generate sequence with spintax + variables → assert N drafts created with correct per-recipient variables and staggered send times.
2. **Negative-keyword guardrail:** draft with a banned word → status becomes `Flagged` and it's excluded from approval.
3. **Hard vs. soft bounce:** simulate `550` → Do Not Contact; simulate `452` → flagged for retry, NOT Do Not Contact.
4. **LRU cache + TTL:** exceed 1000 entries → oldest evicted; expired entry (>24h) → cache miss + fresh resolve.
5. **Header-injection prevention:** subject `Hi\r\nBcc: evil@x.com` → newlines stripped, no injected header.
6. **Overnight window:** window 21:00–05:00 → allowed at 23:00, paused at 12:00.

### 3.2 Prove they actually catch bugs
- Deliberately break 2–3 behaviors (e.g. force `is_within_sending_window` to always return `True`; disable the negative-keyword flag) and **confirm the matching test goes red**. Report which test caught which break, then revert the breaks. A test that stays green when the code is broken is worse than no test.

### 3.3 Mock DNS in the suite
- The existing MX test hits real DNS (`google.com`, a "nonexistent" domain). Mock the resolver so tests are deterministic and work offline. No live network calls in the test suite.

---

## Part 4 — Build & packaging

### 4.1 Single build pipeline
- One source of truth in the workspace. Installed and dist copies are produced by `build.py` (PyInstaller via the spec, then Inno Setup). Never hand-edit `_internal` copies.

### 4.2 Remove the monkeypatch startup hack
- Delete the `importlib`-based `.py` reload loops in `app.py` (~lines 37–48) and `launcher.py` (~125–138). Keep the `sys.path.insert(...)` lines so normal imports resolve.
- Then build with `python build.py --no-installer` and **verify the packaged `dist/SellomizeReach/SellomizeReach.exe` actually starts** — not just `streamlit run`. That hack existed to make the frozen build work, so the exe is exactly where this may break.

---

## Part 5 — Architecture (do this LAST, on top of real tests)

### 5.1 Break up the 3,000-line `app.py` incrementally
- Target structure: `config.py`, `database.py` (pure queries), `scheduler.py` (windows/cadence — move the sending-window functions here out of `database.py`), `smtp_dispatcher.py`, `mx_checker.py`, `contacts_handler.py`, and a `ui/` package (`theme.py`, `components.py`, `sidebar.py`, `tabs/`).
- Move **one module at a time**, run the app after each move, keep each move a separate commit. No big-bang refactor.

### 5.2 Isolate the fragile CSS
- Move the ~700 lines of Streamlit-internal CSS into `ui/theme.py` with a header comment: `# Targets Streamlit private internals. Verified on Streamlit 1.63.0. Re-check all [data-testid]/[role] selectors on upgrade.`

### 5.3 De-duplicate
- Single source for the warmup daily-limit calc (UI and scheduler must use the same function).
- Single `resolve_template(body, contact)` used by both the Studio preview and real campaign generation.

---

## Part 6 — UX cleanup

### 6.1 Tooltips
- Delete tooltips that just parrot the label (e.g. "Company" → "Brand or business name"). Keep and standardize only the ones that explain a non-obvious consequence, in plain, consistent, emoji-free language.

### 6.2 Accent-color discipline
- Orange `#FD4D1B` = **primary action only** (one per view).
- Warnings → amber `#D97706`; hard bounces / dead domains → crimson `#DC2626`; warmup badges → warm gold; selected-count indicator → pine green `#083731`. Stop using orange for status/warmup/selection.

### 6.3 Move / merge / hide (from the earlier action-to-goal audit)
- Rename the Leads "Reset View" button to "Discard Unsaved Changes" and only show it when there are unsaved edits.
- Move the `:8502` tracking port + base-URL details into an "Advanced" collapsed section; show users a single "Tracking Active" badge by default.
- Remove the redundant top-bar "Audit MX on All Leads" button; keep MX verification inside Bulk Actions (on selected leads) and run it automatically on CSV import.
- IMAP scanner should auto-quarantine hard bounces on detection (with audit log), not require a separate manual button.

### 6.4 Empty / loading / error states
- Every list, table, and preview needs defined states for: zero items, loading, and error. Don't design only the happy path.

### 6.5 Confirmations
- "Approve & Schedule" sends real mail — add a confirmation and, ideally, a short cancel window. Confirm on "Clear signature" and any destructive action.

---

## Part 7 — Dependencies
- Pin `requirements.txt` from **`pip freeze` on the working machine** (not from memory). Include the exact Streamlit version the CSS depends on. Drop `litellm` (AI removed).

---

## Part 8 — Work order

1. **Part 0** — remove AI entirely (deletes the API-key problem; keep the rule-based auditor/keyword/spintax logic).
2. **Part 3** — write `test_smoke.py`, prove the tests go red, mock DNS.
3. **Part 4** — single build pipeline; remove monkeypatch; verify the packaged `.exe` starts.
4. **Part 1** — security: password encryption, HTML sanitizer + iframe, header injection, ALTER TABLE guard.
5. **Part 2** — correctness/perf: overnight window, `except:pass` logging, LRU MX cache + remove purge button, per-keystroke DNS, hard/soft bounce, thread discipline, absolute paths.
6. **Part 7** — pin dependencies.
7. **Part 6** — UX cleanup.
8. **Part 5** — incremental module split (last).

Confirm findings/plan for each part before applying. Start at Part 0.

---
---

# Appendix A — Master Prompt: "Turn this email into a Sellomize Reach template"

This is the replacement for the removed in-app AI. It's given to end users (and can be surfaced in Studio per 0.5). A user copies the box below into any AI model, pastes their email, and gets back a template in this app's exact syntax.

```
You are an expert cold-outreach copywriter. Convert the email I give you into a REUSABLE OUTREACH TEMPLATE for the Sellomize Reach platform. Follow these rules exactly.

PLACEHOLDER SYNTAX (square brackets):
- Recipient's first name  -> [Name]
- Recipient's company/brand -> [Company]
- Any other detail that changes per recipient -> a clear custom variable in square brackets, e.g. [Role], [City], [ProductCategory], [ASIN]. Name them in TitleCase, no spaces (use underscores if needed).
- DO NOT turn MY OWN details into variables. My name, my company, my signature, my links stay exactly as written.

RULES:
1. Only replace text that is genuinely specific to one recipient. Do not force a placeholder where the wording isn't actually person-specific.
2. Every placeholder must read grammatically once filled in (mind a/an, capitalization, plurals).
3. SPINTAX (optional, light): for the greeting and 1-3 short interchangeable phrases, offer variations using {option A|option B} syntax so each send differs slightly and lands in the inbox better. Every possible combination must read correctly. Never apply spintax to names, links, the core offer, or anything where a wrong combo would sound off.
4. DELIVERABILITY: avoid spam-trigger words and patterns (e.g. "100% free", "guarantee", "act now", "risk-free", "limited time", ALL CAPS, and rows of exclamation marks). Keep the subject line short, lower-key, and human.
5. Preserve my tone, intent, structure, and call-to-action. Do not invent claims, offers, statistics, or facts I did not write.
6. Keep all links intact.

OUTPUT — return ONLY this, nothing before or after:

Subject: <templated subject line>

<templated body>

---
Variables used:
- [Name] -> recipient first name
- [Company] -> recipient company/brand
- (list every other variable you used, one per line, with a short note on what data each column needs)

Now convert the email below.

====== EMAIL START ======
[PASTE YOUR EMAIL HERE]
====== EMAIL END ======
```

**How users use it:** copy the box → replace `[PASTE YOUR EMAIL HERE]` with a real email → send to any model → paste the result into Studio & Templates → the "Variables used" list tells them which CRM columns each contact needs. Run the result through the app's Deliverability Auditor before saving.
</USER_REQUEST>
<ADDITIONAL_METADATA>
The current local time is: 2026-09-18T18:37:37+05:00.
</ADDITIONAL_METADATA>