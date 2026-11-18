# Test-environment run book (US-066)

For the local stand-in only. There is no production and no go-live handover in this POC. Start with the [README](../README.md) for clone, tests, demo, prove and server.

## Who owns what

| Thing | Owner | Where it lives |
|---|---|---|
| Test passwords: `live_password` in `config.json`, and the ERPNext test-site user when Part B opens | Manjot (Tech Lead) | Never in the repository. Sent to a person directly, never in a chat thread, a commit or a pull request. The committed `config.json` keeps `live_password` empty. |
| Weight rule (`kg_per_piece`, `loss_allowance`) | Manjot; the BA may change it | `config.json` under `weight`. See D-6 in [decisions.md](decisions.md). |
| Merging into main | Manjot | GitHub. Main needs the `test` check to be green. |

If you find a filled password anywhere in the repository, stop and tell the lead. Do not delete it in a new commit: it is already in the history.

## Restart the desk

1. Press Ctrl+C in the terminal where `python3 server.py` runs.
2. Run `python3 server.py` again and open http://127.0.0.1:8765.
3. The ledger is in memory. A restart starts at raw 500 kg, both switches off, and WO-2401 open. The Message log shows the rows from the last session. A Reset job clears it. Files already in `inbox/done/` stay there and are not read again.

If the desk does not start:

| What it prints | What to do |
|---|---|
| *Test mode refuses to start when a live password is set.* | Empty `live_password` in `config.json`. |
| *Mode must be test or live.* | Set `mode` to `test`. |
| *config.json weight needs kg_per_piece above 0 and loss_allowance from 0 to 1.* | Fix the `weight` block, or remove it to use 1.20 and 0.05. |
| `Address already in use` | Another desk holds 8765. `lsof -i :8765` names it. Stop that one, not a stranger's process. |

**Reset job** on the Today screen does the same as a restart without stopping the server.

## The two switches

- **Switch A** stops stock posts. Issue, return, disposition and receipt files are logged as Queued and stay in `inbox/in/`. Confirmations, hold, resume and cancel are still processed. Turning Switch A off does not post the queue. Press **Read inbox** again to post it. A message sent from a desk button while Switch A is on is only logged, and nothing posts it later, so send it again.
- **Switch B** stops the WO-2401 release. Release is refused while it is on.
- Neither switch calls Jinchen.

## Replay a message

A replay must never move stock twice. This is how to show it:

1. Read the nine samples once (Inbox: **Copy samples**, then **Read inbox**). The tiles read raw 384, hold 96, entries 7.
2. Copy one file back from `inbox/done/` to `inbox/in/`, for example `08-receipt-C-1.json`.
3. Press **Read inbox**. The result is Posted with E-DUP and document STE-0006. Hold stays 96 and there are still 7 entries.
4. `inbox/results/08-receipt-C-1.json` is overwritten with the E-DUP result. That is expected.

Replay on the desk: **Send J-100 again**, **Send R-1 again** and **Send C-1 again** do the same from buttons.

A confirmation is not checked for repeats in the stand-in. Replaying `03-confirm-op10.json` stores it a second time. It moves no stock.

## Hold and cancel

On the Work order screen:

- **Hold WO-2401** (H-1): every stock message and confirmation returns E-CLOSED until **Resume WO-2401** (RS-1).
- **Cancel WO-2401** (X-1) is refused with E-QTY while steel is out. Return the 120 kg (or what is left) first. After cancel, nothing reopens the order, and Release is refused with E-CLOSED. **Reset job** is the only way back on the stand-in.

## Clean up after a run

`inbox/in/`, `inbox/done/` and `inbox/results/` are local. Do not commit them. To start the inbox fresh, empty those three folders by hand; Copy samples only copies when `inbox/in/` holds no JSON file.
