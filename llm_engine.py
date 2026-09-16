"""
llm_engine.py - AI generation and revision engine using LiteLLM.
Handles prompt variations, variable injection, Spintax resolution,
negative keyword scanning, and model fallbacks.
"""

import os
import sys
import re
import json
import random
import logging
from typing import List, Dict, Any, Optional

# Ensure tiktoken uses local bundled cache if present
base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
tiktoken_cache = os.path.join(base_dir, "tiktoken_cache")
if os.path.exists(tiktoken_cache):
    os.environ["TIKTOKEN_CACHE_DIR"] = tiktoken_cache

import litellm
from litellm import completion

from database import get_all_configs

# Configure litellm logging
litellm.suppress_debug_info = True
logger = logging.getLogger("llm_engine")
logging.basicConfig(level=logging.INFO)

# ------------------------------------------------------------------------------
# API CREDENTIAL CONFIGURATION
# ------------------------------------------------------------------------------

def _setup_api_keys(configs: Dict[str, str]):
    """Set environment variables for LLM providers based on saved config."""
    gemini_key = configs.get("gemini_api_key", "").strip()
    openai_key = configs.get("openai_api_key", "").strip()
    anthropic_key = configs.get("anthropic_api_key", "").strip()
    gcp_project = configs.get("gcp_project_id", "").strip()

    if gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key
        os.environ["GOOGLE_API_KEY"] = gemini_key
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
    if anthropic_key:
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key
    if gcp_project:
        os.environ["VERTEX_PROJECT"] = gcp_project

def _build_spam_instruction(blocklist_str: str) -> str:
    """Build the spam restriction instruction if blocklist is provided."""
    words = [w.strip() for w in blocklist_str.split(",") if w.strip()]
    if not words:
        return ""
    clean_list = ", ".join(f'"{w}"' for w in words)
    return f"You are strictly forbidden from using any of the following words or phrases in your output: {clean_list}."

# ------------------------------------------------------------------------------
# TEXT PROCESSING: VARIABLE INJECTION & SPINTAX
# ------------------------------------------------------------------------------

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
        try:
            custom_vars = json.loads(contact_data["custom_variables"] or "{}")
        except Exception:
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

def scan_negative_keywords(text: str, negative_keywords_str: str) -> Optional[str]:
    """
    Run a text scan against the saved negative_keywords list.
    If a negative word or phrase is detected, returns the first detected trigger word.
    Otherwise returns None.
    """
    if not text or not negative_keywords_str:
        return None

    # Strip HTML tags for clean text scanning
    plain_text = re.sub(r'<[^>]+>', ' ', text)

    keywords = [kw.strip() for kw in negative_keywords_str.split(",") if kw.strip()]
    for kw in keywords:
        # Match whole words or phrases case-insensitively
        escaped_kw = re.escape(kw)
        pattern = rf'(?:\b|_){escaped_kw}(?:\b|_)'
        if re.search(pattern, plain_text, re.IGNORECASE):
            return kw

    return None

# ------------------------------------------------------------------------------
# LITELLM COMPLETION CALLS
# ------------------------------------------------------------------------------

