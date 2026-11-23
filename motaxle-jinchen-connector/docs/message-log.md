# Message log (US-025)

Every attempt is one row in the message log (`connector.log`, and `inbox/log.jsonl` when the desk keeps a log file). The log is not the stock ledger. A row is never deleted; a retry adds a new row.

## The twelve fields

| # | Field | Row key | Example |
|---|---|---|---|
| 1 | Message | `kind` | `issue` |
| 2 | Direction | `direction` | `Jinchen to ERPNext` (hold, resume, cancel and release are `ERPNext to Jinchen`) |
| 3 | Repeat key | `repeat_key` | `J-100\|WO-2401\|issue`; a confirmation uses its operation: `CF-10\|WO-2401\|op10` |
| 4 | Work order | `work_order` | `WO-2401` |
| 5 | Jinchen document id | `ref` | `J-100` |
| 6 | Item, quantity and unit as received and as posted | `received`, `posted` | received `{"item": "ST-20", "qty": "120", "uom": "Kg"}`, posted `{"item": "ST-20", "qty": 120.0, "uom": "Kg"}` |
| 7 | Heat, batch, serial | `batch`, `serial` inside `received` and `posted` | `H-441` |
| 8 | Status | `status` | one of the seven below |
| 9 | Error code | `code` | `E-DUP`, or empty |
| 10 | ERPNext document | `document` | `STE-0001`, or empty |
| 11 | Attempts | `attempts` | 1 for the first try, 2 for the next row with the same repeat key |
| 12 | Payload | `payload` | the message as received, with secrets masked |

## Status

Always one of: Queued, Sent, Posted, Business Rejected, Technical Failed, Dead, Replayed. Any other status is refused before the row is written. Switch B's "Stopped" on the desk is logged as Business Rejected.

## Secrets

A key whose name holds `password`, `token`, `secret` or `api_key`, at any depth of the message, is stored as `***` in `payload` and `received`. The value itself is never written.

## Rows from before US-025

A row written before these fields existed loads with them empty and `attempts` 1.
