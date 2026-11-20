# Lead's decisions (US-086)

The POC decisions that stories were waiting on. Each has a date, an owner, and the stories it unblocks. Owner: Manjot (Tech Lead). Add a decision as a new row. Do not rewrite an old one; mark it replaced instead.

| # | Decision | Date | Owner | Unblocks | State |
|---|---|---|---|---|---|
| D-1 | Merge manjot-rules (tech lead points 1, 3, 7) into main before ananya-rules. | 17 Nov 2026 | Manjot | US-067, US-068, US-069, US-070; then US-076 | Done: PR #5 |
| D-2 | Give Ananya's round 2 the go. Her branch merges after manjot-rules, and the lead closes any review points left. | 17 Nov 2026 | Manjot | US-071 to US-076 | Done: PR #4 |
| D-3 | Send the four Part B items in one note: site URL, company, stock entry type name, and a user that cannot submit a sales invoice. | open | Manjot | US-087, then US-015 and the Blocked test-site stories | **Open**. Nothing on Part B starts until this is sent. |
| D-4 | A confirmation on a work order with no steel out is refused: E-QTY *No metal was issued on this work order.* This is the plan default (BR-36, US-041 acceptance 6). It closes Observation 16. | 18 Nov 2026 | Manjot | US-080 | Built on manjot-stand-in-rules |
| D-5 | A confirmation carries four quantities (completed, rejected, rework, scrap). Their sum is checked against the order of 100 with E-QTY, and each is stored on the log row. Check this again when the Jinchen API pack arrives (Observation 2). | 18 Nov 2026 | Manjot | US-079 | Built on manjot-stand-in-rules |
| D-6 | Weight check: pieces × 1.20 kg must not exceed the steel still out on the order × 1.05 (a 5% loss allowance). Both numbers are in `config.json` under `weight`, not in the rule. With the samples, 100 pieces need 120 kg and 116 kg allows 121.8 kg. Observation 4's 2% (118.32 kg) would refuse sample 03, so it was not chosen. The BA may change the numbers in config. | 18 Nov 2026 | Manjot | US-081 | Built on manjot-stand-in-rules |
| D-7 | Hold and cancel use a new eleventh code, E-CLOSED, as US-082 names it. It is also in the full catalogue (US-028). The task sheets' "ten codes" rule now reads eleven. | 18 Nov 2026 | Manjot | US-082 | Built on manjot-stand-in-rules |
| D-8 | Confirmation repeats now come before E-CLOSED. A replayed confirmation on a held or cancelled order returns Posted E-DUP (if quantities match) rather than E-CLOSED. | 20 Nov 2026 | Manjot | US-090 | Built on jashan-confirm-repeat |

The workbook's Observations tab should link to this file (US-086 acceptance 3). That is a change in the Google Sheet, made by hand.
