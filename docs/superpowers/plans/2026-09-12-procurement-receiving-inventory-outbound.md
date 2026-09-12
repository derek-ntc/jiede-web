# Procurement Receiving, Inventory, and Outbound Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add category-aware purchase-order receiving, independent purchase inventory with audited location/quantity changes, purchase-goods outbound, and the approved raw-material purchase-order export corrections.

**Architecture:** Add `procurement_inventory.py` as the single transactional boundary for receipt, purchase-stock, transfer, adjustment, and outbound operations. It consumes existing unified purchase orders and shared `warehouse_locations`, but never writes product inventory, customer shipment, or sales-finance tables. Flask routes render category-specific pages and submit CSRF-protected, idempotent commands; documents render saved snapshots.

**Tech Stack:** Python 3, Flask, SQLite, Jinja, vanilla JavaScript, openpyxl, ReportLab, unittest, Node test runner

**Spec:** `docs/superpowers/specs/2026-09-12-procurement-receiving-inventory-outbound-design.md`

## Global Constraints

- Categories are exactly `raw_material`, `carton`, `outsourcing`, and `other`.
- Purchase inventory reuses `warehouse_locations` but never writes `inventory_balances` or `inventory_transactions`.
- Actual receipt snapshots never update `purchase_orders` or `purchase_order_items`.
- A receipt, transfer, adjustment, outbound, void, or reversal is atomic and auditable.
- Available purchase inventory must never become negative.
- Posted records are corrected by void/reversal, never destructive edits.
- Repeated matching submissions are idempotent; a reused key with changed content conflicts.
- Price fields are absent from HTML, JSON, Excel, and PDF when the user lacks `purchase_price_view`.
- Raw-material purchase-order standard terms appear only on raw-material Excel/PDF exports.
- Deployment and production-data changes require a separate explicit user instruction.

## File Structure

- Create `procurement_inventory.py`: schema, validation, querying, receipt posting, purchase-stock transfer/adjustment, outbound posting, void/reversal, and invariants.
- Modify `procurement.py`: receipt-aware purchase-order status/history queries only.
- Modify `app.py`: schema startup and permission-guarded receipt, inventory, outbound, and export routes.
- Modify `procurement_documents.py`: raw-material order fix plus receipt, inventory, and outbound Excel/PDF builders.
- Create `templates/purchase_receipts.html`, `purchase_receipt_form.html`, and `purchase_receipt_detail.html`: order selection, batch receipt, and posted receipt history.
- Create `templates/purchase_inventory.html` and `purchase_inventory_change.html`: filters, stock list, adjustment, and transfer.
- Create `templates/purchase_inventory_outbounds.html`, `purchase_inventory_outbound_form.html`, and `purchase_inventory_outbound_detail.html`: outbound history, selection, and detail.
- Modify `templates/purchase_tabs.html`, `templates/inventory_overview.html`, and `templates/shipment_tabs.html`: expose the new flows without changing existing product flows.
- Create `static/purchase-inventory.css`, `static/purchase-receipts.js`, and `static/purchase-inventory-outbound.js`: category tabs, editable receipt rows, selection, validation, and conflict display.
- Add focused Python and Node tests under `tests/` and `tests/js/`.

---

### Task 1: Purchase receipt and independent inventory schema

**Files:**
- Create: `procurement_inventory.py`
- Modify: `app.py` in `init_db()` procurement initialization
- Test: `tests/test_purchase_inventory_schema.py`

**Interfaces:**
- Consumes: `procurement.PURCHASE_CATEGORIES`
- Produces: `ensure_purchase_inventory_tables(conn) -> None`
- Produces: `parse_purchase_category_slug(value: str) -> str`
- Produces: `parse_purchase_inventory_quantity(value: object, *, allow_zero: bool = False) -> int`
- Produces: `purchase_inventory_invariant_errors(conn) -> list[str]`

- [ ] **Step 1: Write failing schema and constraint tests**

```python
def test_schema_is_idempotent_and_uses_shared_locations(self):
    ensure_purchase_inventory_tables(self.conn)
    ensure_purchase_inventory_tables(self.conn)
    names = {row[0] for row in self.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    self.assertTrue({"purchase_receipts", "purchase_receipt_items",
                     "purchase_inventory_lots", "purchase_inventory_operations",
                     "purchase_inventory_transactions",
                     "purchase_inventory_invoice_events",
                     "purchase_inventory_outbounds",
                     "purchase_inventory_outbound_items"} <= names)
    foreign = self.conn.execute("PRAGMA foreign_key_list(purchase_inventory_lots)").fetchall()
    self.assertTrue(any(row[2] == "warehouse_locations" and row[3] == "location_id" for row in foreign))

def test_invalid_category_quantity_and_status_are_rejected(self):
    self.assertRaises(ValueError, parse_purchase_category_slug, "raw")
    self.assertRaises(ValueError, parse_purchase_inventory_quantity, "0")
    schema = self.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='purchase_receipts'"
    ).fetchone()[0]
    self.assertIn("CHECK status IN ('posted','voided')", " ".join(schema.split()))
```

