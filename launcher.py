"""
launcher.py - Unified desktop launcher for Sellomize Reach.
Boots both the background Outlook Dispatch Scheduler and the Streamlit Web UI.
"""

import os
import sys
import threading
import time
import webbrowser
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Launcher] %(message)s"
)
logger = logging.getLogger("launcher")

def get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def start_background_scheduler(stop_event: threading.Event):
    try:
        from scheduler import start_scheduler_loop
        logger.info("Initializing Outlook dispatch background thread...")
        start_scheduler_loop(interval=60, stop_event=stop_event)
    except Exception as e:
        logger.error(f"Failed to run background scheduler: {e}")

def open_browser_delayed(url: str = "http://localhost:8501", delay: float = 2.5):
    time.sleep(delay)
    logger.info(f"Opening default browser at {url}...")
    webbrowser.open(url)

def main():
    base_dir = get_base_dir()
    app_path = os.path.join(base_dir, "app.py")

    # Ensure offline tiktoken cache is recognized
    tiktoken_cache = os.path.join(base_dir, "tiktoken_cache")
    if os.path.exists(tiktoken_cache):
        os.environ["TIKTOKEN_CACHE_DIR"] = tiktoken_cache
        logger.info(f"Using offline tiktoken cache at: {tiktoken_cache}")

    logger.info("==================================================")
    logger.info("SELLOMIZE REACH - AGENCY EMAIL AUTOMATION")
    logger.info(f"Base Directory: {base_dir}")
    logger.info(f"App Script: {app_path}")
    logger.info("==================================================")

    # 1. Start background Outlook Scheduler thread
    stop_event = threading.Event()
    sched_thread = threading.Thread(
        target=start_background_scheduler,
        args=(stop_event,),
        daemon=True,
        name="SellomizeSchedulerThread"
    )
    sched_thread.start()

    # 2. Schedule automatic browser launch
    browser_thread = threading.Thread(
        target=open_browser_delayed,
        args=("http://localhost:8501", 2.5),
        daemon=True,
        name="BrowserLauncherThread"
    )
    browser_thread.start()

    # 3. Launch Streamlit Web Engine
    import streamlit.web.cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port=8501",
        "--browser.serverAddress=localhost",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=true"
    ]

    try:
        sys.exit(stcli.main())
    except SystemExit:
        stop_event.set()
    except KeyboardInterrupt:
        logger.info("Shutting down Sellomize Reach...")
        stop_event.set()

if __name__ == "__main__":
    main()