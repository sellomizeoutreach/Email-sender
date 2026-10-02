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
from typing import List, Dict, Any, Optional, Set, Tuple

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


def inject_variables(template_text: str, contact_data: Dict[str, Any], client_story_key: Optional[str] = None) -> str:
    """
    Replace bracketed variables in a template (e.g., [Name], [Company], [Location], [Product], [ASIN], etc.)
    with corresponding contact/research data.
    Supports 21 Sellomize prospect/Amazon research variables:
    [Name], [First Name], [Company], [Product], [ASIN], [AmazonIssue], [RelevantService],
    [SpecificObservation], [SupportingObservation], [Location], [Role], [Keyword],
    [OrganicRank], [AdRank], [ListingScore], [Rating], [ReviewCount], [BoughtPastMonth],
    [BSR], [ClientStory], [ClientStoryFound], [ClientStorySolved], [ClientStoryRewarded].

    Strict Fact-Safety Rules:
    - Fallback 'Hi [Name],' -> 'Hi,' if contact name is empty or missing (never invent a name).
    - Location is only populated if verified; otherwise omitted cleanly.
    - [Company] + [Location] + Sellomize falls back to [Company] + Sellomize if location unverified.
    - Missing variables are omitted cleanly without leaving dangling brackets or broken spacing.
    """
    if not template_text:
        return ""

    # Build mapping from contact data
    var_map: Dict[str, str] = {}
    full_name = str(contact_data.get("name") or "").strip()
    first_name = full_name.split()[0] if full_name else ""
    if full_name:
        var_map["name"] = full_name
        var_map["firstname"] = first_name
        var_map["first_name"] = first_name

    if "email" in contact_data and contact_data["email"]:
        var_map["email"] = str(contact_data["email"]).strip()
    if "company" in contact_data and contact_data["company"]:
        var_map["company"] = str(contact_data["company"]).strip()

    # Extract custom variables
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
        val_str = str(v).strip()
        if val_str:
            var_map[k.lower()] = val_str
            var_map[k.lower().replace(" ", "").replace("_", "")] = val_str

    # Strict Location Handling
    raw_loc = (
        custom_vars.get("Verified Location") or
        custom_vars.get("verified_location") or
        custom_vars.get("Location") or
        custom_vars.get("location") or
        contact_data.get("country_or_timezone") or
        ""
    ).strip()
    is_verified_loc = bool(
        raw_loc and
        raw_loc.upper() not in ["LOCAL", "UTC", "UTC+5", "UNKNOWN", "N/A", "NONE", ""] and
        not raw_loc.startswith("GMT") and
        not raw_loc.startswith("UTC")
    )
    if is_verified_loc:
        var_map["location"] = raw_loc
        var_map["verifiedlocation"] = raw_loc

    # Amazon Research 21 Variables Mapping
    var_map["product"] = str(custom_vars.get("Product") or custom_vars.get("product") or "").strip()
    var_map["asin"] = str(custom_vars.get("ASIN") or custom_vars.get("asin") or "").strip()
    var_map["amazonissue"] = str(custom_vars.get("Amazon Issues") or custom_vars.get("amazon_issue") or custom_vars.get("Listing Issues") or custom_vars.get("pain_point") or "").strip()
    var_map["relevantservice"] = str(custom_vars.get("Relevant Service") or custom_vars.get("relevant_service") or "").strip()
    var_map["specificobservation"] = str(custom_vars.get("Brand Observation") or custom_vars.get("specific_observation") or contact_data.get("notes") or "").strip()
    var_map["supportingobservation"] = str(custom_vars.get("Supporting Observation") or custom_vars.get("supporting_observation") or "").strip()
    var_map["role"] = str(custom_vars.get("Role") or custom_vars.get("role") or "").strip()
    var_map["keyword"] = str(custom_vars.get("Keyword") or custom_vars.get("keyword") or "").strip()
    var_map["organicrank"] = str(custom_vars.get("Organic Rank") or custom_vars.get("organic_rank") or "").strip()
    var_map["adrank"] = str(custom_vars.get("Ad Rank") or custom_vars.get("ad_rank") or "").strip()
    var_map["listingscore"] = str(custom_vars.get("Listing Score") or custom_vars.get("listing_score") or "").strip()
    var_map["rating"] = str(custom_vars.get("Rating") or custom_vars.get("rating") or "").strip()
    var_map["reviewcount"] = str(custom_vars.get("Review Count") or custom_vars.get("review_count") or "").strip()
    var_map["boughtpastmonth"] = str(custom_vars.get("Bought Past Month") or custom_vars.get("bought_past_month") or "").strip()
    var_map["bsr"] = str(custom_vars.get("BSR") or custom_vars.get("bsr") or "").strip()

    # Aliases
    aliases = {
        "observation": ["specific_observation", "specificobservation", "brandobservation", "amazonobservation", "listingissue", "listingissues"],
        "specific_observation": ["observation", "specificobservation", "brandobservation", "amazonobservation"],
        "pain_point": ["painpoint", "amazonissue", "amazonissues", "listingissues"],
        "painpoint": ["pain_point", "amazonissue", "amazonissues"],
        "compliment": ["praise"],
        "offer_angle": ["offerangle", "angle"],
        "offerangle": ["offer_angle", "angle"],
        "trigger_event": ["triggerevent", "trigger"],
        "triggerevent": ["trigger_event", "trigger"],
        "proof_story": ["proofstory", "case_study", "casestudy", "clientstory"],
        "proofstory": ["proof_story", "case_study", "casestudy", "clientstory"],
        "client_story": ["clientstory", "proof_story", "proofstory"],
        "first_name": ["firstname", "first"],
        "firstname": ["first_name", "first"]
    }
    for canon, syns in aliases.items():
        if canon in var_map and var_map[canon]:
            for s in syns:
                if s not in var_map or not var_map[s]:
                    var_map[s] = var_map[canon]
        else:
            for s in syns:
                if s in var_map and var_map[s]:
                    var_map[canon] = var_map[s]
                    break

    # Client story resolution if passed
    if client_story_key:
        try:
            from sellomize_templates import APPROVED_CLIENT_STORIES
            if client_story_key in APPROVED_CLIENT_STORIES:
                cs = APPROVED_CLIENT_STORIES[client_story_key]
                var_map["clientstoryfound"] = cs["found"]
                var_map["clientstorysolved"] = cs["solved"]
                var_map["clientstoryrewarded"] = cs["rewarded"]
                var_map["clientstory"] = (
                    f"What we found: {cs['found']}\n"
                    f"How we solved it: {cs['solved']}\n"
                    f"What it rewarded: {cs['rewarded']}"
                )
        except Exception as e:
            logger.debug(f"Could not load APPROVED_CLIENT_STORIES: {e}")

    # Special Location subject line logic:
    # [Company] + [Location] + Sellomize -> [Company] + Sellomize if location missing
    text = template_text
    if "[Company] + [Location] + Sellomize" in text and not is_verified_loc:
        text = text.replace("[Company] + [Location] + Sellomize", "[Company] + Sellomize")

    # Name Fallback: "Hi [Name]," or "Hi [First Name]," -> "Hi," if no name
    if not full_name:
        text = re.sub(r'Hi\s+\[Name\],', 'Hi,', text, flags=re.IGNORECASE)
        text = re.sub(r'Hi\s+\[First Name\],', 'Hi,', text, flags=re.IGNORECASE)
        text = re.sub(r'Hi\s+\[firstname\],', 'Hi,', text, flags=re.IGNORECASE)

    # Known variables set to cleanly omit if unverified
    KNOWN_CLEAN_OMIT = {
        "location", "verifiedlocation", "product", "asin", "amazonissue", "relevantservice",
        "specificobservation", "supportingobservation", "role", "keyword", "organicrank",
        "adrank", "listingscore", "rating", "reviewcount", "boughtpastmonth", "bsr",
        "clientstory", "clientstoryfound", "clientstorysolved", "clientstoryrewarded"
    }

    # Regex to match [variable_name]
    def replacer(match):
        raw_key = match.group(1).strip()
        token = raw_key.lower().replace(" ", "").replace("_", "")
        # Check standard token lookup
        if token in var_map and var_map[token]:
            return var_map[token]
        raw_tok = raw_key.lower()
        if raw_tok in var_map and var_map[raw_tok]:
            return var_map[raw_tok]
        # If it's a known Sellomize variable that is empty/unverified, omit cleanly
        if token in KNOWN_CLEAN_OMIT or raw_tok in KNOWN_CLEAN_OMIT:
            return ""
        # Return original token if no matching variable found
        return match.group(0)

    # Regex to match {variable_name} when NOT spintax (no pipe)
    def curly_replacer(match):
        inner = match.group(1).strip()
        if "|" in inner:
            return match.group(0)
        token = inner.lower().replace(" ", "").replace("_", "")
        if token in var_map and var_map[token]:
            return var_map[token]
        raw_tok = inner.lower()
        if raw_tok in var_map and var_map[raw_tok]:
            return var_map[raw_tok]
        if token in KNOWN_CLEAN_OMIT or raw_tok in KNOWN_CLEAN_OMIT:
            return ""
        return match.group(0)

    result = re.sub(r'\[([a-zA-Z0-9_\s-]+)\]', replacer, text)
    result = re.sub(r'\{([a-zA-Z0-9_\s-]+)\}', curly_replacer, result)

    # If [AmazonIssue] is missing/omitted, smooth out awkward phrasing like "note about for [Company]"
    if not var_map.get("amazonissue"):
        result = re.sub(r'note about\s+for\s+', 'note about ', result, flags=re.IGNORECASE)
        result = re.sub(r'note regarding\s+for\s+', 'note regarding ', result, flags=re.IGNORECASE)

    # Clean up double spaces caused by clean variable omission
    result = re.sub(r'[ \t]{2,}', ' ', result)
    result = re.sub(r' \n', '\n', result)
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
    Ensure the email body is formatted in clean HTML paragraphs and inline markdown rules are applied:
    - **bold** -> <strong style="font-weight:700;">bold</strong>
    - *italic* -> <em>italic</em>
    - ~~strike~~ -> <del>strike</del>
    - [text](url) -> <a href="url"...>text</a>
    """
    if not raw_body or not raw_body.strip():
        return ""
    text = raw_body.replace('\r\n', '\n').replace('\r', '\n').strip()

    # Apply inline markdown formatting if present
    if "**" in text:
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong style="font-weight:700;">\1</strong>', text)
    if "*" in text:
        text = re.sub(r'(?<!\*)\*([^\*\n]+?)\*(?!\*)', r'<em>\1</em>', text)
    if "~~" in text:
        text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text)
    if "](" in text:
        def link_repl(match):
            label = match.group(1)
            url = match.group(2).strip()
            return f'<a href="{url}" style="color:#083731; font-weight:600; text-decoration:underline;">{label}</a>'
        text = re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+|mailto:[^\s\)]+|[^\s\)]+)\)', link_repl, text)

    has_block_tags = any(tag in text.lower() for tag in ["<p", "<div", "<table", "<br", "<h1", "<h2", "<h3", "<h4", "<ul", "<ol"])
    if has_block_tags:
        return text
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return f"<p style='margin: 0 0 1em 0;'>{text.replace(chr(10), '<br>')}</p>"
    return "".join(f"<p style='margin: 0 0 1em 0;'>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def resolve_template(template_body: str, contact: Dict[str, Any]) -> str:
    """
    Unified template resolution:
    1. Injects contact variables ([Name], [Company], custom vars).
    2. Resolves Spintax {option1|option2}.
    """
    injected = inject_variables(template_body, contact)
    return parse_spintax(injected)


# ------------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'\[([a-zA-Z0-9_\s\-]+)\]')


def _missing_tokens(text: str) -> List[str]:
    """Return list of unfilled [Token] placeholders remaining in text."""
    if not text:
        return []
    return list({f"[{m.group(1).strip()}]" for m in _TOKEN_RE.finditer(text)})