- [ ] **Step 2: Run the focused test and verify the missing module failure**

Run: `python -m unittest tests.test_purchase_inventory_schema -v`

Expected: FAIL because `procurement_inventory.py` and its tables do not exist.

- [ ] **Step 3: Implement exact schema and indexes**

Create:

```sql
purchase_receipts(
  id, receipt_no UNIQUE, purchase_order_id, received_at,
  status CHECK status IN ('posted','voided'), remark,
  idempotency_key UNIQUE, payload_hash,
  created_by, posted_by, voided_by, created_at, posted_at, voided_at
)

purchase_receipt_items(
  id, receipt_id, purchase_order_item_id,
  item_name, drawing_no, material, dimension_text, surface, spec, unit,
  length, width, height, thickness,
  actual_quantity, qualified_quantity, location_id,
  invoice_status CHECK invoice_status IN ('not_required','pending','invoiced'), remark
)

purchase_inventory_lots(
  id, lot_no UNIQUE, category,
  origin_receipt_item_id, source_lot_id,
  source_kind CHECK source_kind IN ('receipt','transfer'),
  item_name, drawing_no, material, dimension_text, surface, spec, unit,
  length, width, height, thickness,
  supplier_id, supplier_name, purchase_order_id, purchase_order_item_id,
  receipt_id, location_id, opening_quantity, available_quantity,
  unit_price_minor, currency, invoice_status,
  version, created_at, updated_at
)

purchase_inventory_operations(
  id, operation_no UNIQUE,
  operation_type CHECK operation_type IN ('transfer','adjustment','invoice_status'),
  idempotency_key UNIQUE, payload_hash, operator, reason, created_at
)

purchase_inventory_transactions(
  id, transaction_no UNIQUE, lot_id,
  transaction_type CHECK transaction_type IN
    ('receipt','transfer_out','transfer_in','adjustment','outbound','reversal'),
  quantity_delta, related_type, related_id,
  from_location_id, to_location_id, paired_transaction_id,
  operator, remark, created_at
)

purchase_inventory_invoice_events(
  id, lot_id, old_status, new_status, operator, remark, created_at
)

purchase_inventory_outbounds(
  id, outbound_no UNIQUE, outbound_at, used_by, operator, remark,
  status CHECK status IN ('posted','voided'),
  idempotency_key UNIQUE, payload_hash, created_at, voided_at, voided_by
)

purchase_inventory_outbound_items(
  id, outbound_id, lot_id,
  category, item_name, drawing_no, material, dimension_text, surface, spec, unit,
  supplier_name, location_id, location_code, location_name,
  quantity, remark
)
```

Use integer checks for all quantities, nonnegative `available_quantity`, restrictive parent references, and indexes for category, supplier, order, receipt, location, item name, drawing number, status, and dates. Add triggers for production connections where SQLite foreign keys may be disabled, following `ensure_procurement_tables()` patterns.

- [ ] **Step 4: Wire startup and implement the invariant checker**

`purchase_inventory_invariant_errors()` must report negative lots, receipt lots whose opening quantity differs from qualified receipt quantity, transfer pairs with unequal absolute deltas, outbound items without matching negative transactions, and orphaned source records.

- [ ] **Step 5: Run schema and existing procurement tests**

Run: `python -m unittest tests.test_purchase_inventory_schema tests.test_procurement_schema tests.test_procurement_permissions -v`

Expected: PASS with repeated `init_db()` calls and no new permissions granted to existing users.

- [ ] **Step 6: Commit the schema boundary**

```bash
git add procurement_inventory.py app.py tests/test_purchase_inventory_schema.py
git commit -m "feat: add purchase inventory schema"
```

### Task 2: Receipt preview, actual snapshots, and atomic posting

**Files:**
- Modify: `procurement_inventory.py`
- Modify: `procurement.py`
- Test: `tests/test_purchase_receipts.py`

**Interfaces:**
- Produces: `load_receivable_orders(conn, category: str, filters: dict) -> list[dict]`
- Produces: `load_receipt_preview(conn, order_id: int) -> dict`
- Produces: `receipt_preview_token(preview: dict) -> str`
- Produces: `post_purchase_receipt(conn, payload: dict, actor: str, now: str) -> dict`
- Produces: `void_purchase_receipt(conn, receipt_id: int, actor: str, now: str) -> dict`
- Produces: `PurchaseInventoryConflict(ValueError)` with `needs_confirmation: bool` and `current: dict | None`

- [ ] **Step 1: Write failing receipt-domain tests**

