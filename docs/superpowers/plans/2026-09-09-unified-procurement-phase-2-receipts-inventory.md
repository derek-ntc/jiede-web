# Unified Procurement Phase 2: Receipts and Purchase Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add purchase-order receiving, batch posting, three-state invoice tracking, independent purchase inventory, and inbound documents.

**Architecture:** Create `procurement_inventory.py` as the transactional inventory boundary. It consumes phase-one purchase orders and the existing `warehouse_locations` master, but writes only purchase receipt, lot, and transaction tables. Flask pages use preview tokens and idempotency keys so retries and stale submissions cannot duplicate or overwrite stock.

**Tech Stack:** Python 3, Flask, SQLite, Jinja, vanilla JavaScript, openpyxl, ReportLab, unittest, Node test runner

**Spec:** `docs/superpowers/specs/2026-09-09-unified-procurement-vendor-inventory-design.md`

## Global Constraints

- Phase 1 must be complete and its tests passing before this plan starts.
- Purchase inventory reuses `warehouse_locations` but never writes `inventory_balances` or `inventory_transactions`.
- Invoice status values are exactly `not_required`, `pending`, and `invoiced`.
- Only posted qualified quantity enters inventory.
- Posted receipts are corrected by void/reversal, not destructive edits.
- Historical source tables remain unchanged.
- Deployment and formal-data migration require a separate explicit instruction.

---

### Task 1: Receipt, lot, and purchase-inventory transaction schema

**Files:**
- Create: `procurement_inventory.py`
- Modify: `app.py:542-588`
- Test: `tests/test_purchase_inventory_schema.py`

**Interfaces:**
- Produces: `ensure_purchase_inventory_tables(conn) -> None`
- Produces: `parse_invoice_status(value: str) -> str`
- Produces: `next_purchase_receipt_no(conn, received_at: str) -> str`
- Produces: `purchase_lot_available(conn, lot_id: int) -> int`

- [ ] **Step 1: Write failing schema and constraint tests**

```python
def test_schema_is_idempotent_and_reuses_warehouse_location(self):
    procurement_inventory.ensure_purchase_inventory_tables(self.conn)
    procurement_inventory.ensure_purchase_inventory_tables(self.conn)
    columns = self.foreign_keys("purchase_inventory_lots")
    self.assertIn(("location_id", "warehouse_locations", "id"), columns)

def test_invoice_status_is_strict(self):
    for value in ("not_required", "pending", "invoiced"):
        self.assertEqual(procurement_inventory.parse_invoice_status(value), value)
    with self.assertRaises(ValueError):
        procurement_inventory.parse_invoice_status("已开")
```

- [ ] **Step 2: Run tests and verify the module is absent**

Run: `python -m unittest tests.test_purchase_inventory_schema -v`

Expected: FAIL because receipt and lot tables do not exist.

- [ ] **Step 3: Implement exact tables and indexes**

Create `purchase_receipts`, `purchase_receipt_items`, `purchase_inventory_lots`, `purchase_inventory_transactions`, and `purchase_inventory_invoice_events`. Add checks for nonnegative actual/qualified quantities, qualified not exceeding actual, allowed statuses/types, unique receipt/transaction numbers, and unique posted idempotency keys. Reference `purchase_orders`, `purchase_order_items`, and `warehouse_locations` with restrictive foreign keys. Invoice events store old/new states, actor, remark, and timestamp without changing stock.

```python
INVOICE_STATUSES = {"not_required", "pending", "invoiced"}
RECEIPT_STATUSES = {"draft", "posted", "voided"}
PURCHASE_TRANSACTION_TYPES = {"receipt", "outbound", "adjustment", "reversal"}
```

- [ ] **Step 4: Wire schema startup and run integrity tests**

Run: `python -m unittest tests.test_purchase_inventory_schema tests.test_procurement_schema -v`

Expected: PASS after repeated `init_db()` calls and clean foreign-key checks.

- [ ] **Step 5: Commit the inventory schema**

```bash
git add procurement_inventory.py app.py tests/test_purchase_inventory_schema.py
git commit -m "feat: add purchase inventory schema"
```

