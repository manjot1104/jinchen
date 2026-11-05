# Motaxle–Jinchen Practical POC

This project demonstrates the Motaxle–Jinchen integration workflow using a local POC stand-in.

ERPNext stock is maintained in an in-memory ledger. Messages are represented by JSON files. No live Jinchen or ERPNext API calls are made. The JSON fields represent the POC contract, not a signed Jinchen API specification.

## 1. Clone


    git clone https://github.com/manjot1104/jinchen.git
    cd jinchen/motaxle-jinchen-connector

## 2. Discover

Start the application:

    python3 server.py

Open http://127.0.0.1:8765. Use the Trace screen to look up batch B-1 and trace it back to heat H-441.

## 3. Demo

Run:

    python3 demo.py

The demo covers material issues, duplicate replay, returns, operation confirmations, quality dispositions, receipts, and batch traceability.

Expected results include raw stock of 380 kg after the J-100 issue, no duplicate stock posting on replay, finished goods of 0, hold quantity of 96 kg, and batch B-1 linked to heat H-441.

## 4. Prove

Reserved for Ananya.

## 5. Server

Start the application:

    python3 server.py

Open http://127.0.0.1:8765.

Available screens: Today, Work order, Material, Operations, Quality, Trace, Message log, and Inbox.

Stop the server with Ctrl+C.

## 6. Copy Samples and Read Inbox

Open Inbox and select Copy samples. This copies sample JSON messages from inbox/samples/ into inbox/in/. The inbox must be empty before copying.

Select Read inbox to process available messages. Processed messages move to inbox/done/, and results are written to inbox/results/.

## 7. Switch A Behaviour

When Switch A is ON, stock messages are queued and not posted. Confirmation messages can still be processed.

Turning Switch A OFF does not automatically process queued messages. Select Read inbox again to process them.

Switch A is a local POC control and does not call Jinchen.

## 8. Configuration

config.json uses test mode with an empty live_password. No production credentials or live integration are configured.

## 9. Tests

Run the complete test suite:

    python3 -m unittest discover -s tests -v

Tests cover connector workflows, duplicate replay after a simulated log failure, and Switch A inbox behaviour.
