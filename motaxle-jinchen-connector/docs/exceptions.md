# Exception list, health view and replay (US-029)

The **Exceptions** screen on the desk is for the Stores Manager and Support. It shows the messages that failed and still need a person, so a failed issue is seen the same day.

Everything on this screen is read from the message log (see [message-log.md](message-log.md)). The log is not the stock ledger.

## The list

| Column | What it shows |
|---|---|
| Work order | The work order on the message, for example WO-2401. |
| Message | The kind and Jinchen's id, for example `issue J-100`. |
| Item and qty | Item, quantity and unit as received. |
| Code | The error code, for example E-CLOSED. It can be read without opening the payload. |
| Age (h) | Hours since the failed attempt, to one decimal. `-` for a row written before rows carried a time. |
| Status | Business Rejected, Technical Failed or Dead. |

Rules:

- Posted rows are never listed. Queued, Sent and Replayed are not failures either.
- When the same message failed more than once, only its latest failure is listed, with the attempt count.
- A message leaves the list once a later attempt with the same repeat key is Posted.

## Replay

**Only Support can open a payload or replay a row.** A Planner, or anyone else, gets: *Only Support can open a payload or replay a row. Planner was refused.* Nothing is logged and no stock moves. On the desk, pick the role in **Acting as** above the list.

A replay runs the logged message again as a new attempt:

- The original row is kept as it was. 0 rows are deleted.
- The new row has `replay_of` set to the number of the row it replays, the attempt id `replay of row N`, and the next attempt count.
- If the cause is still true, the replay fails again with the same code, and stock does not move.
- After the cause is fixed (for example, WO-2401 resumed after a hold), the replay posts exactly once and the row stores the entry name.
- A replay of a Posted row, or a second replay of a row already fixed, is a repeat: E-DUP with the original document, and 0 new documents.
- A row with no message (for example, a file that was not valid JSON) cannot be replayed: *Row N has no message to replay.*

The payload shown to Support is the logged copy. Secrets in it are already masked as `***`.

## Health

| Field | What it shows |
|---|---|
| Check | `ok`, or `failed` when the health check itself fails. |
| Last success | The time of the latest Posted row, or `none yet`. |
| Last failure | The time of the latest Business Rejected, Technical Failed or Dead row, or `none yet`. |
| Dead | How many rows are Dead. |

When the check fails, every field says `failed`, never blank, with the reason below. The health view only reads the log. It creates no work order and no stock entry.

## Not in the POC

- Roles are picked on the desk. There is no sign-in, so the role is not proven; the full build takes it from the ERPNext user.
- `Technical Failed` and `Dead` rows are written to the main log when `Connector.process()` handles transient or non-retryable technical failures (US-027) and are listed in the Exceptions and Health views (`Dead` count).
- Times are the desk computer's local time.
