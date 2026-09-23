"""
template_engine.py - Rule-based template resolution and deliverability engine.
Handles dynamic variable injection, Spintax evaluation, negative keyword scanning,
HTML paragraph formatting, and 0-100 deliverability spam auditing.
Contains ZERO external AI or LLM dependencies.
"""

import re
import json
import random
import logging
import html
from typing import List, Dict, Any, Optional, Set

try:
    import nh3
    NH3_AVAILABLE = True
except ImportError:
    nh3 = None
    NH3_AVAILABLE = False

logger = logging.getLogger(__name__)

SAFE_EMAIL_TAGS = {
    "a", "b", "blockquote", "br", "caption", "cite", "code", "col", "colgroup",
    "div", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img",
    "li", "ol", "p", "pre", "s", "small", "span", "strike", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul"
}
SAFE_EMAIL_ATTRIBUTES = {
    "a": {"href", "title", "target", "style", "class"},
    "img": {"src", "alt", "title", "width", "height", "style", "class"},
    "*": {"style", "class", "align", "valign", "color", "bgcolor", "width", "height", "border", "cellpadding", "cellspacing"}
}
SAFE_URL_SCHEMES = {"http", "https", "mailto", "cid", "data"}

def sanitize_email_html(html_str: str) -> str:
    """Sanitize email HTML with nh3 using an allowlist of safe email formatting tags."""
    if not html_str or not str(html_str).strip():
        return ""
    if NH3_AVAILABLE and nh3:
        try:
            return nh3.clean(
                str(html_str),
                tags=SAFE_EMAIL_TAGS,
                attributes=SAFE_EMAIL_ATTRIBUTES,
                url_schemes=SAFE_URL_SCHEMES,
                strip_comments=True
            )
        except Exception:
            return html.escape(str(html_str))
    return html.escape(str(html_str))


def inject_variables(template_text: str, contact_data: Dict[str, Any]) -> str:
    """
    Replace bracketed variables in a template (e.g., [Name], [Company], [Email], [Role])
    with the corresponding data from the contact record.
    Supports case-insensitive replacement and custom variables stored in JSON.
    """
    if not template_text:
        return ""

    # Build mapping from contact data
    var_map = {}
    if "name" in contact_data and contact_data["name"]:
        var_map["name"] = str(contact_data["name"])
    if "email" in contact_data and contact_data["email"]:
        var_map["email"] = str(contact_data["email"])
    if "company" in contact_data and contact_data["company"]:
        var_map["company"] = str(contact_data["company"])

    # Include custom variables
    custom_vars = contact_data.get("custom_variables_dict") or {}
    if not custom_vars and "custom_variables" in contact_data:
        cv_val = contact_data["custom_variables"]
        if isinstance(cv_val, dict):
            custom_vars = cv_val
        elif isinstance(cv_val, str):
            try:
                custom_vars = json.loads(cv_val or "{}")
            except (json.JSONDecodeError, TypeError, ValueError) as json_err:
                logger.warning(f"Error parsing custom_variables in inject_variables: {json_err}")
                custom_vars = {}


    for k, v in custom_vars.items():
        var_map[k.lower()] = str(v)

    # Regex to match [variable_name]
    def replacer(match):
        token = match.group(1).strip().lower()
        if token in var_map:
            return var_map[token]
        # Return original token if no matching variable found
        return match.group(0)

    result = re.sub(r'\[([a-zA-Z0-9_\s-]+)\]', replacer, template_text)
    return result

def parse_spintax(text: str) -> str:
    """
    Parse {word1|word2|word3} Spintax syntax using Python's regex module.
    For each block, randomly select one variation separated by the pipe character.
    Recursively resolves nested Spintax blocks from innermost to outermost.
    """
    if not text:
        return ""

    spintax_pattern = re.compile(r'\{([^{}]+)\}')
    while True:
        match = spintax_pattern.search(text)
        if not match:
            break
        options = match.group(1).split('|')
        selected = random.choice(options)
        text = text[:match.start()] + selected + text[match.end():]

    return text

