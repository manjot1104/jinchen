# Message contract (US-013)

**POC contract, not a signed Jinchen API.** These JSON fields are what the local stand-in accepts. When the Jinchen API pack and the signed interface note arrive, compare them with this page field by field. A field that differs is a change to the connector, not to this page.

Every message is one JSON object. A file or a typed message that is not an object returns **E-ITEM** *Unknown message.* Numbers are JSON numbers. A numeric string such as `"4"` is accepted, but `"abc"`, `true`, or a blank string is not a number.

## Codes

Eleven codes. E-CLOSED was added on 18 November 2026 (US-082, decision D-7 in [decisions.md](decisions.md)).

| Code | Meaning |
|---|---|
| E-DUP | The same id, work order and kind was already applied. Status stays Posted and the first document is returned. For confirmations, the repeat key is id + work_order + operation, the document is empty, and a repeated confirmation logs the original stored quantities. |
| E-ITEM | Wrong or missing item, unknown kind, or a return against an issue that was not posted. |
| E-UOM | Wrong unit. |
| E-WH | Wrong warehouse on an issue. |
| E-BATCH | Wrong heat or batch, or a receipt without an output batch. |
| E-STOCK | Not enough free stock. |
| E-QTY | Missing id or work order, a quantity that is not a number, zero or negative, or above what the order, the issue or the steel allows. |
| E-OP | Operation not on the route, or resume on an order that is not on hold. |
| E-REASON | Reason missing or not matching the decision, or scrap sent as a return. |
| E-QC | A receipt aimed at Finished Goods Store. Good pieces wait in Final QC Hold. |
| E-CLOSED | The work order is on hold or cancelled. |

## Fields on every message

| Field | Type | Required | Allowed values | When wrong |
|---|---|---|---|---|
| kind | string | yes | issue, return, confirm, disposition, receipt, hold, resume, cancel | E-ITEM *Unknown message.* |
| id | string | yes | any; the repeat key is id + work_order + kind (for confirmations, id + work_order + operation) | E-QTY *Id and work order are required.* |
| work_order | string | yes | WO-2401 in the samples | E-QTY *Id and work order are required.*; E-CLOSED when the order is on hold or cancelled |

## issue (sample 01)

| Field | Type | Required | Allowed values | When wrong |
|---|---|---|---|---|
| item | string | yes | ST-20 (exact; ST20 is refused) | E-ITEM *Item does not match ST-20.* |
| qty | number, kg | yes | above 0, not above the free stock | E-QTY *Quantity must be greater than zero.*; E-STOCK *Not enough free stock of H-441.* |
| uom | string | yes | Kg | E-UOM *Issue unit must be Kg.* |
| batch | string | yes | H-441 (exact; H441 is refused) | E-BATCH *Heat does not match H-441.* |
| from_warehouse | string | yes | Raw Material Store | E-WH *Issue must move from Raw Material Store to Forging WIP.* |
| to_warehouse | string | yes | Forging WIP | E-WH, same sentence |

## return (sample 02)

| Field | Type | Required | Allowed values | When wrong |
|---|---|---|---|---|
| against | string | yes | the id of a posted issue on the same work order | E-ITEM *Original issue was not posted.* |
| item | string | yes | the item of that issue | E-BATCH *Return must be ST-20 heat H-441.* |
| batch | string | yes | the heat of that issue | E-BATCH, same sentence |
| qty | number, kg | yes | above 0, not above what is still out on that issue | E-QTY *Return is above the quantity still out.* |
| uom | string | no | Kg when present | E-UOM *Return unit must be Kg.* |
| as_scrap | boolean | no | false or absent | E-REASON *Scrap is not a return.* |

## confirm (samples 03 and 04)

| Field | Type | Required | Allowed values | When wrong |
|---|---|---|---|---|
| operation | integer | yes | 10 or 20; 30 is vendor work and is ignored | E-OP *Operation is not on the demo route.* |
| completed | number, pieces | yes | 0 or more | E-QTY *A quantity is negative.* |
| rejected | number, pieces | no (0) | 0 or more | E-QTY, same sentence |
| rework | number, pieces | no (0) | 0 or more | E-QTY, same sentence |
| scrap | number, pieces | no (0) | 0 or more | E-QTY, same sentence |

Checks across the fields, in this order:

1. A repeated confirmation (same id, work order and operation) returns Posted with E-DUP *Already confirmed. Not stored again.* and an empty document, logging the stored quantities. This repeat check runs first, before the closed-state check (E-CLOSED) and validation checks.
2. completed + rejected + rework + scrap is not above the order of 100: E-QTY *Completed pieces are above the order.* (US-079)
3. Steel is out on this work order (issued minus returned above 0): E-QTY *No metal was issued on this work order.* (US-080)
4. The pieces × kg_per_piece is not above the steel out × (1 + loss_allowance), both from `config.json`: E-QTY *Pieces need more steel than was issued.* (US-081)

A confirmation never creates a stock entry. The four quantities are stored separately on the log row.

## disposition (samples 05, 06 and 07)

| Field | Type | Required | Allowed values | When wrong |
|---|---|---|---|---|
| item | string | yes | CR-100 | E-ITEM *Disposition item is not CR-100.* |
| decision | string | yes | rejection, rework, scrap | E-REASON *Reason is blank or does not match the decision.* |
| reason | string | yes | forge-crack (rejection), rework-bend (rework), scale (scrap) | E-REASON, same sentence |
| qty | number, pieces | yes | above 0 | E-QTY *Quantity must be greater than zero.* |

A disposition never opens a CAPA.

## receipt (samples 08 and 09)

| Field | Type | Required | Allowed values | When wrong |
|---|---|---|---|---|
| item | string | yes | CR-100 | E-ITEM *Receipt item is not CR-100.* |
| qty | number, pieces | yes | above 0 | E-QTY *Quantity must be greater than zero.* |
| uom | string | no | Nos when present | E-UOM *Receipt unit must be Nos.* |
| batch | string | yes | the rod batch, for example B-1 | E-BATCH *Output batch is required.* |
| to_warehouse | string | no | absent or Final QC Hold | E-QC *Saleable receipt is blocked. Hold stays with quality.* |

## hold, resume, cancel (desk Work order screen)

Only `kind`, `id` and `work_order`. No stock moves.

| Kind | Effect | When refused |
|---|---|---|
| hold | issue, return, confirm, disposition and receipt on the order return E-CLOSED *Work order is on hold.* | E-CLOSED if already cancelled |
| resume | the order is open again | E-OP *Work order is not on hold.*; E-CLOSED if cancelled |
| cancel | final; every message returns E-CLOSED *Work order is cancelled.*, and the desk Release is refused | E-QTY *Steel is still out. Return or scrap it before cancel.* |
