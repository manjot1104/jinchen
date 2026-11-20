"""File-based inbox helpers for the Jinchen POC."""

import errno
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from connector.poc import DEFAULT_WEIGHT, ConfigError, require_test_mode


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = PROJECT_ROOT / "inbox" / "in"
DONE_DIR = PROJECT_ROOT / "inbox" / "done"
RESULTS_DIR = PROJECT_ROOT / "inbox" / "results"
CONFIG_PATH = PROJECT_ROOT / "config.json"


def _validate_config(data, label="Config"):
    """Validate a config dict and return normalized mode, password, and weight."""
    if not isinstance(data, dict):
        raise ConfigError(f"{label} is not a valid JSON object.")

    mode = data.get("mode")
    live_password = data.get("live_password") or ""
    require_test_mode(mode, live_password)
    return {"mode": mode, "live_password": live_password, "weight": _weight(data.get("weight"))}


def load_config(path=None):
    """Read config.json and refuse to continue unless test mode is clean.

    Returns {"mode": ..., "live_password": ..., "weight": {...}}. The weight
    block (US-081) is optional; a missing key takes DEFAULT_WEIGHT. Raises
    ConfigError with the
    sentences already in connector.poc when the mode is wrong or a live
    password is set in test mode. The password is never printed.
    """
    config_path = Path(path) if path is not None else CONFIG_PATH
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError("config.json was not found at %s." % config_path)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ConfigError("config.json is not a valid JSON object.")
    return _validate_config(data, label="config.json")


WEIGHT_SENTENCE = "config.json weight needs kg_per_piece above 0 and loss_allowance from 0 to 1."


def _weight(block):
    if block is None:
        return dict(DEFAULT_WEIGHT)
    if not isinstance(block, dict):
        raise ConfigError(WEIGHT_SENTENCE)
    weight = dict(DEFAULT_WEIGHT)
    weight.update(block)
    per_piece = weight["kg_per_piece"]
    allowance = weight["loss_allowance"]
    for value in (per_piece, allowance):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(WEIGHT_SENTENCE)
    if per_piece <= 0 or not 0 <= allowance <= 1:
        raise ConfigError(WEIGHT_SENTENCE)
    return {"kg_per_piece": float(per_piece), "loss_allowance": float(allowance)}

STOCK_KINDS = {"issue", "return", "disposition", "receipt"}

QUEUED_MESSAGE = (
    "Switch A is on. Logged only. Stock was not posted. "
    "Turning Switch A off does not post the queue."
)


def list_inbox(inbox_dir=None):
    """Return JSON message files from inbox/in in filename order."""
    directory = Path(inbox_dir) if inbox_dir is not None else INBOX_DIR

    if not directory.exists():
        return []

    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == ".json"
        ),
        key=lambda path: path.name,
    )


