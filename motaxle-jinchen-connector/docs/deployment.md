# Where the connector runs (US-024)

The connector is its own program. It is not part of ERPNext and not part of the Motaxle app, so either side can be released without the other.

## Where it lives

- In its own repository, `github.com/manjot1104/jinchen`, folder `motaxle-jinchen-connector/`.
- Not under `motaxle-bench/apps`, not a bench app, and not an edit to ERPNext core or the Motaxle app. The repository has no `hooks.py`, no bench app files and no `frappe` or `erpnext` import. `tests/test_deployment.py` checks this.
- It runs with Python 3.9 or newer and the standard library only. Nothing is installed with pip.
- In the POC it runs on one machine, at http://127.0.0.1:8765, with the local stand-in in place of Jinchen and of ERPNext.

## Test and live settings

Test and live settings are configuration, not code. `config.json` holds `mode` and `live_password`:

- The committed file is `mode: test` with an empty `live_password`.
- A test run refuses to start when a live password is set: *Test mode refuses to start when a live password is set.* The password is never printed.
- The POC only uses the test pair. No live password is kept in the repository or in a log. `tests/test_deployment.py` fails if a tracked file holds a filled `live_password`.

Who owns the passwords is in [run-book.md](run-book.md).

## Start

    cd motaxle-jinchen-connector
    python3 server.py

It prints `Open http://127.0.0.1:8765` and `Ctrl+C stops it.` When `config.json` is refused, it prints the reason and `The desk did not start.`, and exits with code 1.

## Stop

Press Ctrl+C in the same terminal. It prints `The desk stopped.` and frees port 8765.

## Restart

Stop it, then start it again. Nothing else is restarted: no ERPNext site, no bench and no Motaxle app. What a restart keeps and what it clears is in [run-book.md](run-book.md), under "Restart the desk".

`evidence/US-024-restart.txt` is one start, stop and restart, run on 20 November 2026.

## Not shown yet

- **Restarting ERPNext without the connector, and the other way round.** This needs the ERPNext test site, which opens with Part B (US-087). Until then the stand-in plays ERPNext inside the connector's own process.
- **`git diff` on ERPNext core and the Motaxle app.** The POC does not touch either repository. The check is run once on the test site when Part B opens.
