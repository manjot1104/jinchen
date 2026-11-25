"""Retry engine for transient technical failures and Dead status handling."""

from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

from connector.poc import ConfigError

DEFAULT_RETRY = {
    "max_attempts": 5,
    "initial_delay_seconds": 1.0,
    "backoff_multiplier": 2.0,
}

RETRY_SENTENCE = "config.json retry settings are invalid."
_STATE_LOCK = threading.Lock()

TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    socket.timeout,
    http.client.RemoteDisconnected,
)


def validate_retry_config(block):
    """Validate optional retry config block and return normalized settings."""
    if block is None:
        return dict(DEFAULT_RETRY)
    if not isinstance(block, dict):
        raise ConfigError(RETRY_SENTENCE)

    cfg = dict(DEFAULT_RETRY)
    cfg.update(block)

    max_attempts = cfg["max_attempts"]
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ConfigError(RETRY_SENTENCE)

    initial_delay = cfg["initial_delay_seconds"]
    if isinstance(initial_delay, bool) or not isinstance(initial_delay, (int, float)) or initial_delay <= 0:
        raise ConfigError(RETRY_SENTENCE)

    multiplier = cfg["backoff_multiplier"]
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or multiplier <= 1:
        raise ConfigError(RETRY_SENTENCE)

    return {
        "max_attempts": max_attempts,
        "initial_delay_seconds": float(initial_delay),
        "backoff_multiplier": float(multiplier),
    }


def classify_failure(code="", exc=None):
    """Classify a failure as 'retryable', 'auth', 'business', or 'error'."""
    if code == "E-AUTH":
        return "auth"
    if code == "E-TIMEOUT":
        return "retryable"
    if exc is not None and isinstance(exc, TRANSIENT_EXCEPTIONS):
        return "retryable"
    if code:
        return "business"
    return "error"


def _atomic_write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        with _STATE_LOCK:
            os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


class RetryManager:
    """Manage technical retries, append-only attempt history, and Dead rows."""

    def __init__(self, config=None, state_path=None, clock=None, sleeper=None):
        self.config = validate_retry_config(config)
        self.state_path = Path(state_path) if state_path is not None else None
        self.clock = clock or datetime.now
        self.sleeper = sleeper or time.sleep
        self.rows = {}
        self._load_state()

    def _load_state(self):
        if self.state_path is not None and self.state_path.exists():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.rows = dict(data.get("rows", {}))

    def _save_state(self):
        if self.state_path is not None:
            _atomic_write_json(self.state_path, {"rows": self.rows})

    @property
    def exception_list(self):
        """Return all rows that exhausted retries and transitioned to Dead."""
        return [row for row in self.rows.values() if row.get("status") == "Dead"]

    def _next_delay(self, retry_index):
        return self.config["initial_delay_seconds"] * (
            self.config["backoff_multiplier"] ** retry_index
        )

    def run(self, row_id, work_order, transport_fn):
        """Execute transport_fn with retry only for E-TIMEOUT and dropped connections.

        max_attempts is the total number of attempts (1 initial + up to
        max_attempts - 1 retries). Repeated calls for an already terminal row
        (Posted, Dead, Business Rejected, or E-AUTH) do not add duplicate attempts.
        """
        existing = self.rows.get(row_id)
        if existing is not None and existing.get("status") in (
            "Posted",
            "Dead",
            "Business Rejected",
            "Auth Rejected",
        ):
            return existing

        row = existing or {
            "id": row_id,
            "work_order": work_order,
            "status": "Pending",
            "code": "",
            "document": "",
            "message": "",
            "attempts": [],
        }
        self.rows[row_id] = row

        max_attempts = self.config["max_attempts"]
        while len(row["attempts"]) < max_attempts:
            attempt_num = len(row["attempts"]) + 1
            if attempt_num > 1:
                delay = self._next_delay(attempt_num - 2)
                self.sleeper(delay)

            ts = self.clock().isoformat()
            try:
                result = transport_fn()
            except Exception as exc:
                code = "E-CONN" if isinstance(exc, TRANSIENT_EXCEPTIONS) else "E-ERR"
                kind = classify_failure(code=code, exc=exc)
                if kind == "retryable" and attempt_num < max_attempts:
                    outcome = "retry_scheduled"
                elif kind == "retryable":
                    outcome = "exhausted"
                else:
                    outcome = "failed_non_retryable"
                row["attempts"].append({
                    "attempt": attempt_num,
                    "timestamp": ts,
                    "code": code,
                    "classification": kind,
                    "outcome": outcome,
                    "detail": type(exc).__name__,
                })
                row["code"] = code
                row["message"] = "Transient connection failure." if kind == "retryable" else "Transport error."
                if kind != "retryable":
                    row["status"] = "Failed"
                    self._save_state()
                    return row
                if attempt_num >= max_attempts:
                    row["status"] = "Dead"
                    self._save_state()
                    return row
                self._save_state()
                continue

            status = result.get("status", "")
            code = result.get("code", "")
            if status == "Posted":
                row["attempts"].append({
                    "attempt": attempt_num,
                    "timestamp": ts,
                    "code": code,
                    "classification": "success",
                    "outcome": "posted",
                    "detail": result.get("document", ""),
                })
                row["status"] = "Posted"
                row["code"] = code
                row["document"] = result.get("document", "")
                row["message"] = "Posted."
                self._save_state()
                return row

            kind = classify_failure(code=code)
            if kind == "retryable":
                outcome = "retry_scheduled" if attempt_num < max_attempts else "exhausted"
                row["attempts"].append({
                    "attempt": attempt_num,
                    "timestamp": ts,
                    "code": code,
                    "classification": kind,
                    "outcome": outcome,
                    "detail": "Technical timeout.",
                })
                row["code"] = code
                row["message"] = "Technical timeout."
                if attempt_num >= max_attempts:
                    row["status"] = "Dead"
                    self._save_state()
                    return row
                self._save_state()
                continue

            # Non-retryable: E-AUTH or business rejection
            row["attempts"].append({
                "attempt": attempt_num,
                "timestamp": ts,
                "code": code,
                "classification": kind,
                "outcome": "non_retryable",
                "detail": code,
            })
            row["code"] = code
            row["status"] = "Auth Rejected" if kind == "auth" else "Business Rejected"
            row["message"] = "Authentication rejected." if kind == "auth" else "Business rejected."
            self._save_state()
            return row

        row["status"] = "Dead"
        self._save_state()
        return row
