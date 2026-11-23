# POC demonstration and acceptance (US-064)

How the POC is shown to the client, and the checklist they accept it against. There is no production floor and no go-live step.

## The demo

Run on one machine with the local stand-in:

1. `python3 -m unittest discover tests` ends with `OK`.
2. `python3 prove.py` prints `ALL PASS`.
3. `python3 server.py`, then follow [demo/SCRIPT.txt](../demo/SCRIPT.txt) screen by screen. It takes about 8 minutes.

## Acceptance checklist

The figures the client checks on the desk. Each one is covered by a test and by `prove.py`.

| # | Check | Expected |
|---|---|---|
| 1 | One issue of J-100 | Raw 380 kg, WIP 120 kg, one stock entry |
| 2 | J-100 sent again | Raw still 380, E-DUP, no second entry |
| 3 | Return R-1 | Raw 384, WIP 116 |
| 4 | Cutting and forging confirmed | Finished goods 0 |
| 5 | 2 rejected, 1 rework, 1 scrap | Finished goods 0, CAPA 0 |
| 6 | Receipts C-1 and C-2 | Final QC Hold 96 |
| 7 | Rejected pieces as saleable | Refused; hold stays 96 |
| 8 | Lookup of batch B-1 | Reads back to H-441 and WO-2401; H441 does not match |
| 9 | Restart of the desk | The ledger, confirmations and order state come back |
| 10 | No Jinchen call, no live ERPNext site | The demo's last line says so |

## Record of the demo

| Item | Value |
|---|---|
| Date | (to fill in) |
| Attendees | (to fill in) |
| Must stories Done, or with a written exception | (to fill in from Linear) |
| Defects found, with severity | (to fill in); 0 Critical open to accept |
| Accepted by Project Manager | (signature) |
| Accepted by Tech Lead | (signature) |

## Not done yet

- The demo run with the client, and the attendance list.
- The demo on the ERPNext test site (needs Part B). The stand-in demo above is ready.
- The acceptance signatures.
