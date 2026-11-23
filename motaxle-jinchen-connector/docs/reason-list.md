# Reason list (US-012)

One shared list. Each reason maps to exactly one decision, and each decision to one warehouse, so a reason never means scrap on one order and rework on the next. These are the POC baseline reasons from `REASONS` in `connector/poc.py`. `tests/test_docs.py` checks that this table and the code agree.

| Reason | Decision | Warehouse |
|---|---|---|
| forge-crack | rejection | Rejection Store |
| rework-bend | rework | Rework Store |
| scale | scrap | Scrap Yard |

Duplicate or conflicting mappings: 0. A disposition whose reason is not in this list, or whose decision does not match its reason, is refused with E-REASON.

## Not done yet

- Production and Quality confirm these three reasons or replace them in writing.
- Signed by the Production Head and the Quality Head.

Until then this list is the POC baseline.
