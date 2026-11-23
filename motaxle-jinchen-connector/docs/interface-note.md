# Interface note: POC draft (US-010, US-011)

This is the POC draft of the interface note. It is written from the POC code and the plan, not from a workshop with Jinchen or the factory. Every mark and answer below is a **proposal** until the people named in "Sign-off" sign it. The Jinchen API pack has not been received, so nothing here is a signed Jinchen API.

## The ten messages (US-010)

One mark per message. "Built in the POC" says what the stand-in already does.

| ID | What moves | Direction | Mark (proposal) | Built in the POC |
|---|---|---|---|---|
| M1 | Identity of the item | ERPNext to Jinchen | In scope | No. The POC knows ST-20 and CR-100 only. |
| M2 | BOM and routing | ERPNext to Jinchen | In scope | No. The POC route is operations 10, 20 and 30 in the code. |
| M3 | Release, hold, cancel, quantity change | ERPNext to Jinchen | In scope | Release (Switch B), hold, resume and cancel. Quantity change is not built. |
| M4 | Material to the machine | Jinchen to ERPNext | In scope | Yes: `issue`, raw to Forging WIP. |
| M5 | Unused material back | Jinchen to ERPNext | In scope | Yes: `return`, tied to its issue. |
| M6 | Progress of the operation | Jinchen to ERPNext | In scope | Yes: `confirm`, per operation (10 and 20; 30 is vendor work and ignored). |
| M7 | Good pieces received | Jinchen to ERPNext | In scope | Yes: `receipt`, into Final QC Hold. |
| M8 | Scrap, rejection, rework | Jinchen to ERPNext | In scope | Yes: `disposition`, with the reason list in [reason-list.md](reason-list.md). |
| M9 | Check on the machine | Jinchen to ERPNext | By hand | No. Quality enters the inspection in ERPNext. **Person: open** (to be named by the Quality Head). |
| M10 | Heat, batch, serial | On M4 and M7 | Link only | Yes: the heat rides on the issue, the batch on the receipt, and the lookup reads B-1 back to H-441. |

0 messages are unmarked. M10 is not an end-of-shift file.

Sample orders, tooling orders and machine minutes:

| Item | Decision (proposal) |
|---|---|
| Sample orders | Out of scope. Not sent unless the note adds them. |
| Tooling orders | Out of scope. Not sent unless the note adds them. |
| Machine minutes | Stored when present, not required. |

## System of record (US-011)

- **Stock = ERPNext.** Only ERPNext posts the stock ledger. The connector posts in ERPNext on Jinchen's behalf; Jinchen never keeps its own stock of the same 120 kg.
- **Heat = Stores (ERPNext batch).** The heat (H-441) is created as an ERPNext batch by Stores when the steel is received. The connector never creates a heat. **Named owner: open** (to be named by Stores).
- **Confirmation style = per operation.** The POC confirms operations 10 and 20 one by one. Per-order confirmation is recorded here as **not built**.
- **Failure notice = 4 working hours**, as a configuration value (US-031 builds it).

## The fifteen workshop questions (US-011)

"POC assumption" means the client has not answered yet, so the POC works on this answer and it is flagged.

| # | Question | Answer | Kind |
|---|---|---|---|
| 1 | Jinchen version, document, and who answers mapping questions | Unknown. Not invented. Requested in the API request (US-008). | Open |
| 2 | Test environment, or live only | Both pairs are needed before go-live. The POC uses only the local stand-in. | POC assumption |
| 3 | Which lines are on day one | One forging line for WO-2401 in the POC. The real list goes on the signed note. | POC assumption |
| 4 | Which system posts the stock ledger | ERPNext. | Decided (above) |
| 5 | Operator issues, or Stores types | The message comes from Jinchen (operator side); Stores does not type the same issue. Never both for the same 120 kg. | POC assumption |
| 6 | Each operation, or the whole order | Each operation. | Decided (above) |
| 7 | Who creates the heat | Stores, as an ERPNext batch. Not the connector. | Decided (above), owner open |
| 8 | Which items are batch, which are serial | ST-20 is batch (heat). CR-100 output is batch (B-1) in the POC; serial only if the item says so. | POC assumption |
| 9 | Inspection before the saleable warehouse | Yes. Receipts go to Final QC Hold; only an ERPNext move takes them to Finished Goods. | Decided in the POC rules |
| 10 | Words for scrap, rejection and rework | One reason, one decision, one warehouse: [reason-list.md](reason-list.md). | POC assumption |
| 11 | Sample and tooling orders sent or not | Not sent. | POC assumption |
| 12 | Minutes required or not | Stored when present, not required. | POC assumption |
| 13 | Supervisor may approve metal off the BOM | No. Only ST-20 heat H-441 is accepted (E-ITEM, E-BATCH). | POC assumption |
| 14 | Network path and password owner | Password owner: Manjot (run book). Network path: open, asked in the API request. | Partly open |
| 15 | Hours before Stores must hear about a failure | 4 working hours. | POC assumption |

15 of 15 questions have an answer or a flagged assumption.

## Sign-off (not done)

The note blocks a build only once it is signed. Still to do:

- ERPNext Lead and Tech Lead sign the note.
- Production and Stores sign the posting decision.
- Named people for M9 (by hand) and for the heat owner.
- Jinchen confirms the marks once the API pack arrives.
