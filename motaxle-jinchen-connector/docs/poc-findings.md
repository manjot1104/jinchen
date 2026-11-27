# POC findings and scope-gap report (US-065)

What the Jinchen POC proved, what it taught us, and what is still between the POC and the full build. Status as of 27 November 2026, taken from Linear and from `main`. Owner: Manjot (Tech Lead).

> **On hold, 27 November 2026.** The client has not officially given us the project yet; this work is a POC only. Everything that needs the client or Jinchen (the ERPNext test site, the Jinchen API pack, the client demo and the signatures) is on hold until the project is confirmed. Only the connector work that runs on the stand-in goes on.

## In one paragraph

The connector rules work on a laptop. Every message the floor sends (M4 to M8, with M10 riding on them) is checked, posted once, logged, and refused with a readable code when it is wrong, and nothing in the POC talks to Jinchen or to a live ERPNext site. 51 of the 91 stories are done. 30 stories need the ERPNext test site (Part B) or the Jinchen API pack, and the paper drafts need signatures; all of these are on hold until the client confirms the project. 7 stories are still open in the team, all of them connector work on the stand-in.

## What the POC proved

Run on `main` (22b8c50): `python3 -m unittest discover tests` gives 316 tests OK, and `python3 prove.py` prints TC-02 to TC-13 and ALL PASS.

| # | Claim | Evidence |
|---|---|---|
| 1 | A message moves stock once, even when it is sent again or its log row is lost. | TC-03, TC-04, TC-05, TC-08, TC-10; repeat key is id + work order + kind (US-067), and operation for a confirmation (US-090). |
| 2 | The nine sample files give the expected figures: raw 384 kg, WIP 116 kg, QC hold 96, finished 0, 7 stock entries. | TC-12 through `read_all`; reconcile shows no gaps (US-084). |
| 3 | A wrong message is a refusal with a code, never a crash, and stock does not move. | TC-13 (eight bad files); 11 codes in [message-contract.md](message-contract.md); US-054, US-068. |
| 4 | Good pieces wait in Final QC Hold; rejected pieces never become finished goods. | TC-09, TC-11; desk check 7 in [poc-acceptance.md](poc-acceptance.md). |
| 5 | The heat is traced from the issue: batch B-1 reads back to H-441 and WO-2401. | TC-11; lookup on the desk; US-072. |
| 6 | Switch A stops posting and Switch B stops release, without losing what is on the floor. | US-006, US-048; desk tests. |
| 7 | The desk survives a restart: ledger, confirmations, order state and the message log come back. | US-089; [message-log.md](message-log.md). |
| 8 | Every attempt is a log row with 12 fields and one of 7 statuses; secrets are stored as `***`. | US-025, `tests/test_message_log.py`. |
| 9 | Failed messages are listed the same day, Support can replay them, and a replay never posts twice. | US-029, [exceptions.md](exceptions.md). |
| 10 | The connector runs on its own, outside ERPNext, and refuses a live password in test mode. | TC-02; US-024, [deployment.md](deployment.md). |
| 11 | Every push runs the tests, and a test that leaves a file behind fails the check. | US-085, US-091. |

## What we learned

Things the POC found that the plan did not say, and what we did about each.