```python
def test_actual_snapshot_creates_stock_without_changing_order_item(self):
    preview = load_receipt_preview(self.conn, self.order_id)
    result = post_purchase_receipt(self.conn, {
        "preview_token": receipt_preview_token(preview),
        "idempotency_key": "receipt-1", "received_at": "2026-09-12", "remark": "首批",
        "rows": [{"purchase_order_item_id": self.item_id,
                  "item_name": "实际镀锌板", "material": "DC51D+Z",
                  "length": "1290", "width": "1250", "thickness": "1.4",
                  "actual_quantity": "100", "qualified_quantity": "98",
                  "location_id": str(self.location_id), "invoice_status": "pending",
                  "remark": "两张待处理"}],
    }, "receiver", self.now)
    self.assertEqual(self.scalar("SELECT item_name FROM purchase_order_items WHERE id=?", self.item_id), "镀锌板")
    self.assertEqual(self.scalar("SELECT item_name FROM purchase_receipt_items"), "实际镀锌板")
    self.assertEqual(self.scalar("SELECT available_quantity FROM purchase_inventory_lots"), 98)
    self.assertEqual(result["status"], "posted")

def test_invalid_second_row_rolls_back_everything(self):
    payload = self.two_row_payload(second_location_id=999999)
    with self.assertRaises(PurchaseInventoryConflict):
        post_purchase_receipt(self.conn, payload, "receiver", self.now)
    self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)
    self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_inventory_lots"), 0)
```

Also cover raw-material `length/width/thickness`, carton `length/width/height`, optional outsourcing/other fields, zero qualified quantity, partial receipts, all-complete status, stale previews, matching retry, changed retry, and void restrictions.

- [ ] **Step 2: Run receipt tests and confirm service failures**

Run: `python -m unittest tests.test_purchase_receipts -v`

Expected: FAIL because receipt preview/post/void services are absent.

- [ ] **Step 3: Implement normalized actual-snapshot validation**

```python
ACTUAL_FIELDS = ("item_name", "drawing_no", "material", "dimension_text",
                 "surface", "spec", "unit", "length", "width", "height", "thickness")

def normalize_receipt_row(source, category):
    row = {name: normalize_purchase_text(source.get(name)) for name in ACTUAL_FIELDS[:7]}
    row.update({name: parse_optional_positive_decimal(source.get(name)) for name in ACTUAL_FIELDS[7:]})
    row["actual_quantity"] = parse_purchase_inventory_quantity(source.get("actual_quantity"))
    row["qualified_quantity"] = parse_purchase_inventory_quantity(source.get("qualified_quantity"), allow_zero=True)
    if row["qualified_quantity"] > row["actual_quantity"]:
        raise ValueError("合格入库数量不能大于实际到货数量")
    validate_actual_fields_for_category(row, category)
    return row
```

Require material plus long/width/thickness for raw material, material plus long/width/height for carton, and item name or drawing number for outsourcing/other. Permit zero qualified quantity while preserving the receipt row.

- [ ] **Step 4: Implement transactional preview, posting, idempotency, and order status**

Within `BEGIN IMMEDIATE`, reload order, items, posted cumulative quantities, and enabled locations; compare preview identity; reject cancelled orders and cross-order/cross-category rows. A confirmed over-receipt can proceed only when `confirm_over_receipt is True`. Insert receipt, snapshots, one receipt-source lot for each positive qualified row, and one positive `receipt` transaction per lot. Recalculate `purchase_orders.status` from non-voided cumulative actual quantities.

- [ ] **Step 5: Implement receipt void and reversal**

Void only when every original receipt lot still contains its full opening quantity and no descendant transfer lot or outbound usage depends on it. Write negative `reversal` transactions, zero the affected availability, mark the receipt void, and recalculate order status. A second void returns the existing result without another balance change.

- [ ] **Step 6: Run domain tests**

Run: `python -m unittest tests.test_purchase_receipts tests.test_purchase_orders -v`

Expected: PASS for all four categories, partial/complete/over-receipt flows, rollback, retry, and void behavior.

- [ ] **Step 7: Commit receipt services**

```bash
git add procurement_inventory.py procurement.py tests/test_purchase_receipts.py
git commit -m "feat: post purchase receipt snapshots"
```

### Task 3: Category receipt pages and batch inbound

**Files:**
- Modify: `app.py`
- Modify: `templates/purchase_tabs.html`
- Create: `templates/purchase_receipts.html`
- Create: `templates/purchase_receipt_form.html`
- Create: `templates/purchase_receipt_detail.html`
- Create: `static/purchase-inventory.css`
- Create: `static/purchase-receipts.js`
- Test: `tests/test_purchase_receipt_routes.py`
- Test: `tests/js/purchase-receipts.test.js`