def call_litellm_with_fallback(
    messages: List[Dict[str, str]],
    primary_model: str,
    fallback_model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500
) -> str:
    """Execute completion call with automatic model fallback."""
    configs = get_all_configs()
    _setup_api_keys(configs)

    primary_model = primary_model.strip() if primary_model else "gemini/gemini-1.5-flash"
    fallback_model = fallback_model.strip() if fallback_model else None

    kwargs = {
        "model": primary_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    if fallback_model and fallback_model != primary_model:
        kwargs["fallbacks"] = [fallback_model]

    try:
        response = completion(**kwargs)
        content = response.choices[0].message.content
        return content.strip()
    except Exception as primary_err:
        logger.warning(f"Primary call failed with: {primary_err}")
        if fallback_model and fallback_model != primary_model:
            logger.info(f"Attempting manual fallback to: {fallback_model}")
            try:
                response = completion(
                    model=fallback_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                return response.choices[0].message.content.strip()
            except Exception as fb_err:
                raise RuntimeError(f"Both primary model ({primary_model}) and fallback ({fallback_model}) failed. Error: {fb_err}")
        else:
            raise RuntimeError(f"Model call failed: {primary_err}")

def generate_variations(
    playbook_prompt: str,
    num_variations: int = 1,
    configs: Optional[Dict[str, str]] = None
) -> List[Dict[str, str]]:
    """
    Generate 1 to 5 distinct email variations from a listing audit playbook/prompt.
    Strictly forbids spam words and rewrites structure/openings across variations.
    Outputs strict JSON schema: {"variations": [{"subject": "...", "body_html": "..."}]}.
    """
    if configs is None:
        configs = get_all_configs()

    primary_model = configs.get("primary_model", "gemini/gemini-1.5-flash")
    fallback_model = configs.get("fallback_model", "gpt-4o-mini")
    spam_blocklist = configs.get("spam_blocklist", "")

    spam_instruction = _build_spam_instruction(spam_blocklist)

    system_prompt = (
        "You are an elite B2B cold email strategist and copywriter specializing in listing audits and e-commerce growth.\n"
        "Your task is to write high-converting, professional, deliverable outreach emails.\n"
        "Format the email body strictly in clean HTML paragraphs (<p>...</p>, <strong>...</strong>, <br>, <ul><li>...</li></ul>).\n"
        "Do NOT include enclosing <html> or <body> tags, only the email body markup.\n"
        "Do NOT include signature placeholders at the bottom; a signature will be appended automatically.\n"
    )

    if spam_instruction:
        system_prompt += f"\nCRITICAL SPAM PREVENTION RULE:\n{spam_instruction}\n"

    user_prompt = (
        f"Listing Audit Playbook / Core Prompt:\n\"\"\"\n{playbook_prompt}\n\"\"\"\n\n"
        f"Task: Generate exactly {num_variations} distinct email variation(s).\n\n"
        "CRITICAL INSTRUCTION - STRICT JSON OUTPUT REQUIRED:\n"
        "You MUST output your entire response ONLY in strict JSON format with the following schema:\n"
        "{\n"
        "  \"variations\": [\n"
        "    {\n"
        "      \"subject\": \"Descriptive, high-converting subject line\",\n"
        "      \"body_html\": \"<p>Opening hook...</p><p>Core observation...</p><p>Call to action...</p>\"\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Variation Guidelines:\n"
        "- Keep the core context, offer, and structural intent identical, but completely rewrite the sentence structure, vocabulary, and opening hooks for each variation.\n"
        "- Format body_html strictly in clean HTML paragraphs (<p>...</p>, <strong>...</strong>, <br>, <ul><li>...</li></ul>).\n"
        "- Output ONLY the JSON object. Do not wrap in conversational preamble or closing remarks.\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    raw_output = call_litellm_with_fallback(
        messages=messages,
        primary_model=primary_model,
        fallback_model=fallback_model,
        temperature=0.8
    )

    # Parse strict JSON output from model
    variations = _parse_variations_json(raw_output, num_variations)
    return variations

def polish_campaign_email(
    resolved_body: str,
    contact_name: str,
    company_name: str,
    custom_instructions: str = "",
    configs: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """
    Polishes a Spintax/variable resolved email draft into high-converting HTML format using LiteLLM.
    Ensures proper HTML formatting and generates a customized subject line.
    """
    if configs is None:
        configs = get_all_configs()

    primary_model = configs.get("primary_model", "gemini/gemini-1.5-flash")
    fallback_model = configs.get("fallback_model", "gpt-4o-mini")
    spam_blocklist = configs.get("spam_blocklist", "")

    spam_instruction = _build_spam_instruction(spam_blocklist)

    system_prompt = (
        "You are an expert cold outreach copywriter.\n"
        "Given an outreach message template that has already been personalized with contact variables and Spintax,\n"
        "format it into clean, deliverable HTML paragraphs (<p>, <strong>, <br>).\n"
        "Do NOT include <html> or <body> tags, nor signature blocks.\n"
    )
    if spam_instruction:
        system_prompt += f"\nCRITICAL SPAM PREVENTION RULE:\n{spam_instruction}\n"

    user_prompt = (
        f"Contact: {contact_name} at {company_name}\n"
        f"Base Outreach Text:\n\"\"\"\n{resolved_body}\n\"\"\"\n\n"
    )
    if custom_instructions.strip():
        user_prompt += f"Special Instructions: {custom_instructions.strip()}\n\n"

    user_prompt += (
        "Respond ONLY in valid JSON with keys 'subject' and 'body_html'.\n"
        "Example:\n"
        "{\"subject\": \"Quick question regarding Company catalog\", \"body_html\": \"<p>Hi Name...</p>\"}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    raw_output = call_litellm_with_fallback(
        messages=messages,
        primary_model=primary_model,
        fallback_model=fallback_model,
        temperature=0.7
    )

    return _parse_single_email_json(raw_output, f"<p>{resolved_body}</p>")

def rewrite_email(
    rejected_draft_html: str,
    revision_instructions: str,
    configs: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """
    Revise an existing rejected email draft based on specific user feedback.
    Applies: 'Revise the following email draft by strictly applying these user instructions: [instructions].'
    """
    if configs is None:
        configs = get_all_configs()

    primary_model = configs.get("primary_model", "gemini/gemini-1.5-flash")
    fallback_model = configs.get("fallback_model", "gpt-4o-mini")
    spam_blocklist = configs.get("spam_blocklist", "")

    spam_instruction = _build_spam_instruction(spam_blocklist)

    system_prompt = (
        "You are an expert copy editor revising cold outreach emails.\n"
        "Return the revised draft formatted in clean HTML paragraphs (<p>...</p>, <strong>, etc.).\n"
        "Do NOT include enclosing <html> or <body> tags, nor signature placeholders.\n"
    )
    if spam_instruction:
        system_prompt += f"\nCRITICAL SPAM PREVENTION RULE:\n{spam_instruction}\n"

    user_prompt = (
        f"Existing Email Draft:\n\"\"\"\n{rejected_draft_html}\n\"\"\"\n\n"
        f"Revise the following email draft by strictly applying these user instructions: {revision_instructions}.\n\n"
        "Respond in valid JSON format with keys 'subject' and 'body_html'.\n"
        "Example:\n"
        "{\"subject\": \"Updated Subject Line\", \"body_html\": \"<p>Revised text...</p>\"}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    raw_output = call_litellm_with_fallback(
        messages=messages,
        primary_model=primary_model,
        fallback_model=fallback_model,
        temperature=0.7
    )

    result = _parse_single_email_json(raw_output, rejected_draft_html)
    return result

def auto_rewrite_negative_keyword(
    email_html: str,
    trigger_word: str,
    configs: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """
    Auto-rewrite an email to eliminate a detected negative keyword.
    Enforces instruction: 'Rewrite this email to remove the negative keyword: [Trigger Word]'.
    """
    instruction = f"Rewrite this email to remove the negative keyword: '{trigger_word}'. Replace it with natural, compliant wording while preserving the core offer and structure."
    return rewrite_email(rejected_draft_html=email_html, revision_instructions=instruction, configs=configs)

# ------------------------------------------------------------------------------
# PARSING HELPERS
# ------------------------------------------------------------------------------

def _parse_variations_json(raw_output: str, expected_count: int) -> List[Dict[str, str]]:
    """Robustly extract and parse JSON variations list from LLM output."""
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "variations" in parsed:
            results = []
            for item in parsed["variations"]:
                results.append({
                    "subject": str(item.get("subject", "Audit Inquiry")).strip(),
                    "body_html": str(item.get("body_html", "")).strip()
                })
            if results:
                return results[:expected_count]
        elif isinstance(parsed, list):
            results = []
            for item in parsed:
                subj = str(item.get("subject", "Audit Inquiry")).strip()
                body = str(item.get("body_html", "")).strip()
                results.append({"subject": subj, "body_html": body})
            if results:
                return results[:expected_count]
    except Exception as e:
        logger.warning(f"Failed direct JSON parse: {e}. Falling back to regex extraction.")

    # Fallback heuristic: find JSON object containing variations
    match_dict = re.search(r"\{\s*\"variations\"\s*:\s*\[.*?\]\s*\}", cleaned, re.DOTALL)
    if match_dict:
        try:
            parsed_dict = json.loads(match_dict.group(0))
            if "variations" in parsed_dict:
                return [{
                    "subject": str(it.get("subject", "Audit Inquiry")).strip(),
                    "body_html": str(it.get("body_html", "")).strip()
                } for it in parsed_dict["variations"]][:expected_count]
        except Exception:
            pass

    # Fallback heuristic: find bare JSON array
    match_list = re.search(r"\[\s*\{.*?\}\s*\]", cleaned, re.DOTALL)
    if match_list:
        try:
            items = json.loads(match_list.group(0))
            return [{
                "subject": str(it.get("subject", "Audit Inquiry")).strip(),
                "body_html": str(it.get("body_html", "")).strip()
            } for it in items][:expected_count]
        except Exception:
            pass

    # Fallback to returning raw output wrapped in HTML paragraph
    paragraphs = "\n".join(f"<p>{p.strip()}</p>" for p in cleaned.split("\n\n") if p.strip())
    return [{"subject": "Listing Audit Follow-up", "body_html": paragraphs}]

def _parse_single_email_json(raw_output: str, fallback_html: str) -> Dict[str, str]:
    """Parse single email JSON output."""
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return {
                "subject": str(parsed.get("subject", "Outreach Inquiry")).strip(),
                "body_html": str(parsed.get("body_html", fallback_html)).strip()
            }
    except Exception:
        pass

    # Look for json object in output
    match = re.search(r"\{\s*\"subject\":.*?\"body_html\":.*?\}", cleaned, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return {
                "subject": str(parsed.get("subject", "Outreach Inquiry")).strip(),
                "body_html": str(parsed.get("body_html", fallback_html)).strip()
            }
        except Exception:
            pass

    # Fallback wrap
    paragraphs = "\n".join(f"<p>{p.strip()}</p>" for p in cleaned.split("\n\n") if p.strip())
    return {"subject": "Outreach Inquiry", "body_html": paragraphs}
