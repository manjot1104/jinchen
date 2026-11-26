# Motaxle–Jinchen Practical POC

This folder runs the Jinchen practical POC on one machine with a local stand-in.

ERPNext stock is an in-memory ledger. A Jinchen message is a JSON object, typed on the desk or read from a file. Nothing is sent to a network. The JSON fields are the POC contract. They are not a signed Jinchen API.

Python 3.9 or newer, standard library only.

## 1. Clone

    git clone https://github.com/manjot1104/jinchen.git
    cd jinchen/motaxle-jinchen-connector

## 2. Tests

    python3 -m unittest discover tests

A pass ends with `OK`. The original twelve tests lock raw 380 after a repeated J-100, raw 384 after R-1, finished goods 0 after cutting, hold 96, and a lookup from batch B-1 back to heat H-441. The other files cover the refusal codes, the desk switches, the inbox reader, and the config check.

## 3. Demo

    python3 demo.py

Prints the whole job in the terminal: the live-password refusal at startup, J-100 posted once with its log write dropped, the same J-100 refused as E-DUP, R-1 once, cutting and forging, 2 rejected, 1 rework, 1 scrap, C-1 and C-2 into Final QC Hold, and the B-1 lookup. The last line says it did not call Jinchen and it did not call a live ERPNext site.

## Prove

`python3 prove.py` prints `ALL PASS`.

## 4. Server

    python3 server.py

Open http://127.0.0.1:8765. The port is required. The desk has Today, Work order, Material, Operations, Quality, Trace, Message log, and Inbox. Reset job returns the ledger to 500 kg and turns both switches off, moving processed files in `inbox/done/` and `inbox/results/` to `inbox/archive/<time>/done/` and `inbox/archive/<time>/results/` when the message log is cleared. Stop the server with Ctrl+C.

## 5. Copy samples, then Read inbox

Open Inbox. Press **Copy samples**: the nine files in `inbox/samples/` are copied into `inbox/in/`, only when `inbox/in/` holds no JSON file. Press **Read inbox**: the files are read in filename order, each result is written to `inbox/results/`, and each file moves to `inbox/done/`. After the nine files the tiles read raw 384, WIP 116, hold 96, finished 0, rejection 2, rework 1, scrap 1, seven stock entries, CAPA 0.

`inbox/examples/` holds eight deliberately bad files. Copy samples never reads that folder.

## 6. Switch A

With Switch A on, issue, return, disposition, and receipt files are logged as Queued and stay in `inbox/in/`. Confirmations are still processed. **Turning Switch A off does not flush the queue.** Press Read inbox again to post it. Switch A is a local control and does not call Jinchen.

## 7. Configuration

`config.json` has `mode`, `live_password`, `weight`, and optional `retry`. The committed file is test mode with an empty password. `server.py` and the inbox reader read it before they start. A filled password in test mode stops both with *Test mode refuses to start when a live password is set.* A mode other than `test` or `live` stops both with *Mode must be test or live.* Do not commit a password.

`weight` holds `kg_per_piece` (1.2) and `loss_allowance` (0.05). A confirmation is refused with E-QTY when its pieces × 1.2 kg need more than the steel still out on the order × 1.05. Leave the block out to use those two values. A bad value stops the desk with *config.json weight needs kg_per_piece above 0 and loss_allowance from 0 to 1.*

`retry` holds `max_retries` (5, meaning 1 initial attempt plus up to 5 retries, range 1..10), `initial_delay_seconds` (1.0, range >0..300), and `backoff_multiplier` (2.0, >1). Only `E-TIMEOUT` and transient connection drops are retried; `E-AUTH` and business rejections are never retried. Intermediate retry failures log `Technical Failed` and exhausted retries log `Dead` to the main message log.

## 8. Hold and cancel

The Work order screen has **Hold**, **Resume** and **Cancel**. A held or cancelled order refuses issue, return, confirmation, disposition and receipt with E-CLOSED. Cancel is refused while steel is out, and after cancel the release stays refused.

## More

- [docs/message-contract.md](docs/message-contract.md): every field of every message, and the code returned when it is wrong. POC contract, not a signed Jinchen API.
- [docs/run-book.md](docs/run-book.md): restart, switches, replay, and who owns the test passwords.
- [docs/decisions.md](docs/decisions.md): the lead's decisions, with dates and the stories they unblock.
- [docs/deployment.md](docs/deployment.md): where the connector runs, outside ERPNext and the Motaxle app, and how to start, stop and restart it.
- [docs/interface-note.md](docs/interface-note.md): POC draft of the interface note: the ten messages marked, the system of record, and the fifteen workshop answers. Not signed yet.
- [docs/reason-list.md](docs/reason-list.md): the scrap, rejection and rework reasons, one decision each.
- [docs/master-data.md](docs/master-data.md): every master the POC needs, with owner, doctype, key field and load order.
- [docs/poc-acceptance.md](docs/poc-acceptance.md): how the POC is demonstrated, and the acceptance checklist.
- [docs/message-log.md](docs/message-log.md): the twelve fields of every log row, the seven statuses, and how secrets are masked.
- [docs/exceptions.md](docs/exceptions.md): the exception list, replay by Support, and the health view.
- [docs/poc-findings.md](docs/poc-findings.md): what the POC proved, what it taught us, and the scope gap to the full build, story by story.

## How we work

- **One branch per task sheet part**, named `<person>-<topic>`, for example `ananya-rules` or `jashan-ci`. Branch from an up-to-date main. Never commit on main.
- **One pull request per branch.** The title is the task's title from the sheet. The description lists the sheet task ids, the tests added by name, and the before and after figures.
- **Review against the sheet.** The reviewer checks every Pass line of the tasks in the pull request, runs `python3 -m unittest discover tests` and `python3 prove.py`, and puts each rule back once to see its test go red. Juniors review each other; the lead reviews and merges.
- **Green CI before merge.** Main requires the `test` check. A red check is fixed on the same branch with a new commit.
- **Merge order is set by the lead** when two branches touch the same files. A later branch merges main in (no rebase, no force-push) and resolves conflicts there.
- **poc.py changes only in the functions the sheet names.** Every sentence the code already returns stays word for word. raw 380, raw 384, hold 96 and finished 0 do not move. `tests/test_poc.py` and `demo.py` are not edited.
- **Commits** are in the author's own name. The subject line says what, then a blank line, then a body that says why.
- **Never commit** a password, a `.env` file, a live site URL, or files from `inbox/in`, `inbox/done` or `inbox/results`. No Jinchen web address, token or path.