def _write_result_atomic(results_directory, filename, result_data):
    """Write result JSON to a temp file and atomically replace results/<filename>."""
    result_file = results_directory / filename
    tmp_fd, tmp_name = tempfile.mkstemp(dir=results_directory, prefix=".tmp-", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(tmp_fd, "w", encoding="utf-8")
        except BaseException:
            os.close(tmp_fd)
            raise
        with handle:
            handle.write(json.dumps(result_data, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    try:
        os.replace(tmp_name, result_file)
    except OSError as exc:
        return str(exc)
    return ""


def _next_dup_counter(done_directory, stem, suffix):
    pattern = re.compile(rf"^{re.escape(stem)}\.dup(\d+){re.escape(suffix)}$")
    highest = 0
    for entry in done_directory.iterdir():
        match = pattern.match(entry.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _move_to_done_unique(source, done_directory):
    """Move source into done_directory without overwriting an existing file."""
    target = done_directory / source.name
    stem = target.stem
    suffix = target.suffix
    counter = 0
    while True:
        candidate = target if counter == 0 else done_directory / f"{stem}.dup{counter}{suffix}"
        try:
            os.link(source, candidate)
        except FileExistsError:
            if counter == 0:
                counter = _next_dup_counter(done_directory, stem, suffix)
            else:
                counter += 1
            continue
        except OSError as exc:
            if exc.errno not in (errno.EXDEV, errno.EACCES, errno.EPERM):
                raise
            try:
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if counter == 0:
                    counter = _next_dup_counter(done_directory, stem, suffix)
                else:
                    counter += 1
                continue
            os.close(fd)
            try:
                os.replace(source, candidate)
            except BaseException:
                try:
                    candidate.unlink()
                except OSError:
                    pass
                raise
            return candidate
        source.unlink()
        return candidate


def read_next(
    connector,
    inbox_dir=None,
    done_dir=None,
    results_dir=None,
    switch_a=False,
    source_file=None,
):
    """Read one file, save its result, then move it unless it is queued."""
    if source_file is not None:
        source = Path(source_file)
        if not source.is_file():
            return None
    else:
        files = list_inbox(inbox_dir)
        if not files:
            return None
        source = files[0]

    done_directory = Path(done_dir) if done_dir is not None else DONE_DIR
    results_directory = (
        Path(results_dir) if results_dir is not None else RESULTS_DIR
    )

    try:
        message = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        message = None

    move_to_done = True

    if not isinstance(message, dict):
        result_data = {
            "status": "Rejected",
            "code": "E-ITEM",
            "document": "",
            "message": "Inbox file must contain a valid JSON object.",
        }
    elif switch_a and message.get("kind") in STOCK_KINDS:
        result_data = {
            "status": "Queued",
            "code": "",
            "document": "",
            "message": QUEUED_MESSAGE,
        }
        move_to_done = False
    else:
        try:
            result = connector.process(
                message,
                drop_log="log-fails" in source.name,
            )
        except Exception:  # the last guard: the next file must still be read
            result = None

        if result is None:
            result_data = {
                "status": "Business Rejected",
                "code": "E-ITEM",
                "document": "",
                "message": "File could not be processed.",
            }
        else:
            result_data = {
                "status": result.status,
                "code": result.code,
                "document": result.document,
                "message": result.message,
            }

    done_directory.mkdir(parents=True, exist_ok=True)
    results_directory.mkdir(parents=True, exist_ok=True)

    if move_to_done:
        done_target = _move_to_done_unique(source, done_directory)
        result_name = done_target.name
    else:
        result_name = source.name

    write_err = _write_result_atomic(results_directory, result_name, result_data)
    if write_err:
        result_data["message"] = f"{result_data['message']} (result write failed: {write_err})"

    return result_data


def read_all(
    connector,
    switch_a=False,
    inbox_dir=None,
    done_dir=None,
    results_dir=None,
    config_path=None,
    include_names=False,
):
    """Read the current inbox in filename order, respecting Switch A.

    The config is checked before the first file is touched. A filled live
    password or a bad mode raises ConfigError and nothing moves.
    """
    load_config(config_path)
    files = list_inbox(inbox_dir)
    results = []

    for source in files:
        result = read_next(
            connector,
            inbox_dir=inbox_dir,
            done_dir=done_dir,
            results_dir=results_dir,
            switch_a=switch_a,
            source_file=source,
        )

        if result is not None:
            results.append((source.name, result) if include_names else result)

    return results


def copy_samples(inbox_dir=None, samples_dir=None):
    """Copy sample JSON files only when the inbox has no JSON messages."""
    destination = Path(inbox_dir) if inbox_dir is not None else INBOX_DIR
    sample_directory = (
        Path(samples_dir) if samples_dir is not None else PROJECT_ROOT / "inbox" / "samples"
    )

    if list_inbox(destination):
        return {
            "copied": 0,
            "files": [],
            "message": "Inbox is not empty. No samples were copied.",
        }

    sample_files = list_inbox(sample_directory)
    if not sample_files:
        return {
            "copied": 0,
            "files": [],
            "message": "No JSON sample files were found.",
        }

    destination.mkdir(parents=True, exist_ok=True)
    copied = []

    for source_file in sample_files:
        target_file = destination / source_file.name

        # Exclusive creation prevents overwriting an existing file.
        with source_file.open("rb") as source_stream:
            with target_file.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream)

        copied.append(source_file.name)

    return {
        "copied": len(copied),
        "files": copied,
        "message": f"Copied {len(copied)} sample files into the inbox.",
    }