def scan_all_negative_keywords(text: str, negative_keywords_str: str) -> List[str]:
    """
    Run a text scan against the saved negative_keywords list.
    Returns a list of all distinct negative trigger words or phrases detected in the text.
    """
    if not text or not negative_keywords_str:
        return []

    plain_text = re.sub(r'<[^>]+>', ' ', text)
    keywords = [kw.strip() for kw in negative_keywords_str.split(",") if kw.strip()]
    detected = []
    seen = set()

    for kw in keywords:
        escaped_kw = re.escape(kw)
        pattern = rf'(?:\b|_){escaped_kw}(?:\b|_)'
        if re.search(pattern, plain_text, re.IGNORECASE):
            lower_kw = kw.lower()
            if lower_kw not in seen:
                seen.add(lower_kw)
                detected.append(kw)

    return detected


def scan_negative_keywords(text: str, negative_keywords_str: str) -> Optional[str]:
    """
    Run a text scan against the saved negative_keywords list.
    If a negative word or phrase is detected, returns the first detected trigger word.
    Otherwise returns None.
    """
    all_found = scan_all_negative_keywords(text, negative_keywords_str)
    return all_found[0] if all_found else None

# ------------------------------------------------------------------------------
# DELIVERABILITY & SPAM TRIGGER WORD AUDITOR
# ------------------------------------------------------------------------------

COMMON_SPAM_TRIGGERS: Dict[str, List[str]] = {
    "guarantee": ["ensure", "stand behind", "commit to"],
    "guaranteed": ["proven", "reliable", "consistent"],
    "100% free": ["complimentary", "at no charge", "no-obligation"],
    "free": ["complimentary", "on the house", "gift"],
    "risk-free": ["straightforward", "worry-free", "low-friction"],
    "risk free": ["straightforward", "worry-free", "low-friction"],
    "act now": ["at your earliest convenience", "when you have a moment"],
    "urgent": ["timely", "upcoming", "time-sensitive"],
    "winner": ["standout", "leader", "top performer"],
    "congratulations": ["kudos", "great work"],
    "no catch": ["simple terms", "transparent"],
    "make money": ["drive revenue", "boost growth", "expand margin"],
    "earn cash": ["generate returns", "increase profitability"],
    "earn money": ["increase revenue", "boost profitability"],
    "extra income": ["additional revenue", "growth opportunity"],
    "buy now": ["explore options", "get started", "take a look"],
    "order now": ["review details", "schedule onboarding"],
    "click here": ["view breakdown", "check out the link", "see the teardown"],
    "limited time": ["temporary", "current focus"],
    "unlimited": ["extensive", "comprehensive", "full"],
    "credit card": ["billing details", "payment method"],
    "cash": ["capital", "funds", "revenue"],
    "bonus": ["additional perk", "added value"],
    "pure profit": ["net gain", "efficiency gain"],
}

SAFE_ACRONYMS = {
    "CRM", "ROI", "B2B", "B2C", "SEO", "SEM", "FBA", "DTC", "CEO", "CTO", "CFO",
    "COO", "CMO", "VP", "SaaS", "ASIN", "API", "LLM", "URL", "HTML", "PNG", "PDF",
    "USA", "USD", "EUR", "GBP", "CAD", "AUD", "UK", "EU", "AI", "ML", "Q1", "Q2", "Q3", "Q4"
}