**Interfaces:**
- Consumes: Task 2 receipt services
- Produces routes: `GET /admin/purchase-receipts/<category>`
- Produces routes: `GET|POST /admin/purchase-receipts/<category>/<int:order_id>/new`
- Produces routes: `GET /admin/purchase-receipts/records/<int:receipt_id>`
- Produces routes: `POST /admin/purchase-receipts/records/<int:receipt_id>/void`

- [ ] **Step 1: Write failing route and browser-state tests**

```python
def test_raw_receipt_search_filters_supplier_and_order(self):
    response = self.client.get("/admin/purchase-receipts/raw-material", query_string={
        "supplier_id": self.supplier_a, "order_no": "PO-RAW", "q": "镀锌"
    })
    html = response.get_data(as_text=True)
    self.assertEqual(response.status_code, 200)
    self.assertIn("PO-RAW", html)
    self.assertNotIn("PO-CARTON", html)

def test_post_requires_receipt_permission_and_csrf(self):
    self.login_without("purchase_receipt")
    response = self.client.post(self.url, json=self.payload)
    self.assertEqual(response.status_code, 302)
    self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_receipts"), 0)
```

```javascript
test('carton uses height and raw material uses thickness', () => {
  assert.deepEqual(visibleDimensionFields('carton'), ['length', 'width', 'height']);
  assert.deepEqual(visibleDimensionFields('raw_material'), ['length', 'width', 'thickness']);
});
```

- [ ] **Step 2: Run tests and verify missing pages**

Run: `python -m unittest tests.test_purchase_receipt_routes -v`

Run: `node --test tests/js/purchase-receipts.test.js`

Expected: FAIL with missing routes/assets.

- [ ] **Step 3: Build supplier/order search and category navigation**

Use `purchase_category_from_slug()` and `load_receivable_orders()`. Display supplier, order number, order date, item summary, status, ordered quantity, cumulative actual, and remaining quantity. Preserve the current category when searching or clearing.

- [ ] **Step 4: Build editable batch receipt form**

Preload order values in editable actual-snapshot fields. Render category-specific dimension columns, row checkboxes, actual quantity, qualified quantity, enabled location, invoice state, and remark. Only checked rows submit. Use the existing `inventory_csrf_token()`/`require_inventory_csrf()` pair and a signed preview token.

- [ ] **Step 5: Implement JSON response and conflict behavior**

Return 201 for new receipt, 200 with `duplicate: true` for a matching retry, 409 with `needs_confirmation: true` for over-receipt, and 409 with current quantities for stale or concurrent changes. Preserve entered values after conflicts; confirmation resubmits the same idempotency key and payload plus `confirm_over_receipt: true`.

- [ ] **Step 6: Add detail and void pages**

Show order values beside actual snapshot values when they differ. Show actual, qualified, location, invoice status, operator, timestamps, and linked lot number. Hide the void action unless all reversal conditions are currently satisfied.

- [ ] **Step 7: Run route and JavaScript tests**

Run: `python -m unittest tests.test_purchase_receipt_routes tests.test_procurement_permissions -v`

Run: `node --test tests/js/purchase-receipts.test.js`

Expected: PASS for search, category fields, access control, CSRF, over-receipt confirmation, retries, and details.

- [ ] **Step 8: Commit receipt pages**

```bash
git add app.py templates/purchase_tabs.html templates/purchase_receipts.html templates/purchase_receipt_form.html templates/purchase_receipt_detail.html static/purchase-inventory.css static/purchase-receipts.js tests/test_purchase_receipt_routes.py tests/js/purchase-receipts.test.js
git commit -m "feat: add category purchase receiving"
```

### Task 4: Purchase inventory filters, transfer, quantity adjustment, and invoice state

**Files:**
- Modify: `procurement_inventory.py`
- Modify: `app.py`
- Modify: `templates/inventory_overview.html`
- Create: `templates/purchase_inventory.html`
- Create: `templates/purchase_inventory_change.html`
- Modify: `static/purchase-inventory.css`
- Test: `tests/test_purchase_inventory.py`
- Test: `tests/test_purchase_inventory_routes.py`

**Interfaces:**
- Produces: `fetch_purchase_inventory(conn, filters: dict, include_prices: bool) -> list[dict]`
- Produces: `transfer_purchase_inventory(conn, lot_id: int, quantity: int, target_location_id: int, reason: str, actor: str, now: str, expected_version: int, idempotency_key: str) -> dict`
- Produces: `adjust_purchase_inventory(conn, lot_id: int, counted_quantity: int, reason: str, actor: str, now: str, expected_version: int, idempotency_key: str) -> dict`
- Produces: `update_purchase_invoice_status(conn, lot_id: int, new_status: str, remark: str, actor: str, now: str, idempotency_key: str) -> dict`
- Produces route: `GET /admin/purchase-inventory/<category>`
- Produces route: `POST /admin/purchase-inventory/lots/<int:lot_id>/transfer`
- Produces route: `POST /admin/purchase-inventory/lots/<int:lot_id>/adjust`
- Produces route: `POST /admin/purchase-inventory/lots/<int:lot_id>/invoice-status`

