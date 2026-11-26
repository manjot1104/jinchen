"""Retry policy, classification, and config validation for transient technical failures."""

import http.client
import math
import socket

from connector.poc import ConfigError

DEFAULT_RETRY = {
    "max_retries": 5,
    "initial_delay_seconds": 1.0,
    "backoff_multiplier": 2.0,
}

MAX_RETRIES_LIMIT = 10
MAX_INITIAL_DELAY_SECONDS = 300.0
TIMEOUT_CODE = "E-TIMEOUT"

TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    socket.timeout,
    http.client.RemoteDisconnected,
)

BUSINESS_CODES = frozenset({
    "E-DUP",
    "E-ITEM",
    "E-UOM",
    "E-WH",
    "E-BATCH",
    "E-STOCK",
    "E-QTY",
    "E-OP",
    "E-REASON",
    "E-QC",
    "E-CLOSED",
    "E-AMEND",
})


def validate_retry_config(block):
    """Validate optional retry config block and return normalized settings."""
    if block is None:
        return dict(DEFAULT_RETRY)
    if not isinstance(block, dict):
        raise ConfigError("config.json retry must be a JSON object.")

    unknown = sorted(set(block) - set(DEFAULT_RETRY))
    if unknown:
        raise ConfigError(f"config.json retry has unknown setting: {unknown[0]}.")

    cfg = dict(DEFAULT_RETRY)
    cfg.update(block)

    max_retries = cfg["max_retries"]
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or not (1 <= max_retries <= MAX_RETRIES_LIMIT)
    ):
        raise ConfigError(
            f"config.json retry max_retries must be a whole number from 1 to {MAX_RETRIES_LIMIT}."
        )

    initial_delay = cfg["initial_delay_seconds"]
    if (
        isinstance(initial_delay, bool)
        or not isinstance(initial_delay, (int, float))
        or not math.isfinite(initial_delay)
        or initial_delay <= 0
        or initial_delay > MAX_INITIAL_DELAY_SECONDS
    ):
        raise ConfigError(
            f"config.json retry initial_delay_seconds must be a finite number above 0 and at most {int(MAX_INITIAL_DELAY_SECONDS)}."
        )

    multiplier = cfg["backoff_multiplier"]
    if (
        isinstance(multiplier, bool)
        or not isinstance(multiplier, (int, float))
        or not math.isfinite(multiplier)
        or multiplier <= 1
    ):
        raise ConfigError(
            "config.json retry backoff_multiplier must be a finite number above 1."
        )

    return {
        "max_retries": max_retries,
        "initial_delay_seconds": float(initial_delay),
        "backoff_multiplier": float(multiplier),
    }


def classify_failure(status="", code="", exc=None):
    """Classify a failure as 'retryable', 'auth', 'business', or 'technical'."""
    if code == "E-AUTH":
        return "auth"
    if code == "E-TIMEOUT" or (exc is not None and isinstance(exc, TRANSIENT_EXCEPTIONS)):
        return "retryable"
    if status == "Business Rejected" or code in BUSINESS_CODES:
        return "business"
    return "technical"


def next_retry_delay(retry_config, retry_index):
    """Compute delay in seconds before retry attempt `retry_index` (0-based)."""
    delay = retry_config["initial_delay_seconds"] * (
        retry_config["backoff_multiplier"] ** retry_index
    )
    return min(delay, MAX_INITIAL_DELAY_SECONDS)