def audit_email_deliverability(
    body_html: str,
    subject: str = "",
    custom_negative_keywords: Optional[str] = None
) -> Dict[str, Any]:
    """
    Comprehensive Deliverability & Spam Score Auditor for cold outreach:
    1. Scores copy from 0 to 100 based on inbox placement probability.
    2. Identifies high-risk spam trigger words and offers 1-click alternative synonyms.
    3. Analyzes subject line length, casing, and punctuation.
    4. Audits word count, link count, and formatting traps (excessive !!!, ???, $$$).
    5. Returns detailed score, findings, passes, and suggestions.
    """
    score = 100
    issues = []
    passes = []
    detected_words = []

    # Strip HTML tags
    clean_body = re.sub(r'<[^>]+>', ' ', body_html or '')
    full_text = f"{subject} {clean_body}".strip()

    # 1. Subject Line Analysis (if provided)
    if subject and subject.strip():
        subj_clean = subject.strip()
        subj_words = [w for w in subj_clean.split() if w.strip()]
        subj_len = len(subj_words)

        if 3 <= subj_len <= 7:
            passes.append(f"Optimal subject length ({subj_len} words: ideal for mobile preview).")
        elif subj_len < 3:
            passes.append(f"Short punchy subject ({subj_len} words).")
        elif 8 <= subj_len <= 9:
            issues.append(f"Subject is slightly long ({subj_len} words). Recommend 3-7 words for higher open rate.")
            score -= 4
        else:
            issues.append(f"Subject is too long ({subj_len} words). May be truncated on mobile and flag spam filters (-8 pts).")
            score -= 8

        if "!" in subj_clean:
            issues.append("Exclamation mark '!' detected in subject line (-10 pts: high spam trigger).")
            score -= 10
        else:
            passes.append("No exclamation marks in subject line.")

        if re.search(r"^\s*(re|fwd)\s*:", subj_clean, re.IGNORECASE):
            issues.append("Avoid fake 'Re:' or 'Fwd:' prefix in cold outreach (-12 pts: flagged by ISP algorithms).")
            score -= 12

        # Check ALL CAPS in subject
        subj_upper_words = [w for w in subj_words if w.isupper() and len(w) >= 3 and w not in SAFE_ACRONYMS]
        if subj_upper_words:
            issues.append(f"ALL CAPS word in subject line: '{subj_upper_words[0]}' (-8 pts).")
            score -= 8

    # 2. Spam Trigger Words Scanning
    found_triggers = set()
    combined_trigger_dict = dict(COMMON_SPAM_TRIGGERS)

    # Incorporate custom negative keywords from settings if provided
    if custom_negative_keywords:
        for custom_kw in custom_negative_keywords.split(","):
            c_clean = custom_kw.strip().lower()
            if c_clean and c_clean not in combined_trigger_dict:
                combined_trigger_dict[c_clean] = ["alternative wording", "clean phrasing"]

    for trigger, alternatives in combined_trigger_dict.items():
        escaped = re.escape(trigger)
        pattern = rf'(?:\b|_){escaped}(?:\b|_)'
        matches = re.findall(pattern, full_text, re.IGNORECASE)
        if matches and trigger not in found_triggers:
            found_triggers.add(trigger)
            count = len(matches)
            detected_words.append({
                "word": trigger,
                "count": count,
                "suggestions": alternatives
            })
            deduction = min(10, 6 * count)
            score -= deduction
            sug_str = ", ".join(f"'{a}'" for a in alternatives[:3])
            issues.append(f"Spam trigger word detected: '{trigger}' (-{deduction} pts). Try replacing with: {sug_str}.")

    if not found_triggers:
        passes.append("Zero blacklisted spam trigger words detected.")

    # 3. Excessive Punctuation & Typography Traps
    if re.search(r'!{2,}', full_text):
        issues.append("Multiple exclamation points '!!' detected in email (-8 pts: strong spam signal).")
        score -= 8
    elif "!" in clean_body:
        excl_count = clean_body.count("!")
        if excl_count > 2:
            issues.append(f"Too many exclamation marks ({excl_count}) in body text (-5 pts).")
            score -= 5

    if re.search(r'\?{2,}', full_text):
        issues.append("Multiple question marks '??' detected in email (-5 pts).")
        score -= 5

    if re.search(r'\${2,}|\$\d{4,}', full_text):
        issues.append("Aggressive dollar signs or currency hype detected (-8 pts).")
        score -= 8

    # 4. ALL CAPS Words in Body
    body_words = [w.strip('.,!?:;"()[]{}') for w in clean_body.split() if w.strip()]
    body_upper_words = [w for w in body_words if w.isupper() and len(w) >= 4 and w not in SAFE_ACRONYMS]
    if len(body_upper_words) >= 2:
        issues.append(f"Multiple ALL CAPS words detected ({', '.join(body_upper_words[:3])}) (-6 pts).")
        score -= 6
    elif len(body_upper_words) == 0:
        passes.append("Clean casing: no aggressive ALL CAPS shouting.")

    # 5. Word Count Benchmark
    total_body_words = len(body_words)
    if 40 <= total_body_words <= 160:
        passes.append(f"Optimal cold outreach length ({total_body_words} words: concise & skimmable).")
    elif total_body_words < 25 and total_body_words > 0:
        issues.append(f"Body is very brief ({total_body_words} words). May lack sufficient context (-4 pts).")
        score -= 4
    elif total_body_words > 250:
        issues.append(f"Body is lengthy ({total_body_words} words). Cold emails over 200 words suffer 35% lower reply rates (-6 pts).")
        score -= 6

    # 6. Link Count
    link_matches = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\']', body_html or '', re.IGNORECASE)
    link_count = len([l for l in link_matches if not l.startswith("#") and not l.startswith("mailto:")])
    if link_count <= 1:
        passes.append(f"Healthy link count ({link_count} link: optimal for primary inbox placement).")
    elif link_count == 2:
        passes.append("Acceptable link count (2 links).")
    elif link_count > 2:
        issues.append(f"Too many links ({link_count} links). Cold emails with >2 links trigger ISP promotional filters (-8 pts).")
        score -= 8

    # Clamp score to [0, 100]
    final_score = max(0, min(100, score))

    if final_score >= 90:
        grade = "Excellent"
        grade_color = "#10B981"
        verdict = "High Primary Inboxing Potential. Clean copy ready for high deliverability."
    elif final_score >= 75:
        grade = "Good"
        grade_color = "#F59E0B"
        verdict = "Solid copy with minor deliverability risks. Review suggestions before launch."
    else:
        grade = "Spam Risk"
        grade_color = "#EF4444"
        verdict = "High Risk of landing in Spam or Promotions. Fix highlighted trigger words and formatting."

    return {
        "score": final_score,
        "grade": grade,
        "grade_color": grade_color,
        "verdict": verdict,
        "issues": issues,
        "passes": passes,
        "detected_spam_words": detected_words,
        "word_count": total_body_words,
        "link_count": link_count,
        "subject_word_count": len(subject.split()) if subject else 0
    }