- [ ] **Step 1: Write failing query, price, transfer, and adjustment tests**

```python
def test_inventory_filters_supplier_order_name_spec_and_location(self):
    rows = fetch_purchase_inventory(self.conn, {
        "category": "raw_material", "supplier_id": str(self.supplier_id),
        "order_no": "PO-RAW", "q": "DC51D", "location_id": str(self.location_id),
    }, include_prices=False)
    self.assertEqual([row["id"] for row in rows], [self.raw_lot])
    self.assertNotIn("unit_price_minor", rows[0])

def test_partial_transfer_creates_linked_target_lot_and_conserves_quantity(self):
    before = self.total_available()
    result = transfer_purchase_inventory(
        self.conn, self.raw_lot, 20, self.target_location, "换库位",
        "keeper", self.now, self.version(self.raw_lot), "transfer-1")
    self.assertEqual(self.available(self.raw_lot), 80)
    self.assertEqual(self.available(result["target_lot_id"]), 20)
    self.assertEqual(self.total_available(), before)
    self.assertEqual(self.transaction_deltas(result["transfer_id"]), [-20, 20])
```

Also test full transfer, same-location rejection, disabled target, insufficient quantity, stale version, zero/no-op adjustment, mandatory reasons, negative counted quantity, and invoice-event auditing.

- [ ] **Step 2: Run focused tests and confirm missing behavior**

Run: `python -m unittest tests.test_purchase_inventory tests.test_purchase_inventory_routes -v`

Expected: FAIL because inventory services and pages are absent.

- [ ] **Step 3: Implement inventory projection and filters**

Return category, actual item fields, supplier, order/receipt/lot numbers, received/created date, location, opening/current quantities, version, invoice status, and prices only when authorized. Support category, supplier, order number, receipt number, text, location, date range, invoice state, and `include_zero=1`.

- [ ] **Step 4: Implement transactional partial/full transfer**

Lock and reload the source lot. Require positive quantity, nonblank reason, enabled different target location, exact expected version, and enough availability. Decrement and version the source; create a new `source_kind='transfer'` target lot copying all snapshots and linking `source_lot_id`; write paired `transfer_out`/`transfer_in` rows with opposite deltas and reciprocal `paired_transaction_id` values.

- [ ] **Step 5: Implement audited quantity and invoice adjustments**

Create one `purchase_inventory_operations` row for every transfer, quantity adjustment, and invoice-state change. A matching idempotency-key retry returns its saved result; reusing the key with changed input conflicts. Quantity adjustment computes `delta = counted_quantity - available_quantity`, rejects zero delta, increments the lot version, and writes an `adjustment` transaction containing `盘点调整：{old_quantity} -> {counted_quantity}；原因：{reason}`. Invoice changes never alter stock and always write `purchase_inventory_invoice_events`.

- [ ] **Step 6: Build category stock pages and protected actions**

Add “采购库存” to product inventory navigation. Render four category tabs, filters, optional zero-stock rows, and price-safe columns. Adjustment and transfer use a common modal/page with current version. Require `purchase_inventory_view` for the list, `purchase_inventory_adjust` for transfer/quantity changes, and `purchase_receipt` for invoice-state changes.

- [ ] **Step 7: Run purchase and product inventory regression tests**

Run: `python -m unittest tests.test_purchase_inventory tests.test_purchase_inventory_routes tests.test_inventory tests.test_inventory_filters tests.test_inventory_batch -v`

Expected: PASS; product balances and product inventory transactions remain unchanged.

- [ ] **Step 8: Commit independent purchase inventory**

```bash
git add procurement_inventory.py app.py templates/inventory_overview.html templates/purchase_inventory.html templates/purchase_inventory_change.html static/purchase-inventory.css tests/test_purchase_inventory.py tests/test_purchase_inventory_routes.py
git commit -m "feat: manage independent purchase inventory"
```

### Task 5: Raw-material order format fix and receipt/inventory documents

**Files:**
- Modify: `procurement_documents.py`
- Modify: `app.py`
- Modify: `templates/purchase_receipt_detail.html`
- Modify: `templates/purchase_inventory.html`
- Test: `tests/test_purchase_order_exports.py`
- Test: `tests/test_purchase_receipt_exports.py`

**Interfaces:**
- Extends: `build_purchase_order_workbook(order, items, *, include_prices: bool) -> BytesIO`
- Extends: `build_purchase_order_pdf(order, items, *, include_prices: bool) -> BytesIO`
- Produces: `build_purchase_receipt_workbook(receipt, items, *, include_prices: bool) -> BytesIO`
- Produces: `build_purchase_receipt_pdf(receipt, items, *, include_prices: bool) -> BytesIO`
- Produces: `build_purchase_inventory_workbook(filters, rows, *, include_prices: bool) -> BytesIO`

