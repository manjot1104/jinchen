"""Exception list, health view and replay (US-029).

Everything here reads the message log. Only replay() can move stock, and it
does so through Connector.process, so the repeat rule still applies: a
replay of a Posted row returns the original document and posts nothing.
"""

from datetime import datetime

# Rows a person has to look at. Posted, Queued, Sent and Replayed are not failures.
FAILED_STATUSES = ("Business Rejected", "Technical Failed", "Dead")
# Who may open a payload and replay a row. A planner may not.
SUPPORT_ROLES = ("support",)

REFUSED_ROLE = "Only Support can open a payload or replay a row. %s was refused."
NO_ROW = "There is no log row %s."
NOTHING_TO_REPLAY = "Row %s has no message to replay."


def _parse(stamp):
    try:
        return datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None


def age_hours(stamp, now):
    """Hours from the row's time to now, to one decimal. None when the row has no time."""
    when = _parse(stamp)
    if when is None:
        return None
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return round(max((now - when).total_seconds(), 0) / 3600, 1)


def _key(row, index):
    # Rows with no id cannot be matched with a later attempt, so each stands alone.
    return row.repeat_key or "row%d" % index


def exception_list(connector, now=None):
    """Failed rows a person still has to act on, oldest first.

    A row drops off the list once a later attempt with the same repeat key is
    Posted. When the same message failed more than once, only its latest
    failure is listed, with the attempt count.
    """
    now = now or connector.clock()
    latest = {}
    for index, row in enumerate(connector.log):
        key = _key(row, index)
        if row.status == "Posted":
            latest.pop(key, None)
        elif row.status in FAILED_STATUSES:
            latest[key] = index
    listed = []
    for index in sorted(latest.values()):
        row = connector.log[index]
        received = row.received or {}
        listed.append({
            "row": index,
            "work_order": row.work_order,
            "message": " ".join(part for part in (row.kind, row.ref) if part),
            "item": received.get("item", ""),
            "qty": received.get("qty", ""),
            "uom": received.get("uom", ""),
            "code": row.code,
            "age_hours": age_hours(row.timestamp, now),
            "status": row.status,
            "attempts": row.attempts,
        })
    return listed


def _allowed(role):
    return str(role or "").strip().lower() in SUPPORT_ROLES


def _role_name(role):
    return str(role or "").strip() or "A user with no role"


def _row(connector, index):
    if isinstance(index, bool) or not isinstance(index, int):
        try:
            index = int(str(index))
        except ValueError:
            return None, None
    if not 0 <= index < len(connector.log):
        return None, index
    return connector.log[index], index


def open_payload(connector, index, role):
    """The stored payload of one row, for Support only. Secrets are already masked."""
    if not _allowed(role):
        return {"ok": False, "message": REFUSED_ROLE % _role_name(role)}
    row, index = _row(connector, index)
    if row is None:
        return {"ok": False, "message": NO_ROW % index}
    return {"ok": True, "row": index, "payload": dict(row.payload)}


def replay(connector, index, role):
    """Run one logged message again as a new attempt. The original row is kept.

    The answer carries the result. A Posted row's replay is a repeat (E-DUP):
    it returns the original document and creates no new one.
    """
    if not _allowed(role):
        return {"ok": False, "message": REFUSED_ROLE % _role_name(role)}
    row, index = _row(connector, index)
    if row is None:
        return {"ok": False, "message": NO_ROW % index}
    message = row.payload
    if not isinstance(message, dict) or not isinstance(message.get("kind"), str):
        return {"ok": False, "message": NOTHING_TO_REPLAY % index}
    connector._replay_of = index
    try:
        result = connector.process(dict(message), attempt_id="replay of row %d" % index)
    finally:
        connector._replay_of = -1
    return {
        "ok": True,
        "row": index,
        "status": result.status,
        "code": result.code,
        "document": result.document,
        "message": result.message,
    }


def health(connector):
    """Last success, last failure and the Dead count. Reads only; creates nothing."""
    last_success = ""
    last_failure = ""
    dead = 0
    for row in connector.log:
        if row.status == "Posted":
            last_success = row.timestamp or last_success
        elif row.status in FAILED_STATUSES:
            last_failure = row.timestamp or last_failure
        if row.status == "Dead":
            dead += 1
    return {
        "status": "ok",
        "last_success": last_success or "none yet",
        "last_failure": last_failure or "none yet",
        "dead": dead,
    }


def safe_health(connector):
    """health(), but a failed check says 'failed' with the reason, never a blank."""
    try:
        return health(connector)
    except Exception as exc:  # the view must still answer
        return {
            "status": "failed",
            "last_success": "failed",
            "last_failure": "failed",
            "dead": "failed",
            "message": "The health check failed: %s" % exc,
        }
