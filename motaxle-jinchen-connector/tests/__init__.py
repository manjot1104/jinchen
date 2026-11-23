"""Package-level test setup: isolate the inbox and server logs into a temporary directory."""
import atexit
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

_temp_dir = tempfile.TemporaryDirectory()
_temp_path = Path(_temp_dir.name)

# Create inbox subdirectories in temp
_in_dir = _temp_path / "in"
_done_dir = _temp_path / "done"
_results_dir = _temp_path / "results"
_in_dir.mkdir(parents=True, exist_ok=True)
_done_dir.mkdir(parents=True, exist_ok=True)
_results_dir.mkdir(parents=True, exist_ok=True)

# Patch connector.inbox
import connector.inbox

connector.inbox.INBOX_DIR = _in_dir
connector.inbox.DONE_DIR = _done_dir
connector.inbox.RESULTS_DIR = _results_dir

# Patch server LOG_PATH if present (from PR #11)
try:
    import server

    if hasattr(server, "LOG_PATH"):
        server.LOG_PATH = _temp_path / "log.jsonl"
except ImportError:
    pass


def _cleanup():
    _temp_dir.cleanup()


atexit.register(_cleanup)
