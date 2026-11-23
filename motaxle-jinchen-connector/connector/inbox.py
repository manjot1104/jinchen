"""File-based inbox helpers for the Jinchen POC."""

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
    if not isinstance(data, dict):
        raise ConfigError("config.json is not a valid JSON object.")

    mode = data.get("mode")
    live_password = data.get("live_password") or ""
    require_test_mode(mode, live_password)
    return {"mode": mode, "live_password": live_password, "weight": _weight(data.get("weight"))}


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


def _default_file_mode():
    umask = os.umask(0o022)
    os.umask(umask)
    return 0o666 & ~umask


def _write_result_atomic(results_directory, filename, result_data):
    """Write result JSON to a temp file and atomically replace results/<filename>."""
    result_file = results_directory / filename
    tmp_name = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=results_directory, prefix=".tmp-", suffix=".tmp")
        try:
            handle = os.fdopen(tmp_fd, "w", encoding="utf-8")
        except BaseException:
            os.close(tmp_fd)
            raise
        with handle:
            handle.write(json.dumps(result_data, indent=2) + "\n")
        os.chmod(tmp_name, _default_file_mode())
        os.replace(tmp_name, result_file)
        tmp_name = None
        return ""
    except OSError as exc:
        return str(exc)
    finally:
        if tmp_name is not None and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _next_dup_counter(stem, suffix, *directories):
    pattern = re.compile(rf"^{re.escape(stem)}\.dup(\d+){re.escape(suffix)}$")
    highest = 0
    for directory in directories:
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            match = pattern.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _allocate_unique_name(filename, done_directory, results_directory):
    """Atomically reserve a unique filename checked against both done/ and results/."""
    base_path = Path(filename)
    stem = base_path.stem
    suffix = base_path.suffix
    counter = 0
    while True:
        candidate = filename if counter == 0 else f"{stem}.dup{counter}{suffix}"
        if not (done_directory / candidate).exists():
            try:
                fd = os.open(results_directory / candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _default_file_mode())
                os.close(fd)
                return candidate
            except FileExistsError:
                pass
        if counter == 0:
            counter = _next_dup_counter(stem, suffix, done_directory, results_directory)
        else:
            counter += 1


def _move_to_done(source, target):
    """Move source to target using O_CREAT|O_EXCL reservation, os.replace, and shutil.move fallback."""
    created_placeholder = False
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        created_placeholder = True
        os.close(fd)
        try:
            os.replace(source, target)
            return ""
        except OSError:
            if created_placeholder and target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
                created_placeholder = False
            shutil.move(str(source), str(target))
            return ""
    except OSError as exc:
        if created_placeholder and target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        return str(exc)


def _read_one(
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
        try:
            message = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            message = None

        move_to_done = True
        kind = message.get("kind") if isinstance(message, dict) else None

        if not isinstance(message, dict) or not isinstance(kind, str):
            result_data = {
                "status": "Rejected",
                "code": "E-ITEM",
                "document": "",
                "message": "Inbox file must contain a valid JSON object.",
                "logged": True,
            }
            if hasattr(connector, "record_attempt"):
                connector.record_attempt(
                    source.name,
                    "",
                    "",
                    "Business Rejected",
                    "E-ITEM",
                    "",
                    drop_log=False,
                    quantities=None,
                    attempt_id=source.name,
                )
        elif switch_a and kind in STOCK_KINDS:
            result_data = {
                "status": "Queued",
                "code": "",
                "document": "",
                "message": QUEUED_MESSAGE,
            }
            move_to_done = False
            logged = connector.record_queued(source.name, message)
            result_data["logged"] = logged
        else:
            drop_log = "log-fails" in source.name
            try:
                result = connector.process(
                    message,
                    drop_log=drop_log,
                    attempt_id=source.name,
                )
            except TypeError as te:
                if "unexpected keyword argument 'attempt_id'" in str(te) or "unexpected keyword argument: attempt_id" in str(te):
                    result = connector.process(
                        message,
                        drop_log=drop_log,
                    )
                else:
                    raise
            result_data = {
                "status": result.status,
                "code": result.code,
                "document": result.document,
                "message": result.message,
                "logged": not drop_log,
            }
    except Exception:  # the last guard: the next file must still be read
        move_to_done = True
        result_data = {
            "status": "Business Rejected",
            "code": "E-ITEM",
            "document": "",
            "message": "File could not be processed.",
            "logged": False,
        }

    done_directory.mkdir(parents=True, exist_ok=True)
    results_directory.mkdir(parents=True, exist_ok=True)

    final_name = _allocate_unique_name(source.name, done_directory, results_directory)
    write_err = _write_result_atomic(results_directory, final_name, result_data)
    if write_err:
        try:
            (results_directory / final_name).unlink()
        except OSError:
            pass
        result_data["message"] = f"{result_data['message']} (result write failed: {write_err})"
        return final_name, result_data

    if move_to_done:
        move_err = _move_to_done(source, done_directory / final_name)
        if move_err:
            result_data["message"] = f"{result_data['message']} (move to done failed: {move_err})"

    return final_name, result_data


def read_next(
    connector,
    inbox_dir=None,
    done_dir=None,
    results_dir=None,
    switch_a=False,
    source_file=None,
):
    """Read one file, save its result, then move it unless it is queued."""
    outcome = _read_one(
        connector,
        inbox_dir=inbox_dir,
        done_dir=done_dir,
        results_dir=results_dir,
        switch_a=switch_a,
        source_file=source_file,
    )
    if outcome is None:
        return None
    return outcome[1]


def read_all(
    connector,
    switch_a=False,
    inbox_dir=None,
    done_dir=None,
    results_dir=None,
    config_path=None,
):
    """Read the current inbox in filename order, respecting Switch A.

    The config is checked before the first file is touched. A filled live
    password or a bad mode raises ConfigError and nothing moves.
    Returns a list of (final_name, result) pairs.
    """
    load_config(config_path)
    files = list_inbox(inbox_dir)
    results = []

    for source in files:
        try:
            outcome = _read_one(
                connector,
                inbox_dir=inbox_dir,
                done_dir=done_dir,
                results_dir=results_dir,
                switch_a=switch_a,
                source_file=source,
            )
        except OSError as exc:
            outcome = (
                source.name,
                {
                    "status": "Business Rejected",
                    "code": "E-ITEM",
                    "document": "",
                    "message": f"File could not be read: {exc}",
                    "logged": False,
                },
            )

        if outcome is not None:
            results.append(outcome)

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