### Task 2: Receipt preview and transactional batch posting

**Files:**
- Modify: `procurement_inventory.py`
- Test: `tests/test_purchase_receipts.py`

**Interfaces:**
- Produces: `load_receipt_preview(conn, order_id: int) -> dict`
- Produces: `receipt_preview_token(preview: dict) -> str`
- Produces: `post_purchase_receipt(conn, payload: dict, actor: str, now: str) -> dict`
- Produces: `void_purchase_receipt(conn, receipt_id: int, actor: str, now: str) -> dict`

- [ ] **Step 1: Write failing posting, retry, partial, over-receipt, and rollback tests**

```python
def test_posted_qualified_quantity_creates_one_lot_and_one_transaction(self):
    preview = procurement_inventory.load_receipt_preview(self.conn, self.order_id)
    payload = self.payload(preview, actual=10, qualified=8, location_id=self.location_id)
    result = procurement_inventory.post_purchase_receipt(self.conn, payload, "receiver", self.now)
    self.assertEqual(result["status"], "posted")
    self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 8)
    self.assertEqual(self.scalar("SELECT quantity_delta FROM purchase_inventory_transactions"), 8)

def test_invalid_second_line_rolls_back_entire_receipt(self):
    payload = self.two_line_payload(second_location_id=999999)
    with self.assertRaises(procurement_inventory.PurchaseReceiptConflict):
        procurement_inventory.post_purchase_receipt(self.conn, payload, "receiver", self.now)
    self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)
```

- [ ] **Step 2: Run receipt tests and confirm missing services**

Run: `python -m unittest tests.test_purchase_receipts -v`

Expected: FAIL because preview/posting services are missing.

- [ ] **Step 3: Implement server-side preview, token identity, and posting**

```python
class PurchaseReceiptConflict(ValueError):
    def __init__(self, message, *, needs_confirmation=False):
        super().__init__(message)
        self.needs_confirmation = needs_confirmation

def receipt_identity(payload):
    rows = sorted(
        (int(row["purchase_order_item_id"]), int(row["actual_quantity"]),
         int(row["qualified_quantity"]), int(row["location_id"]), row["invoice_status"])
        for row in payload["rows"]
    )
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
```

Reload order/item/location state inside one transaction; reject cancelled orders, disabled locations, negative/non-integer quantities, stale previews, duplicates, and qualified greater than actual. Return a 409-style conflict marker when cumulative actual exceeds ordered quantity unless `confirm_over_receipt` is true. Insert receipt, items, lots, and positive transaction rows atomically. Recalculate order state from cumulative posted receipts.

- [ ] **Step 4: Implement void/reversal rules**

Void only when every affected lot still has at least the receipt quantity available. Create negative `reversal` transactions, set lot availability to zero for the reversed receipt quantity, mark receipt void, and recalculate order state. A second void returns the existing void result without another reversal.

- [ ] **Step 5: Run all domain tests**

Run: `python -m unittest tests.test_purchase_receipts -v`

Expected: PASS for partial receipts, all-complete state, retry identity, changed-payload conflicts, over-receipt confirmation, rollback, and void recovery.

- [ ] **Step 6: Commit receipt posting**

```bash
git add procurement_inventory.py tests/test_purchase_receipts.py
git commit -m "feat: post purchase receipts"
```

### Task 3: Arrival selection and batch-inbound interface

**Files:**
- Modify: `app.py`
- Create: `templates/purchase_receipts.html`
- Create: `templates/purchase_receipt_form.html`
- Create: `templates/purchase_receipt_detail.html`
- Modify: `templates/purchase_tabs.html`
- Create: `static/purchase-receipts.js`
- Create: `static/purchase-inventory.css`
- Test: `tests/test_purchase_receipt_routes.py`
- Test: `tests/js/purchase-receipts.test.js`

**Interfaces:**
- Consumes: `load_receipt_preview`, `post_purchase_receipt`, `void_purchase_receipt`
- Produces routes `/admin/purchases/receipts`, `/receipts/new`, `/receipts/<id>`, `/receipts/<id>/void`
- Produces: `purchase_csrf_token() -> str`
- Produces: `require_purchase_csrf(payload: dict) -> None`

