# Master-data inventory and load template (US-014)

Every master the POC needs, with where it comes from, who owns it, the ERPNext doctype and the key field. The values are the POC values from `connector/poc.py` and the samples. Owners are roles until the client names people.

## Inventory

| Master | POC value | Source | Owner | ERPNext doctype | Key field |
|---|---|---|---|---|---|
| Company | (test-site company, Part B) | ERPNext test site | ERPNext Lead | Company | company_name |
| Unit | Kg | ERPNext standard | ERPNext Lead | UOM | uom_name |
| Unit | Nos | ERPNext standard | ERPNext Lead | UOM | uom_name |
| Unit conversion | g to Kg (1000 g = 1 Kg) | ERPNext standard | ERPNext Lead | UOM Conversion Factor | from_uom + to_uom |
| Warehouse | Raw Material Store | Stores | Stores | Warehouse | warehouse_name |
| Warehouse | Forging WIP | Production | Production | Warehouse | warehouse_name |
| Warehouse | Final QC Hold | Quality | Quality | Warehouse | warehouse_name |
| Warehouse | Finished Goods Store | Stores | Stores | Warehouse | warehouse_name |
| Warehouse | Rejection Store | Quality | Quality | Warehouse | warehouse_name |
| Warehouse | Rework Store | Production | Production | Warehouse | warehouse_name |
| Warehouse | Scrap Yard | Stores | Stores | Warehouse | warehouse_name |
| Item | ST-20 (steel, Kg, batch) | Purchase / Stores | Stores | Item | item_code |
| Item | CR-100 (forged part, Nos) | Engineering | Production | Item | item_code |
| Item | One serial-tracked test item | Engineering | Production | Item | item_code |
| Workstation | Forging line for operations 10 and 20 | Production | Production | Workstation | workstation_name |
| Operation | 10 cutting, 20 forging, 30 vendor work | Production | Production | Operation | name |
| BOM | CR-100 from ST-20, 1.20 kg per piece, operations 10/20/30 | Engineering | Production | BOM | item + is_default |
| Batch | H-441 (heat of ST-20) | Steel receipt | Stores | Batch | batch_id |
| Opening stock | 500 kg ST-20, H-441, in Raw Material Store | Stock count | Stores | Stock Entry (Material Receipt) | batch + warehouse |
| Reason list | forge-crack, rework-bend, scale | [reason-list.md](reason-list.md) | Quality | (custom list) | reason |

## Load order

Each line needs the ones above it.

1. Company
2. UOM and UOM conversion
3. Warehouses
4. Items (ST-20, CR-100, serial test item)
5. Operations and workstation
6. BOM for CR-100
7. Batch H-441
8. Opening stock (500 kg)
9. Reason list

## Data-quality rules

- **Unique item code.** No two items share a code. No near-miss codes (H441 next to H-441, ST20 next to ST-20).
- **Exact-match codes.** The connector matches codes exactly. A near-miss is refused (E-ITEM, E-BATCH), never guessed.
- **Disabled items excluded.** A disabled item is not loaded and not matched.

## Not done yet

- The real masters and opening stock from the client, and named owners instead of roles.
- Loading them on the ERPNext test site (Part B).
- Signed by the ERPNext Lead.