- [ ] **Step 1: Write failing raw-material format and fixed-term tests**

```python
def test_raw_material_dimensions_and_quantity_are_general_numeric_cells(self):
    sheet = load_workbook(build_purchase_order_workbook(
        sample_order("raw_material"), sample_items(), include_prices=False)).active
    header = next(row for row in sheet if any(cell.value == "数量" for cell in row))
    data = sheet[header[0].row + 1]
    for label in ("长 mm", "宽 mm", "厚度 mm", "数量"):
        cell = data[next(cell.column for cell in header if cell.value == label) - 1]
        self.assertEqual(cell.data_type, "n")
        self.assertEqual(cell.number_format, "General")

def test_standard_terms_are_raw_material_only_and_custom_remark_survives(self):
    required = ("产品交付时必须标识明确", "订单要求纳入供应商考核")
    for category in ("raw_material", "carton", "outsourcing", "other"):
        text = workbook_text(build_purchase_order_workbook(
            sample_order(category), sample_items(), include_prices=False))
        self.assertEqual(all(term in text for term in required), category == "raw_material")
        self.assertIn("请工作日送货", text)
```

- [ ] **Step 2: Write failing receipt/inventory document tests**

Assert landscape A4, one-page-wide print setup, actual snapshot fields, order/receipt/lot numbers, actual/qualified/current quantities, locations, operators, standard terms, Chinese extraction, and absence of prices from unauthorized files.

- [ ] **Step 3: Run export tests and verify failures**

Run: `python -m unittest tests.test_purchase_order_exports tests.test_purchase_receipt_exports -v`

Expected: FAIL on General number formats, raw-only terms, and missing receipt/inventory builders.

- [ ] **Step 4: Correct raw-material Excel/PDF output**

Change numeric writing so raw-material `length`, `width`, `thickness`, and `ordered_quantity` remain Python numeric values with Excel `General` format. Keep dates typed and price formats unchanged. Append the two approved standard terms only when `order['category'] == 'raw_material'`; render custom order remarks in a separate labeled row in both formats.

- [ ] **Step 5: Implement receipt and inventory exports**

Use existing document fonts, text sanitization, snapshot projection, and landscape-A4 layout. Receipt exports include ordered-versus-actual values, actual/qualified quantities, location, invoice status, receipt/order numbers, and operator. Inventory export uses the active filters and includes current quantity and location; price columns are built only when permitted.

- [ ] **Step 6: Run workbook/PDF layout, type, and security tests**

Run: `python -m unittest tests.test_purchase_order_exports tests.test_purchase_template_layout tests.test_purchase_receipt_exports -v`

Expected: PASS with numeric General cells, terms isolated to raw material, editable custom remarks retained, and no hidden prices in XLSX ZIP parts or PDF text.

- [ ] **Step 7: Commit procurement documents**

```bash
git add procurement_documents.py app.py templates/purchase_receipt_detail.html templates/purchase_inventory.html tests/test_purchase_order_exports.py tests/test_purchase_receipt_exports.py
git commit -m "feat: export purchase receiving documents"
```

### Task 6: Purchase-goods outbound domain and stock deduction

**Files:**
- Modify: `procurement_inventory.py`
- Test: `tests/test_purchase_inventory_outbound.py`

**Interfaces:**
- Produces: `load_outbound_candidates(conn, category: str, filters: dict) -> list[dict]`
- Produces: `post_purchase_outbound(conn, payload: dict, actor: str, now: str) -> dict`
- Produces: `void_purchase_outbound(conn, outbound_id: int, actor: str, now: str) -> dict`

- [ ] **Step 1: Write failing outbound transaction tests**

```python
def test_multi_lot_outbound_records_operator_recipient_and_deducts_atomically(self):
    result = post_purchase_outbound(self.conn, {
        "idempotency_key": "out-1", "outbound_at": "2026-09-12",
        "used_by": "王师傅", "remark": "一车间领用",
        "rows": [{"lot_id": self.raw_a, "quantity": "2", "remark": "机架"},
                 {"lot_id": self.raw_b, "quantity": "3", "remark": "面板"}],
    }, "admin", self.now)
    self.assertEqual((self.available(self.raw_a), self.available(self.raw_b)), (8, 4))
    self.assertEqual(self.header(result["id"])["operator"], "admin")
    self.assertEqual(self.header(result["id"])["used_by"], "王师傅")

def test_insufficient_second_lot_rolls_back_all_rows(self):
    with self.assertRaises(PurchaseInventoryConflict):
        post_purchase_outbound(self.conn, self.payload(a=2, b=999), "admin", self.now)
    self.assertEqual((self.available(self.raw_a), self.available(self.raw_b)), (10, 7))
```

