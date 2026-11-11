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

Open http://127.0.0.1:8765. The port is required. The desk has Today, Work order, Material, Operations, Quality, Trace, Message log, and Inbox. Reset job returns the ledger to 500 kg and turns both switches off. Stop the server with Ctrl+C.

## 5. Copy samples, then Read inbox

Open Inbox. Press **Copy samples**: the nine files in `inbox/samples/` are copied into `inbox/in/`, only when `inbox/in/` holds no JSON file. Press **Read inbox**: the files are read in filename order, each result is written to `inbox/results/`, and each file moves to `inbox/done/`. After the nine files the tiles read raw 384, WIP 116, hold 96, finished 0, rejection 2, rework 1, scrap 1, seven stock entries, CAPA 0.

`inbox/examples/` holds eight deliberately bad files. Copy samples never reads that folder.

## 6. Switch A

With Switch A on, issue, return, disposition, and receipt files are logged as Queued and stay in `inbox/in/`. Confirmations are still processed. **Turning Switch A off does not flush the queue.** Press Read inbox again to post it. Switch A is a local control and does not call Jinchen.

## 7. Configuration

`config.json` has `mode` and `live_password`. The committed file is test mode with an empty password. `server.py` and the inbox reader read it before they start. A filled password in test mode stops both with *Test mode refuses to start when a live password is set.* A mode other than `test` or `live` stops both with *Mode must be test or live.* Do not commit a password.
