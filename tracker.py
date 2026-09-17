"""
tracker.py - Open tracking micro-server and HTML pixel injection for Sellomize Reach.
Serves a 1x1 transparent pixel to detect recipient email opens, logs opens in SQLite,
and automatically updates contact status to 'Opened / Interested'.
"""

import os
import re
import socket
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from database import record_email_open, get_config

logger = logging.getLogger("tracker")

# Valid 1x1 Transparent PNG binary data
TRANSPARENT_PIXEL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4'
    b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\r\xefUe\x00\x00\x00\x00IEND\xaeB`\x82'
)

class TrackingRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for open tracking pixel and health check."""

    def log_message(self, format, *args):
        # Suppress standard noisy access logging; log only opens
        pass

    def do_GET(self):
        path = self.path or "/"

        # 1. Health check endpoint
        if path in ["/", "/health", "/ping"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(b'{"status":"online","service":"Sellomize Open Tracker"}')
            return

        # 2. Email Open Tracking Endpoint: /track/open/<email_id>.png
        open_match = re.search(r"/track/open/(\d+)", path)
        if open_match:
            email_id_str = open_match.group(1)
            try:
                email_id = int(email_id_str)
                record_email_open(email_id)
                logger.info(f"[Open Tracker] Tracked open event for Email ID #{email_id} (Client: {self.client_address[0]})")
            except Exception as e:
                logger.error(f"[Open Tracker] Error recording open for ID #{email_id_str}: {e}")

            # Return 1x1 transparent PNG with strict anti-cache headers
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(TRANSPARENT_PIXEL_PNG)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(TRANSPARENT_PIXEL_PNG)
            return

        # 404 for any other path
        self.send_response(404)
        self.end_headers()

_tracker_server: Optional[HTTPServer] = None
_tracker_thread: Optional[threading.Thread] = None

def is_port_in_use(port: int = 8502, host: str = "127.0.0.1") -> bool:
    """Check if tracking server port is already listening."""
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except (OSError, ConnectionRefusedError):
        return False

def start_tracking_server(port: int = 8502, host: str = "0.0.0.0") -> bool:
    """
    Start tracking HTTP server in a background daemon thread.
    Returns True if successfully started or already running.
    """
    global _tracker_server, _tracker_thread

    if is_port_in_use(port=port):
        logger.info(f"Open tracking server is already listening on port {port}.")
        return True

    try:
        server_address = (host, port)
        _tracker_server = HTTPServer(server_address, TrackingRequestHandler)
        _tracker_thread = threading.Thread(
            target=_tracker_server.serve_forever,
            daemon=True,
            name="SellomizeTrackerThread"
        )
        _tracker_thread.start()
        logger.info(f"Open tracking HTTP server started on {host}:{port}.")
        return True
    except Exception as e:
        logger.warning(f"Could not bind open tracking server on port {port}: {e}")
        return False

def stop_tracking_server():
    """Gracefully shutdown the tracking server."""
    global _tracker_server
    if _tracker_server:
        try:
            _tracker_server.shutdown()
            _tracker_server.server_close()
        except Exception:
            pass
        _tracker_server = None

def get_tracking_base_url() -> str:
    """Fetch user-configured tracking base URL or fallback to localhost."""
    url = get_config("tracking_base_url", "http://localhost:8502")
    if not url or not url.strip():
        url = "http://localhost:8502"
    return url.strip().rstrip("/")

def inject_tracking_pixel(html_content: str, email_id: int, base_url: Optional[str] = None) -> str:
    """
    Inject a 1x1 transparent tracking pixel image tag into HTML email body.
    Placed before </body> if present, or appended to the end of the email.
    """
    if not html_content:
        return html_content

    server_url = (base_url or get_tracking_base_url()).rstrip("/")
    pixel_url = f"{server_url}/track/open/{email_id}.png"
    pixel_tag = f'<img src="{pixel_url}" width="1" height="1" alt="" style="display:none !important; width:1px !important; height:1px !important; border:0 !important; max-height:0 !important; max-width:0 !important; opacity:0 !important; visibility:hidden !important;" />'

    # Check if pixel is already injected for this email_id
    if f"/track/open/{email_id}" in html_content:
        return html_content

    if "</body>" in html_content:
        return html_content.replace("</body>", f"{pixel_tag}</body>", 1)
    else:
        return f"{html_content}\n{pixel_tag}"