- [ ] **Step 1: Write failing route and front-end state tests**

```python
def test_receipt_order_search_filters_supplier_category_and_open_quantity(self):
    response = self.client.get("/admin/purchases/receipts", query_string={
        "supplier_id": self.supplier_a, "category": "raw_material", "status": "open"
    })
    self.assertEqual(response.status_code, 200)
    self.assertIn("PO-A", response.get_data(as_text=True))
    self.assertNotIn("PO-B", response.get_data(as_text=True))
```

```javascript
test('qualified quantity cannot exceed actual quantity', () => {
  assert.deepEqual(validateReceiptRow({actual_quantity: '3', qualified_quantity: '4'}),
                   {valid: false, message: '合格入库数不能大于实际到货数'});
});
```

- [ ] **Step 2: Run route and JavaScript tests and confirm failure**

Run: `python -m unittest tests.test_purchase_receipt_routes -v && node --test tests/js/purchase-receipts.test.js`

Expected: FAIL with missing routes/assets.

- [ ] **Step 3: Implement searchable order selection and receipt form**

Render ordered quantity, cumulative actual, cumulative qualified, remaining quantity, current row status, enabled location options, and invoice status. Only checked rows are posted. Use JSON POST with CSRF, preview token, idempotency key, and explicit over-receipt confirmation.

```python
@app.post("/admin/purchases/receipts/new")
@permission_required("purchase_receipt")
def create_purchase_receipt():
    payload = request.get_json(silent=True) or {}
    require_purchase_csrf(payload)
    try:
        with get_db() as conn:
            result = post_purchase_receipt(
                conn, payload, current_admin_username(),
                datetime.utcnow().isoformat(timespec="seconds"),
            )
    except PurchaseReceiptConflict as error:
        status = 409 if error.needs_confirmation else 400
        return jsonify(error=str(error), needs_confirmation=error.needs_confirmation), status
    return jsonify(result), 200 if result.get("duplicate") else 201
```

- [ ] **Step 4: Add detail/void interface and run tests**

Run: `python -m unittest tests.test_purchase_receipt_routes -v && node --test tests/js/purchase-receipts.test.js`

Expected: PASS; unauthorized users cannot load or submit, and stale tabs cannot post outdated quantities.

- [ ] **Step 5: Commit arrival and batch inbound UI**

```bash
git add app.py templates/purchase_receipts.html templates/purchase_receipt_form.html templates/purchase_receipt_detail.html templates/purchase_tabs.html static/purchase-receipts.js static/purchase-inventory.css tests/test_purchase_receipt_routes.py tests/js/purchase-receipts.test.js
git commit -m "feat: add purchase receipt workflow"
```

### Task 4: Purchase inventory list, filters, and controlled adjustment

**Files:**
- Modify: `procurement_inventory.py`
- Modify: `app.py`
- Create: `templates/purchase_inventory.html`
- Create: `templates/purchase_inventory_adjust.html`
- Modify: `templates/base.html`
- Test: `tests/test_purchase_inventory.py`

**Interfaces:**
- Produces: `fetch_purchase_inventory(conn, filters: dict, include_prices: bool) -> list[dict]`
- Produces: `adjust_purchase_inventory(conn, lot_id: int, actual_quantity: int, reason: str, actor: str, now: str, expected_version: str) -> dict`
- Produces: `update_purchase_invoice_status(conn, lot_id: int, new_status: str, remark: str, actor: str, now: str) -> dict`

- [ ] **Step 1: Write failing filter, price isolation, and adjustment tests**