# ------------------------------------------------------------------------------

def highlight_spam_triggers(body_html: str, triggers: List[str]) -> str:
    """
    Wrap each detected spam trigger word/phrase in a highlighted <mark> span
    inside the HTML body so the user sees exactly which words triggered the flag.
    Operates on the plain-text sections of the HTML, not inside tag attributes.
    Returns the annotated HTML string unchanged if triggers is empty.
    """
    if not body_html or not triggers:
        return body_html

    result = body_html
    for trigger in sorted(triggers, key=len, reverse=True):  # longest first to avoid partial overlaps
        escaped = re.escape(trigger)
        # Match whole-word occurrences (not inside HTML tags)
        pattern = re.compile(
            rf'(?i)(?<![<\w])({escaped})(?![\w>])',
        )
        replacement = (
            r'<mark style="background:#FEF08A; color:#92400E; '
            r'font-weight:700; padding:0 2px; border-radius:2px;">\1</mark>'
        )
        result = pattern.sub(replacement, result)
    return result


def format_email_html(raw_body: str) -> str:
    """
    Ensure the email body is formatted in clean HTML paragraphs.
    If the text already contains block HTML tags (<p, <div, <table, <br, etc.),
    it is returned as-is. Otherwise, double newlines are converted to <p>...</p>.
    """
    if not raw_body or not raw_body.strip():
        return ""
    text = raw_body.strip()
    has_block_tags = any(tag in text.lower() for tag in ["<p", "<div", "<table", "<br", "<h1", "<h2", "<h3", "<h4", "<ul", "<ol"])
    if has_block_tags:
        return text
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return f"<p>{text}</p>"
    return "".join(f"<p style='margin: 0 0 1em 0;'>{p}</p>" for p in paragraphs)


def resolve_template(template_body: str, contact: Dict[str, Any]) -> str:
    """
    Unified template resolution:
    1. Injects contact variables ([Name], [Company], custom vars).
    2. Resolves Spintax {option1|option2}.
    """
    injected = inject_variables(template_body, contact)
    return parse_spintax(injected)