Also cover duplicate lot IDs, cross-category rows, disabled locations, empty recipient, matching retry, changed retry, stale balance, and void restoration.

- [ ] **Step 2: Run the outbound domain test and confirm missing services**

Run: `python -m unittest tests.test_purchase_inventory_outbound -v`

Expected: FAIL because outbound services are absent.

- [ ] **Step 3: Implement candidate filtering and normalized rows**

Only return `available_quantity > 0` lots from the requested category. Filters include supplier, order number, item/drawing/spec text, and location. Normalize each selected lot once, require positive integer quantity, nonblank `used_by`, valid date, and bounded remarks.

- [ ] **Step 4: Implement atomic posting and retry identity**

Use `BEGIN IMMEDIATE`, sort selected lot IDs, reload all lots, verify category and availability, insert the outbound header and immutable snapshot rows, decrement each lot and increment its version, then insert negative `outbound` transactions. Hash category, date, recipient, remarks, lot IDs, quantities, and row remarks for idempotency.

- [ ] **Step 5: Implement outbound void**

For a posted outbound, restore the exact original lots with positive `reversal` transactions and increment versions. Mark the header voided and record actor/time. A repeated void is idempotent and cannot restore twice.

- [ ] **Step 6: Run domain and invariant tests**

Run: `python -m unittest tests.test_purchase_inventory_outbound tests.test_purchase_inventory_schema -v`

Expected: PASS with no negative balances and no invariant errors.

- [ ] **Step 7: Commit outbound services**

```bash
git add procurement_inventory.py tests/test_purchase_inventory_outbound.py
git commit -m "feat: deduct purchase inventory for internal use"
```

### Task 7: Category outbound pages and outbound documents

**Files:**
- Modify: `app.py`
- Modify: `templates/shipment_tabs.html`
- Create: `templates/purchase_inventory_outbounds.html`
- Create: `templates/purchase_inventory_outbound_form.html`
- Create: `templates/purchase_inventory_outbound_detail.html`
- Create: `static/purchase-inventory-outbound.js`
- Modify: `static/purchase-inventory.css`
- Modify: `procurement_documents.py`
- Test: `tests/test_purchase_inventory_outbound_routes.py`
- Test: `tests/test_purchase_inventory_outbound_exports.py`
- Test: `tests/js/purchase-inventory-outbound.test.js`

**Interfaces:**
- Consumes: Task 6 outbound services
- Produces routes: `GET /admin/shipping/purchase-goods/<category>`
- Produces routes: `GET|POST /admin/shipping/purchase-goods/<category>/new`
- Produces routes: `GET /admin/shipping/purchase-goods/records/<int:outbound_id>`
- Produces routes: `POST /admin/shipping/purchase-goods/records/<int:outbound_id>/void`
- Produces routes: `GET /admin/shipping/purchase-goods/records/<int:outbound_id>/export.<format>`
- Produces: `build_purchase_outbound_workbook(outbound, items) -> BytesIO`
- Produces: `build_purchase_outbound_pdf(outbound, items) -> BytesIO`

- [ ] **Step 1: Write failing route, client-validation, and export tests**

```python
def test_category_page_only_lists_matching_available_stock(self):
    response = self.client.get("/admin/shipping/purchase-goods/raw-material/new", query_string={
        "supplier_id": self.supplier_id, "order_no": "PO-RAW", "q": "镀锌"
    })
    html = response.get_data(as_text=True)
    self.assertIn(self.raw_lot_no, html)
    self.assertNotIn(self.carton_lot_no, html)

def test_outbound_does_not_create_customer_shipping_or_finance_rows(self):
    before = self.sales_table_counts()
    self.post_outbound()
    self.assertEqual(self.sales_table_counts(), before)
```

```javascript
test('selected rows require positive quantities and a recipient', () => {
  assert.equal(validateOutbound({usedBy: '', rows: [{selected: true, quantity: '2'}]}).valid, false);
  assert.equal(validateOutbound({usedBy: '王师傅', rows: [{selected: true, quantity: '0'}]}).valid, false);
});
```

- [ ] **Step 2: Run route, export, and JavaScript tests**

Run: `python -m unittest tests.test_purchase_inventory_outbound_routes tests.test_purchase_inventory_outbound_exports -v`

Run: `node --test tests/js/purchase-inventory-outbound.test.js`

Expected: FAIL with missing routes, templates, JS, and builders.

- [ ] **Step 3: Build category history and multi-select form**

Add “采购货物出库” to `shipment_tabs.html` for users with `purchase_inventory_outbound`. Provide four category tabs and filters. Display lot number, actual item, supplier, order, location, and current quantity; require at least one checked row, positive quantities, recipient, and date. Show current login as read-only “经办人”.