| # | Finding | What changed |
|---|---|---|
| F-1 | The repeat key needs the message kind. With id + work order only, a different message that reused an id was refused as a repeat. | Key is id + work order + kind (US-067). |
| F-2 | A repeated confirmation moved no stock but was counted twice, so floor progress was wrong. | Confirmations keep their numbers per operation; a repeat is E-DUP (US-090, D-8). |
| F-3 | The plan's 2% loss allowance refuses the sample job's own forging. | 5% allowance, kept in `config.json` (D-6). The real figure must come from Production. |
| F-4 | Hold and cancel needed their own code. | E-CLOSED, an eleventh code (D-7). |
| F-5 | A confirmation on an order with no steel out must be refused. | E-QTY *No metal was issued on this work order.* (D-4). |
| F-6 | A bad value (a string quantity, a list for an id) crashed the connector. | Every bad value is a refusal with a code (US-068). |
| F-7 | File handling differs by machine: Windows locks files, exFAT and network drives cannot hard-link, and a second mount cannot rename. | The inbox reserves one name, writes the result first, moves second, and never stops the batch after posting (PR #13). |
| F-8 | Tests that touch the real inbox folders moved the user's own files. | Tests use a temporary folder, and CI fails if a test leaves a file in the inbox (US-091). |
| F-9 | The same code was built twice in parallel pull requests, and each review found the same bugs again. | One pull request per story; closed duplicates (#18, #20). New work builds on the log, exception list and health view already on `main`. |
| F-10 | Business codes and technical failures need different handling, and the log needs a time on every row. | 7 statuses and a timestamp on every row (US-025, US-029); retry only for technical failures (US-027, in review). |

## Where the stories stand

| State | Stories | Meaning |
|---|---|---|
| Done | 51 | Built, tested and merged, and Done in Linear. |
| Open | 7 | In Linear, not finished. Listed below. |
| On hold | 30 | Needs the ERPNext test site (Part B) or the Jinchen API pack. On hold until the client confirms the project. Not in Linear. |
| Canceled | 3 | Cannot be done by the team in the POC, or not needed while it is a POC. |
| **Total** | **91** | US-001 to US-091. |

### Open in the team

| Story | What is left | Owner |
|---|---|---|
| US-027 Retry and Dead status | PR #25: run the retries through the connector so each attempt is a log row. | Ananya |
| US-031 Notification rules | PR #23: wire the rules to the log and the desk. | Ananya |
| US-028 Error-code catalogue | Add E-BOM, E-SERIAL, E-AMEND, E-AUTH and E-TIMEOUT (16 codes in all). US-027 and US-031 need these codes. | Ananya |
| US-030 Switch audit and notice | Store who moved a switch and when, and notify. | Ananya |
| US-053 Counts and log screens | Screens over the message log. | Ananya |
| US-077, US-083 | Built and merged in PR #13. Only the Linear status is left. | Ananya |

## The scope gap for the full build

### 1. On hold: the ERPNext test site (Part B)

On hold until the client confirms the project. Nothing on a real ERPNext site can start until the client sends four items: the site address, the company, the stock entry type name, and a user that cannot submit a sales invoice (decision D-3, still open). These 12 stories need only the test site:

- Site setup: US-015 to US-018.
- Data: US-019 and US-020 (masters and 500 kg opening stock), then US-021 to US-023 (code maps, unit checks, migration dry-run).
- The repeat rule against real stock entries: US-026.
- The Part B demo: US-062 and US-063.

### 2. On hold: the Jinchen API pack

On hold until the client confirms the project; the request is drafted but not sent. It has a gap register (US-008). No API document, test login, environment list, network path or named contact has arrived. These 18 stories need both the API pack and the test site:

- The outbound messages M1 to M3 cannot be built: US-032 to US-037.
- The inbound messages cannot be posted to a real site: US-038, US-040 to US-045, and US-039.
- Reports that compare the two systems: US-051 and US-052.
- Tests on the real test pair: US-058 and US-059.

Even before the pack arrives:

- Retry (US-027) and the E-AUTH and E-TIMEOUT codes (US-028) can be built and tested only against the stand-in.

### 3. On hold: signatures

The POC drafts are written; each one says who must sign it. No sign-off meeting is planned until the client confirms the project.

| Paper | Who signs | What is open |
|---|---|---|
| [interface-note.md](interface-note.md) | ERPNext Lead, Tech Lead; Production and Stores for posting | The named person for M9, the heat owner, and Jinchen's confirmation of the marks. |
| [reason-list.md](reason-list.md) | Production Head, Quality Head | Confirm or replace the three reasons. |
| [master-data.md](master-data.md) | ERPNext Lead | The real masters, opening stock and named owners. |
| [poc-acceptance.md](poc-acceptance.md) | Project Manager, Tech Lead | The demo with the client, the attendance list and the signatures. |

The floor walk (US-009) was canceled: it needs the factory floor. The interface note was written from the plan instead, and every answer in it is marked as a proposal or an assumption.

### 4. What the stand-in does that the full build must replace

| In the POC | In the full build |
|---|---|
| An in-memory ledger posts stock. | ERPNext stock entries, through the connector user (US-015, US-038). |
| One work order, WO-2401, with operations 10, 20 and 30 in the code. | Work orders and routes from the submitted BOM (US-017, US-033). |
| Two items (ST-20, CR-100) and seven warehouses in the code. | Code maps kept as data, not code (US-021). |
| Messages arrive as JSON files in `inbox/in`. | Jinchen's real transport, with retry and Dead (US-027). |
| The role is picked on the desk; there is no sign-in. | Roles from the ERPNext user (US-015, US-059). |
| The log and ledger are files on one laptop. | A durable store on the connector host (US-024 is ready to run as its own unit). |
| 11 error codes. | 16 codes (US-028). |
| Times are the laptop's local time. | One agreed time zone. |

## Recommended next steps

1. Finish US-028 first, then US-027 and US-031 on top of it, so the three use one code list and the log on `main`. Then US-030 and US-053.
2. Keep everything in sections 1 to 3 on hold until the client confirms the project.
3. Once it is confirmed: send the Jinchen API request, get the four Part B items (D-3), run the client demo from [poc-acceptance.md](poc-acceptance.md) and collect the signatures.
4. When the test site arrives, start with US-015 to US-020 in that order, then US-026, then the inbound messages.

## Every story

State is one of Done, Open, On hold or Canceled. "Linear" is the issue, or `-` when the story is not in Linear.

| Story | Title | Priority | State | Linear | Note |
|---|---|---|---|---|---|
| US-001 | Stand-in connector with in-memory ledger and test-mode guard | Must | Done | PRI-54 | - |
| US-002 | Message validation and duplicate protection | Must | Done | PRI-55 | - |
| US-003 | Material issue and return rules | Must | Done | PRI-56 | - |
| US-004 | Confirmation and disposition rules | Must | Done | PRI-57 | - |
| US-005 | Receipt into QC hold and heat trace | Must | Done | PRI-58 | - |
| US-006 | Local desk with Switch A, Switch B and Reset | Must | Done | PRI-59 | - |
| US-007 | Printed walk (demo.py) and twelve regression tests | Must | Done | PRI-60 | - |
| US-008 | Request Jinchen API pack and test login; record gaps | Must | Done | PRI-176 | - |
| US-009 | Floor walk of one part and screen list | Must | Canceled | PRI-175 | Needs the factory floor; the team cannot do it |
| US-010 | Mark the ten messages: in scope, out of scope, by hand | Must | Done | PRI-174 | - |
| US-011 | System-of-record and floor-flow decisions | Must | Done | PRI-173 | - |
| US-012 | Agree reason list for scrap, rejection and rework | Must | Done | PRI-172 | - |
| US-013 | Document the POC message contract (JSON fields) | Should | Done | PRI-124 | - |
| US-014 | Master-data inventory and load template | Must | Done | PRI-171 | - |
| US-015 | Test company, connector user and restricted permissions | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-016 | Warehouse tree, stock entry type and QC-hold rule | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-017 | Submitted BOM with 1.20 kg per piece, 2% loss and vendor operation 30 | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-018 | Work-order field for Jinchen number, completed-qty update and inspection link | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-019 | Load test masters via ERPNext data import | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-020 | Opening stock 500 kg of H-441 via ERPNext material receipt | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-021 | Maintainable code maps (item, unit, warehouse, operation, reason, machine) | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-022 | Near-miss and unit-conversion validation | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-023 | Data migration plan and second dry-run (test site only) | Should | On hold | - | Part B test site; on hold until the client confirms |
| US-024 | Connector as separate deployable unit with test/live-ready configuration | Must | Done | PRI-170 | - |
| US-025 | Persistent message log | Must | Done | PRI-169 | - |
| US-026 | Repeat rule against ERPNext stock entries with forced log failure | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-027 | Retry for technical failures and Dead status | Must | Open | PRI-168 | Ananya. PR #25, changes requested |
| US-028 | Complete error-code catalogue | Must | Open | PRI-167 | Ananya. Not started |
| US-029 | Exception list, health view and replay | Must | Done | PRI-166 | - |
| US-030 | Switch A and Switch B with audit and notification | Must | Open | PRI-165 | Ananya. Not started |
| US-031 | Notification rules | Could | Open | PRI-164 | Ananya. PR #23, changes requested |
| US-032 | M1 Item identity sent with the release | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-033 | M2 BOM and routing sent with the release | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-034 | M3 Work-order release and Jinchen number | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-035 | Hold and cancel as separate messages | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-036 | Quantity change and BOM change after issue (E-AMEND) | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-037 | Orders that must not be sent | Should | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-038 | M4 Material issue posted once to ERPNext | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-039 | Supervisor approval of an off-BOM item (only if the note allows) | Could | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-040 | M5 Material return with partial returns | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-041 | M6 Production confirmation (four quantities) | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-042 | M7 Receipt of good pieces (partial, QC hold, serial) | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-043 | M8 Scrap, rejection and rework dispositions | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-044 | M9 Check on the machine attached to Quality Inspection | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-045 | M10 Heat, batch and serial chain with lookup screen | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-046 | Nine sample message files | Must | Done | PRI-125 | - |
| US-047 | Inbox reader that calls Connector.process | Must | Done | PRI-126 | - |
| US-048 | Switch A on files and Inbox screen | Must | Done | PRI-127 | - |
| US-049 | config.json guard for mode and live password | Must | Done | PRI-128 | - |
| US-050 | File-driven full walk and dropped-log case | Must | Done | PRI-129 | - |
| US-051 | Daily difference report (Jinchen vs ERPNext) | Should | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-052 | BOM difference screen | Could | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-053 | Counts and message log screens | Could | Open | PRI-163 | Ananya. In progress, no PR yet |
| US-054 | Refusal tests for every issue, return, confirm, disposition and receipt code | Must | Done | PRI-130 | - |
| US-055 | Bad-message example files and disk tests | Must | Done | PRI-131 | - |
| US-056 | prove.py automated proof (TC-02 to TC-11) | Must | Done | PRI-132 | - |
| US-057 | Desk tests: Switch B, B-2 trace and 100-piece split | Should | Done | PRI-133 | - |
| US-058 | ERPNext test-site scenarios S1 to S14 | Must | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-059 | Permission and environment tests on the test pair | Should | On hold | - | Part B test site and Jinchen API pack; on hold until the client confirms |
| US-060 | Demo script and screenshots | Should | Done | PRI-134 | - |
| US-061 | Joint sitting on the merged branches | Must | Canceled | PRI-135 | Not needed while this is a POC |
| US-062 | Part B: connector issue matches figures on the test site | Must | On hold | - | Part B test site; on hold until the client confirms |
| US-063 | Part B: read-only verification of bins and finished goods | Should | On hold | - | Part B test site; on hold until the client confirms |
| US-064 | POC demonstration and acceptance (no production floor) | Must | Done | PRI-136 | - |
| US-065 | POC findings and scope-gap report for the full build | Must | Done | PRI-137 | Manjot. This report |
| US-066 | README and test-environment run book | Should | Done | PRI-138 | - |
| US-067 | Repeat key is id + work order + kind | Must | Done | PRI-139 | - |
| US-068 | A bad value is a refusal, never a crash | Must | Done | PRI-140 | - |
| US-069 | Desk.apply works on a copy; the typed form picks unit and item by kind | Must | Done | PRI-141 | - |
| US-070 | Push, review and merge manjot-rules | Must | Done | PRI-142 | - |
| US-071 | A return is tied to its own issue | Must | Done | PRI-143 | - |
| US-072 | The heat in the trace comes from the issue entry | Must | Done | PRI-144 | - |
| US-073 | Item and unit checked on return, receipt and disposition | Must | Done | PRI-145 | - |
| US-074 | A CAPA check that can fail | Must | Done | PRI-146 | - |
| US-075 | Retake two screenshots and restore the examples object check | Should | Done | PRI-147 | - |
| US-076 | Push ananya-rules and rebase after manjot-rules | Must | Done | PRI-148 | - |
| US-077 | prove.py TC-12 and TC-13 through read_all | Must | Open | PRI-149 | Ananya. Built in PR #13; Linear still In Review |
| US-078 | Inbox message shown as text, evidence files named correctly | Should | Done | PRI-150 | - |
| US-079 | Confirmation piece check on all four quantities | Should | Done | PRI-151 | - |
| US-080 | Refuse a confirmation when nothing was issued | Should | Done | PRI-152 | - |
| US-081 | Weight check with the process-loss allowance | Could | Done | PRI-153 | - |
| US-082 | Hold and cancel in the stand-in (E-CLOSED) | Should | Done | PRI-154 | - |
| US-083 | prove.py reads config.json too | Should | Open | PRI-155 | Ananya. Built in PR #13; Linear still In Review |
| US-084 | Local reconciliation of inbox results against the ledger | Could | Done | PRI-156 | - |
| US-085 | Run the tests on every push | Could | Done | PRI-157 | - |
| US-086 | Register the lead's open decisions | Must | Done | PRI-158 | - |
| US-087 | Send the four Part B handover items | Must | Canceled | PRI-177 | The four values come from the client (D-3) |
| US-088 | Branch and review rules for the POC repository | Should | Done | PRI-159 | - |
| US-089 | Persistent local log for the stand-in | Could | Done | PRI-160 | - |
| US-090 | A repeated confirmation is not stored twice | Must | Done | PRI-161 | - |
| US-091 | CI fails when a test leaves a file in inbox/ | Should | Done | PRI-162 | - |
