"""
launcher.py - Unified desktop launcher for Sellomize Reach.
Boots both the background Email Dispatch Scheduler and the Streamlit Web UI.
Includes single-instance protection, file logging, and reliable server-ready browser launch.
"""

import os
import sys
import socket
import threading
import time
import webbrowser
import logging
import traceback

# 1. Setup persistent application data and logging directory
APPDATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SellomizeReach")
os.makedirs(APPDATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(APPDATA_DIR, "sellomize.log")

# 2. Redirect stdout/stderr if running in windowed/GUI mode (sys.stdout is None)
class StreamToLogger:
    """Redirect writes to Python logger and log file."""
    def __init__(self, logger_func):
        self.logger_func = logger_func
        self.buffer = ""

    def write(self, message):
        if not message:
            return
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8", errors="replace")
            except Exception:
                message = str(message)
        elif not isinstance(message, str):
            message = str(message)

        self.buffer += message
        if "\n" in self.buffer:
            lines = self.buffer.split("\n")
            for line in lines[:-1]:
                line_str = line.strip()
                if line_str:
                    try:
                        self.logger_func(line_str)
                    except Exception:
                        pass
            self.buffer = lines[-1]

    def flush(self):
        if self.buffer.strip():
            try:
                self.logger_func(self.buffer.strip())
            except Exception:
                pass
            self.buffer = ""

    def isatty(self):
        return False

    @property
    def encoding(self):
        return "utf-8"

    @property
    def errors(self):
        return "replace"

# Configure root and launcher loggers
file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [Launcher] %(message)s"))

handlers = [file_handler]
if sys.stdout is not None:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [Launcher] %(message)s"))
    handlers.append(stream_handler)

logging.basicConfig(level=logging.INFO, handlers=handlers)
logger = logging.getLogger("launcher")

# Redirect sys.stdout / sys.stderr if None to prevent NoneType attribute errors
if sys.stdout is None:
    sys.stdout = StreamToLogger(logger.info)
if sys.stderr is None:
    sys.stderr = StreamToLogger(logger.error)

def show_error_dialog(message: str, title: str = "Sellomize Reach"):
    """Display native Windows error alert if anything fails."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10)
    except Exception:
        pass

def is_server_listening(host: str = "127.0.0.1", port: int = 8501) -> bool:
    """Check if the local server port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except (OSError, ConnectionRefusedError):
        return False

def get_base_dir() -> str:
    """Find root bundle directory containing app.py and assets."""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass and os.path.exists(os.path.join(meipass, "app.py")):
            return meipass
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "app.py")):
            return exe_dir
        internal_dir = os.path.join(exe_dir, "_internal")
        if os.path.exists(os.path.join(internal_dir, "app.py")):
            return internal_dir
        return meipass or exe_dir
    return os.path.dirname(os.path.abspath(__file__))

def start_background_scheduler(stop_event: threading.Event):
    try:
        base_dir = get_base_dir()
        for mod_name in ["database", "contacts_handler", "smtp_dispatcher", "llm_engine", "tracker", "scheduler"]:
            py_path = os.path.join(base_dir, f"{mod_name}.py")
            if os.path.exists(py_path):
                try:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location(mod_name, py_path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[mod_name] = mod
                        spec.loader.exec_module(mod)
                except Exception:
                    pass
        from scheduler import start_scheduler_loop
        logger.info("Initializing Email dispatch background thread...")
        start_scheduler_loop(interval=60, stop_event=stop_event)
    except Exception as e:
        logger.error(f"Failed to run background scheduler: {e}")

def wait_for_server_and_open_browser(url: str = "http://localhost:8501", port: int = 8501, timeout: float = 40.0):
    """
    Polls until Streamlit is actively listening on port 8501 before opening the browser.
    Prevents 'ERR_CONNECTION_REFUSED' while Streamlit boots.
    """
    logger.info(f"Waiting for local server on port {port} to become active...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_server_listening("127.0.0.1", port):
            time.sleep(0.5)
            logger.info(f"Server is active! Opening browser at {url}...")
            webbrowser.open(url)
            return
        time.sleep(0.4)

    logger.warning("Timeout waiting for server port; opening browser anyway...")
    webbrowser.open(url)

def main():
    logger.info("==================================================")
    logger.info("SELLOMIZE REACH - STARTING APPLICATION")
    logger.info(f"Executable: {sys.executable}")
    logger.info(f"Log Path: {LOG_FILE}")
    logger.info("==================================================")

    # 1. Single Instance Check: If already running and listening, just open browser and exit
    if is_server_listening("127.0.0.1", 8501):
        logger.info("Sellomize Reach is already running on port 8501. Opening browser and focusing...")
        webbrowser.open("http://localhost:8501")
        return

    base_dir = get_base_dir()
    app_path = os.path.join(base_dir, "app.py")

    if not os.path.exists(app_path):
        err = f"Fatal: app.py not found at '{app_path}'. Installation may be corrupted."
        logger.error(err)
        show_error_dialog(err, "Sellomize Reach Startup Error")
        return

    # Ensure offline tiktoken cache is recognized
    tiktoken_cache = os.path.join(base_dir, "tiktoken_cache")
    if os.path.exists(tiktoken_cache):
        os.environ["TIKTOKEN_CACHE_DIR"] = tiktoken_cache
        logger.info(f"Using offline tiktoken cache at: {tiktoken_cache}")

    logger.info(f"Base Directory: {base_dir}")
    logger.info(f"App Script: {app_path}")

    # 2. Start background Dispatch Scheduler thread
    stop_event = threading.Event()
    sched_thread = threading.Thread(
        target=start_background_scheduler,
        args=(stop_event,),
        daemon=True,
        name="SellomizeSchedulerThread"
    )
    sched_thread.start()

    # 3. Schedule reliable browser launch that waits for port to be ready
    browser_thread = threading.Thread(
        target=wait_for_server_and_open_browser,
        args=("http://localhost:8501", 8501, 35.0),
        daemon=True,
        name="BrowserLauncherThread"
    )
    browser_thread.start()

    # 4. Launch Streamlit Web Engine
    try:
        import streamlit.web.cli as stcli
    except Exception as e:
        err = f"Failed to import Streamlit engine: {e}\n\n{traceback.format_exc()}"
        logger.error(err)
        show_error_dialog(err, "Sellomize Reach Startup Error")
        return

    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port=8501",
        "--browser.serverAddress=localhost",
        "--browser.gatherUsageStats=false",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=true"
    ]

    try:
        logger.info("Launching Streamlit CLI...")
        sys.exit(stcli.main())
    except SystemExit:
        stop_event.set()
    except KeyboardInterrupt:
        logger.info("Shutting down Sellomize Reach...")
        stop_event.set()
    except Exception as ex:
        err = f"Unhandled exception during execution: {ex}\n\n{traceback.format_exc()}"
        logger.exception(err)
        show_error_dialog(err, "Sellomize Reach Error")
        stop_event.set()

if __name__ == "__main__":
    try:
        main()
    except Exception as fatal_e:
        err = f"Fatal crash in Sellomize Reach:\n\n{fatal_e}\n\n{traceback.format_exc()}"
        try:
            with open(os.path.join(APPDATA_DIR, "crash.log"), "a", encoding="utf-8") as f:
                f.write(f"\n[{time.ctime()}] {err}\n")
        except Exception:
            pass
        show_error_dialog(err, "Sellomize Reach Fatal Error")