```python
def test_inventory_filters_category_supplier_name_location_and_invoice_status(self):
    rows = procurement_inventory.fetch_purchase_inventory(self.conn, {
        "category": "carton", "supplier_id": self.supplier_id,
        "query": "五层", "location_id": self.location_id,
        "invoice_status": "pending",
    }, include_prices=True)
    self.assertEqual([row["id"] for row in rows], [self.carton_lot])

def test_stale_adjustment_cannot_overwrite_new_balance(self):
    version = self.lot_version(self.lot_id)
    self.post_outbound_directly(self.lot_id, 1)
    with self.assertRaises(procurement_inventory.PurchaseInventoryConflict):
        procurement_inventory.adjust_purchase_inventory(
            self.conn, self.lot_id, 7, "盘点差异", "keeper", self.now, version
        )

def test_invoice_status_change_is_audited_without_changing_stock(self):
    before = self.available(self.lot_id)
    procurement_inventory.update_purchase_invoice_status(
        self.conn, self.lot_id, "invoiced", "发票号 FP-100", "receiver", self.now
    )
    self.assertEqual(self.available(self.lot_id), before)
    self.assertEqual(self.latest_invoice_event(self.lot_id), ("pending", "invoiced", "receiver"))
```

- [ ] **Step 2: Run focused tests and confirm missing behavior**

Run: `python -m unittest tests.test_purchase_inventory -v`

Expected: FAIL because inventory query and adjustment functions are missing.

- [ ] **Step 3: Implement filters, lot summaries, and optimistic adjustment**

Return current and received quantities, supplier, order/receipt number, category label, item fields, location, invoice status, and price only when authorized. Adjustment requires a nonblank reason, nonnegative actual count, current version match, and an `adjustment` delta transaction.

- [ ] **Step 4: Build inventory subpage and adjustment route**

Add “产品库存 / 采购库存” tabs. Search with category, supplier, text, location, received date, invoice status, and “include zero stock”. Hide all price columns and totals when `purchase_price_view` is false. Add a CSRF-protected invoice-status update action guarded by `purchase_receipt`; it writes `purchase_inventory_invoice_events` and never changes quantity.

- [ ] **Step 5: Run purchase and existing product inventory tests**

Run: `python -m unittest tests.test_purchase_inventory tests.test_inventory tests.test_inventory_filters tests.test_inventory_batch -v`

Expected: PASS; product inventory counts and transactions remain unchanged during every purchase inventory test.

- [ ] **Step 6: Commit purchase inventory UI**

```bash
git add procurement_inventory.py app.py templates/purchase_inventory.html templates/purchase_inventory_adjust.html templates/base.html tests/test_purchase_inventory.py
git commit -m "feat: add independent purchase inventory"
```

### Task 5: Inbound list Excel/PDF exports

**Files:**
- Modify: `procurement_documents.py`
- Modify: `app.py`
- Modify: `templates/purchase_receipt_detail.html`
- Test: `tests/test_purchase_receipt_exports.py`

**Interfaces:**
- Produces: `build_purchase_receipt_workbook(receipt, items, *, include_prices: bool) -> BytesIO`
- Produces: `build_purchase_receipt_pdf(receipt, items, *, include_prices: bool) -> BytesIO`

- [ ] **Step 1: Write failing export tests**

```python
def test_receipt_export_lists_actual_qualified_location_and_invoice_state(self):
    sheet = load_workbook(build_purchase_receipt_workbook(self.receipt, self.items, include_prices=True)).active
    values = [cell.value for row in sheet for cell in row]
    for expected in ("实际到货", "合格入库", "库位", "需要开票/未开票"):
        self.assertIn(expected, values)
```

- [ ] **Step 2: Run export tests and confirm missing builders**

Run: `python -m unittest tests.test_purchase_receipt_exports -v`

Expected: FAIL because receipt builders do not exist.

- [ ] **Step 3: Implement landscape-A4 receipt documents and guarded routes**

Include receipt number, purchase order, supplier snapshot, receipt date, category, actual and qualified quantity, location, invoice status, remarks, operator, and totals. Omit price columns and values when unauthorized. Use receipt snapshots rather than current supplier data.

- [ ] **Step 4: Run export and permission tests**

Run: `python -m unittest tests.test_purchase_receipt_exports tests.test_procurement_permissions -v`

Expected: PASS; PDF text extraction and workbook assertions contain no hidden prices for unauthorized users.

