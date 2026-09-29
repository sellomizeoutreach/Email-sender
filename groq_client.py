"""
groq_client.py - Groq API Client for Sellomize Reach AI Studio.

Features:
- High-speed multimodal (Vision + Text) chat completions via Groq OpenAI-compatible API.
- Native Vision support for analyzing Amazon listing screenshots, audit images, and storefronts.
- Automatic extraction and monitoring of rate limit headers (requests, tokens, reset timers).
- Zero heavy dependencies (uses standard library urllib + PIL for image handling).
"""

import os
import json
import base64
import io
import urllib.request
import urllib.error
from typing import Tuple, Dict, Any, Optional, List
from PIL import Image

DEFAULT_GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
DEFAULT_TEXT_MODEL   = "qwen/qwen3.8-27b"
GROQ_API_URL         = "https://api.groq.com/openai/v1/chat/completions"


def prepare_image_for_groq(image_bytes: bytes) -> Tuple[str, str]:
    """
    Validates, resizes, and base64-encodes an image for Groq Vision input.
    Ensures image is at least 32x32 (Groq requirement) and caps dimensions at 1600px
    to optimize token usage and response latency.
    Returns (base64_data_uri, mime_type).
    """
    img = Image.open(io.BytesIO(image_bytes))

    # Convert RGBA/P to RGB if JPEG, or keep RGBA if PNG
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    width, height = img.size

    # Ensure minimum 32x32 pixels
    if width < 32 or height < 32:
        new_w = max(width, 32)
        new_h = max(height, 32)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        width, height = new_w, new_h

    # Cap maximum dimensions to 1600px to maintain speed & stay well within token limits
    max_dim = 1600
    if width > max_dim or height > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    out_buf = io.BytesIO()
    fmt = "PNG" if img.mode == "RGBA" else "JPEG"
    mime = f"image/{fmt.lower()}"
    
    if fmt == "JPEG":
        img.save(out_buf, format="JPEG", quality=88, optimize=True)
    else:
        img.save(out_buf, format="PNG", optimize=True)

    b64_str = base64.b64encode(out_buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{b64_str}", mime


def parse_rate_limits(headers) -> Dict[str, Any]:
    """Extracts rate limit and quota information from HTTP response headers."""
    limits: Dict[str, Any] = {
        "limit_requests": 1000,
        "remaining_requests": 1000,
        "reset_requests": "0s",
        "limit_tokens": 8000,
        "remaining_tokens": 8000,
        "reset_tokens": "0s",
        "request_used_pct": 0.0,
        "request_remaining_pct": 100.0,
        "token_used_pct": 0.0,
        "token_remaining_pct": 100.0,
        "status": "healthy"
    }

    try:
        req_lim = headers.get("x-ratelimit-limit-requests")
        if req_lim:
            limits["limit_requests"] = int(req_lim)

        req_rem = headers.get("x-ratelimit-remaining-requests")
        if req_rem is not None:
            limits["remaining_requests"] = int(req_rem)

        req_rst = headers.get("x-ratelimit-reset-requests")
        if req_rst:
            limits["reset_requests"] = str(req_rst)

        tok_lim = headers.get("x-ratelimit-limit-tokens")
        if tok_lim:
            limits["limit_tokens"] = int(tok_lim)

        tok_rem = headers.get("x-ratelimit-remaining-tokens")
        if tok_rem is not None:
            limits["remaining_tokens"] = int(tok_rem)

        tok_rst = headers.get("x-ratelimit-reset-tokens")
        if tok_rst:
            limits["reset_tokens"] = str(tok_rst)

        # Calculate percentages
        if limits["limit_requests"] > 0:
            used = limits["limit_requests"] - limits["remaining_requests"]
            limits["request_used_pct"] = round((used / limits["limit_requests"]) * 100, 1)
            limits["request_remaining_pct"] = round(100.0 - limits["request_used_pct"], 1)

        if limits["limit_tokens"] > 0:
            used_tok = limits["limit_tokens"] - limits["remaining_tokens"]
            limits["token_used_pct"] = round((used_tok / limits["limit_tokens"]) * 100, 1)
            limits["token_remaining_pct"] = round(100.0 - limits["token_used_pct"], 1)

        # Status level
        if limits["request_remaining_pct"] <= 10.0 or limits["token_remaining_pct"] <= 10.0:
            limits["status"] = "critical"
        elif limits["request_remaining_pct"] <= 30.0 or limits["token_remaining_pct"] <= 30.0:
            limits["status"] = "warning"
        else:
            limits["status"] = "healthy"

    except Exception:
        pass

    return limits


def call_groq_completion(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    image_bytes: Optional[bytes] = None,
    model: str = DEFAULT_VISION_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 800,
) -> Tuple[str, Dict[str, Any]]:
    """
    Executes a chat completion call against Groq API.
    Supports both text-only and multimodal vision with base64 images.
    Returns (completion_text, rate_limits_dict).
    """
    if not api_key or not api_key.strip():
        raise ValueError("Groq API key is missing. Please provide a valid key.")

    messages: List[Dict[str, Any]] = []

    if system_prompt and system_prompt.strip():
        messages.append({
            "role": "system",
            "content": system_prompt.strip()
        })

    if image_bytes:
        data_uri, _ = prepare_image_for_groq(image_bytes)
        user_content = [
            {"type": "text", "text": user_prompt.strip()},
            {"type": "image_url", "image_url": {"url": data_uri}}
        ]
        messages.append({
            "role": "user",
            "content": user_content
        })
    else:
        messages.append({
            "role": "user",
            "content": user_prompt.strip()
        })

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    req = urllib.request.Request(
        GROQ_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "SellomizeReach/2.4"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            rate_limits = parse_rate_limits(resp.headers)
            content = data["choices"][0]["message"]["content"]
            return content.strip(), rate_limits

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else ""
        rate_limits = parse_rate_limits(e.headers)
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", err_body)
        except Exception:
            msg = err_body
        raise RuntimeError(f"Groq API Error ({e.code}): {msg}")

    except Exception as e:
        raise RuntimeError(f"Failed to connect to Groq API: {str(e)}")


def probe_groq_quota(api_key: str) -> Dict[str, Any]:
    """Lightweight 1-token probe to fetch fresh rate limit headers without burning quota."""
    try:
        _, limits = call_groq_completion(
            api_key=api_key,
            system_prompt="",
            user_prompt="ping",
            model="qwen/qwen3.8-27b",
            max_tokens=1
        )
        return limits
    except Exception as e:
        return {
            "error": str(e),
            "status": "unknown",
            "limit_requests": 1000,
            "remaining_requests": 0,
            "reset_requests": "unknown",
            "request_remaining_pct": 0.0,
            "request_used_pct": 100.0,
        }


def parse_email_output(raw_ai_text: str) -> Tuple[str, str]:
    """
    Parses AI generated text into (subject, body).
    Looks for SUBJECT: and BODY: tags, or intelligently splits first line as subject.
    """
    lines = raw_ai_text.strip().splitlines()
    subject = ""
    body_lines = []
    in_body = False

    for line in lines:
        line_clean = line.strip()
        if not subject and (line_clean.upper().startswith("SUBJECT:") or line_clean.upper().startswith("**SUBJECT:**")):
            subject = line_clean.split(":", 1)[1].strip().strip("*").strip()
            continue

        if line_clean.upper().startswith("BODY:") or line_clean.upper().startswith("**BODY:**"):
            in_body = True
            continue

        if not in_body and not subject and line_clean.startswith("#"):
            subject = line_clean.lstrip("#").strip()
            continue

        body_lines.append(line)

    body = "\n".join(body_lines).strip()

    # Fallback if no explicit subject was found
    if not subject:
        if len(lines) > 1 and len(lines[0]) < 80:
            subject = lines[0].strip().strip("*# ")
            body = "\n".join(lines[1:]).strip()
        else:
            subject = "Quick question regarding [Company]"

    return subject, body
