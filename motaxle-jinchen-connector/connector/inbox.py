"""File-based inbox helpers for the Jinchen POC."""

import json
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = PROJECT_ROOT / "inbox" / "in"
DONE_DIR = PROJECT_ROOT / "inbox" / "done"
RESULTS_DIR = PROJECT_ROOT / "inbox" / "results"

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
        result = connector.process(
            message,
            drop_log="log-fails" in source.name,
        )

        result_data = {
            "status": result.status,
            "code": result.code,
            "document": result.document,
            "message": result.message,
        }

    done_directory.mkdir(parents=True, exist_ok=True)
    results_directory.mkdir(parents=True, exist_ok=True)

    result_file = results_directory / source.name
    result_file.write_text(
        json.dumps(result_data, indent=2) + "\n",
        encoding="utf-8",
    )

    if move_to_done:
        source.rename(done_directory / source.name)

    return result_data


def read_all(
    connector,
    switch_a=False,
    inbox_dir=None,
    done_dir=None,
    results_dir=None,
):
    """Read the current inbox in filename order, respecting Switch A."""
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
            results.append(result)

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