- [ ] **Step 4: Implement protected JSON post, detail, and void routes**

Use inventory CSRF and permission guards. Return 201/200/409 consistently with receipt routes. On conflicts, return refreshed lot availability and keep user entries. Detail pages use outbound snapshots, not current supplier or lot display values.

- [ ] **Step 5: Implement landscape-A4 Excel/PDF documents**

Include outbound number/date, category, recipient, operator, item/drawing, material/spec/dimensions, supplier, source order, lot, source location, quantity, and remarks. Excel cells for quantities are numeric; PDF and Excel use saved snapshots and no purchase price columns.

- [ ] **Step 6: Run route, document, permission, and isolation tests**

Run: `python -m unittest tests.test_purchase_inventory_outbound_routes tests.test_purchase_inventory_outbound_exports tests.test_procurement_permissions -v`

Run: `node --test tests/js/purchase-inventory-outbound.test.js`

Expected: PASS; outbound cannot write delivery-note, shipped-order, product-order, invoice, or product-inventory tables.

- [ ] **Step 7: Commit outbound pages and documents**

```bash
git add app.py procurement_documents.py templates/shipment_tabs.html templates/purchase_inventory_outbounds.html templates/purchase_inventory_outbound_form.html templates/purchase_inventory_outbound_detail.html static/purchase-inventory.css static/purchase-inventory-outbound.js tests/test_purchase_inventory_outbound_routes.py tests/test_purchase_inventory_outbound_exports.py tests/js/purchase-inventory-outbound.test.js
git commit -m "feat: add category purchase goods outbound"
```

### Task 8: Full verification and local acceptance

**Files:**
- Create: `tests/test_purchase_inventory_isolation.py`
- Create: `docs/2026-09-12-procurement-inventory-usage.md`
- Create: `docs/2026-09-12-procurement-inventory-verification.md`
- Modify: `docs/superpowers/plans/2026-09-12-procurement-receiving-inventory-outbound.md`

**Interfaces:**
- Consumes: all receipt, inventory, transfer, adjustment, outbound, and document behavior
- Produces: a verified local release candidate; no deployment

- [ ] **Step 1: Add end-to-end isolation and accounting tests**

```python
def test_receipt_transfer_adjust_outbound_and_void_preserve_domain_isolation(self):
    product_before = self.product_inventory_snapshot()
    sales_before = self.sales_domain_snapshot()
    receipt = self.post_receipt(qualified=100)
    moved = self.transfer(receipt.lot_id, quantity=40)
    self.adjust(moved.target_lot_id, counted=38, reason="盘点少2张")
    outbound = self.outbound(moved.target_lot_id, quantity=10)
    self.void_outbound(outbound.id)
    self.assertEqual(self.product_inventory_snapshot(), product_before)
    self.assertEqual(self.sales_domain_snapshot(), sales_before)
    self.assertEqual(purchase_inventory_invariant_errors(self.conn), [])
```

- [ ] **Step 2: Run the complete automated suites**

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.js`

Expected: all tests PASS.

- [ ] **Step 3: Run database integrity and inventory invariants on a disposable database copy**

Run the application initialization on a copy in `/private/tmp`, then verify `PRAGMA integrity_check` returns `ok`, `PRAGMA foreign_key_check` is empty, `purchase_inventory_invariant_errors()` is empty, and product/sales table counts do not change during purchase-flow fixtures.

- [ ] **Step 4: Perform local browser acceptance for all four categories**

Verify supplier/order search, category fields, actual-value edits, split receipts, zero-qualified rows, over-receipt confirmation, batch rollback, history/detail, stock filters, transfer, quantity adjustment, invoice state, multi-lot outbound, insufficient-stock conflict, void, and permission visibility. Confirm existing product inventory and customer shipping pages still work.

- [ ] **Step 5: Render and inspect representative documents**

Generate raw-material order Excel/PDF, carton order Excel/PDF, receipt Excel/PDF, inventory Excel, and outbound Excel/PDF. Confirm landscape A4, readable Chinese, no clipping, numeric General cells in raw-material orders, the two fixed terms only in raw-material orders, and no prices in unauthorized exports.

- [ ] **Step 6: Write user and verification documents**

The usage guide must describe the four entry points, actual-versus-order behavior, batch receipt, transfer, adjustment, outbound, void, and permissions in user-facing Chinese. The verification report must record exact test counts, browser cases, document samples, database integrity, invariant results, and known limitations.

- [ ] **Step 7: Commit the verified local release candidate**

```bash
git add tests/test_purchase_inventory_isolation.py docs/2026-09-12-procurement-inventory-usage.md docs/2026-09-12-procurement-inventory-verification.md docs/superpowers/plans/2026-09-12-procurement-receiving-inventory-outbound.md
git commit -m "test: verify procurement inventory workflow"
```
