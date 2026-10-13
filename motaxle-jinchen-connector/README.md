# Motaxle Jinchen practical POC stand-in

This runs the story in `Motaxle-Jinchen-Practical-POC.pdf` on this machine.

ERPNext stock is an in-memory ledger. Jinchen is the message in `demo.py`. Nothing is sent to a network. The JSON fields are the POC contract. They are not a signed Jinchen API.

## Show it

```bash
cd motaxle-jinchen-connector
python3 server.py
```

Then open http://127.0.0.1:8765. The desk has Today, Work order, Material, Operations, Quality, Trace, and Message log. Material, Operations, and Quality also take a message you type. Switch A logs a stock message without posting it. Reset job returns the ledger to 500 kg. Stop the server with Ctrl+C.

```bash
python3 demo.py
python3 -m unittest tests/test_poc.py
```

A pass prints raw 380 after a repeated J-100, finished goods 0 after cutting, hold 96, and a lookup from batch B-1 back to heat H-441.
