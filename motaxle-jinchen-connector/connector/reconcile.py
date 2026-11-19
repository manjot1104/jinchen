"""Reconcile inbox results against the ledger (US-084).

Compares results in inbox/results against the in-memory stock ledger.
Read-only: creates nothing, posts nothing, writes no files.
"""

import json
from pathlib import Path


def reconcile(connector, results_dir):
    """Compare result files in results_dir against connector.ledger.entries.

    Returns a dict with five keys:
    - stock_results: number of result files with status Posted and non-empty document.
    - entries: len(connector.ledger.entries).
    - missing_entry: sorted list of file names whose document is not in ledger entries.
    - missing_result: sorted list of entry names that no stock result points to.
    - unreadable: sorted list of file names that are not a valid JSON object.
    """
    directory = Path(results_dir)
    ledger_entries = {entry.name for entry in connector.ledger.entries}

    stock_results_count = 0
    missing_entry = []
    unreadable = []
    referenced_entries = set()

    if directory.exists():
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            if path.name == "KEEP" or path.name.startswith("."):
                continue

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                unreadable.append(path.name)
                continue

            if not isinstance(data, dict):
                unreadable.append(path.name)
                continue

            status = data.get("status")
            document = (data.get("document") or "").strip()

            # Skip anything not Posted, and skip Posted with empty document (confirmations, op 30)
            if status != "Posted" or not document:
                continue

            stock_results_count += 1
            if document in ledger_entries:
                referenced_entries.add(document)
            else:
                missing_entry.append(path.name)

    missing_result = sorted(ledger_entries - referenced_entries)

    return {
        "stock_results": stock_results_count,
        "entries": len(connector.ledger.entries),
        "missing_entry": sorted(missing_entry),
        "missing_result": missing_result,
        "unreadable": sorted(unreadable),
    }