- [ ] **Step 5: Commit inbound documents**

```bash
git add procurement_documents.py app.py templates/purchase_receipt_detail.html tests/test_purchase_receipt_exports.py
git commit -m "feat: export purchase inbound lists"
```

### Task 6: Migrate legacy arrival records and received cartons

**Files:**
- Modify: `procurement.py`
- Modify: `procurement_inventory.py`
- Modify: `scripts/report_procurement_migration.py`
- Test: `tests/test_purchase_receipt_migration.py`

**Interfaces:**
- Produces: `migrate_legacy_purchase_receipts(conn, now: str) -> dict[str, int]`
- Extends: `procurement_migration_report(conn) -> dict[str, object]`

- [ ] **Step 1: Write failing migration tests for explicit and ambiguous arrivals**

```python
def test_received_carton_creates_pending_location_lot_once(self):
    self.seed_carton(id=4, received_at="2026-09-01", quantity=10)
    procurement_inventory.migrate_legacy_purchase_receipts(self.conn, self.now)
    procurement_inventory.migrate_legacy_purchase_receipts(self.conn, self.now)
    self.assertEqual(self.count_receipts("carton_purchases", 4), 1)
    self.assertEqual(self.pending_location_quantity("carton_purchases", 4), 10)

def test_completed_carton_without_received_date_does_not_create_inventory(self):
    self.seed_carton(id=5, received_at="", completed=1, quantity=10)
    procurement_inventory.migrate_legacy_purchase_receipts(self.conn, self.now)
    self.assertEqual(self.count_receipts("carton_purchases", 5), 0)
```

- [ ] **Step 2: Run migration tests and confirm failure**

Run: `python -m unittest tests.test_purchase_receipt_migration -v`

Expected: FAIL because receipt migration is absent.

- [ ] **Step 3: Implement explicit historical receipt migration**

Create one disabled `warehouse_locations` row with code `PURCHASE-PENDING` and name `采购待分配库位` if needed. Migrate `arrival_records` and cartons with nonblank `received_at` into posted historical receipts/lots at that location, with invoice status `pending`. Preserve `(legacy_source, legacy_id)` uniqueness. Do not create stock from a completed flag alone.

- [ ] **Step 4: Extend the read-only reconciliation report and run twice**

Report source count, migrated receipt count, actual/qualified/lot/transaction quantity equality, pending-location quantity, duplicates, and foreign-key errors.

Run: `python -m unittest tests.test_purchase_receipt_migration -v`

Expected: PASS; second migration creates zero rows and all quantity equations balance.

- [ ] **Step 5: Commit historical receipt migration**

```bash
git add procurement.py procurement_inventory.py scripts/report_procurement_migration.py tests/test_purchase_receipt_migration.py
git commit -m "feat: migrate legacy purchase receipts"
```

### Task 7: Phase-two full verification

**Files:**
- Modify: `docs/superpowers/plans/2026-09-09-unified-procurement-phase-2-receipts-inventory.md`

**Interfaces:**
- Consumes: phase-one orders plus all phase-two receipt/inventory behavior
- Produces: reviewed purchase inventory baseline for outbound work

- [ ] **Step 1: Run complete Python and JavaScript suites**

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.js`

Expected: all tests PASS.

- [ ] **Step 2: Rehearse migration on a database copy and compare totals**

Run: `python scripts/report_procurement_migration.py --database /private/tmp/jiede-procurement-receipt-rehearsal.db`

Expected: all explicit arrival quantities reconcile, ambiguous cartons remain non-stock records, and no product inventory rows change.

- [ ] **Step 3: Perform local browser acceptance**

Verify order search, split deliveries, multi-row receipt, quick warehouse-location creation, over-receipt confirmation, invoice status, inventory filters, adjustment conflict, receipt exports, void behavior, and permission isolation.

- [ ] **Step 4: Record evidence and commit only if the plan checkbox log changed**

```bash
git add docs/superpowers/plans/2026-09-09-unified-procurement-phase-2-receipts-inventory.md
git commit -m "docs: record procurement phase two verification"
```
