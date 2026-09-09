# Unified Procurement Phase 3: Purchase Inventory Outbound Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff select one or more purchase-inventory lots, record internal recipients and quantities, deduct stock safely, and generate auditable outbound lists.

**Architecture:** Extend `procurement_inventory.py` with an immutable outbound aggregate and transaction-safe lot deductions. Add a “采购货物出库” child page under shipping while keeping it separate from customer shipments, delivery notes, product-order quantities, and sales finance. Documents use saved outbound snapshots so later supplier or item edits cannot change history.

**Tech Stack:** Python 3, Flask, SQLite, Jinja, vanilla JavaScript, openpyxl, ReportLab, unittest, Node test runner

**Spec:** `docs/superpowers/specs/2026-09-09-unified-procurement-vendor-inventory-design.md`

## Global Constraints

- Phases 1 and 2 must be complete and passing.
- Purchase outbound is internal stock issue; it never creates customer delivery notes or sales-finance sources.
- Stock is deducted from explicitly selected lots; no automatic cross-lot allocation.
- Negative inventory is forbidden.
- Posted outbound records are corrected through void/reversal.
- Warehouse locations are shared master data while purchase/product balances remain separate.
- Deployment requires a separate explicit instruction.

---

### Task 1: Outbound schema and transactional domain

**Files:**
- Modify: `procurement_inventory.py`
- Test: `tests/test_purchase_inventory_outbound.py`

**Interfaces:**
- Produces: `ensure_purchase_outbound_tables(conn) -> None`
- Produces: `load_outbound_candidates(conn, filters: dict) -> list[dict]`
- Produces: `post_purchase_outbound(conn, payload: dict, actor: str, now: str) -> dict`
- Produces: `void_purchase_outbound(conn, outbound_id: int, actor: str, now: str) -> dict`
- Produces: `parse_positive_id(value: object) -> int`

- [ ] **Step 1: Write failing schema, deduction, and rollback tests**

```python
def test_multi_lot_outbound_deducts_selected_lots_and_writes_transactions(self):
    result = procurement_inventory.post_purchase_outbound(
        self.conn,
        {"idempotency_key": "out-1", "used_by": "张三", "outbound_at": "2026-09-09",
         "remark": "车间领用", "rows": [
             {"lot_id": self.lot_a, "quantity": 2, "remark": "一车间"},
             {"lot_id": self.lot_b, "quantity": 3, "remark": "二车间"},
         ]},
        "keeper", self.now,
    )
    self.assertEqual(result["status"], "posted")
    self.assertEqual(self.available(self.lot_a), 8)
    self.assertEqual(self.available(self.lot_b), 4)
    self.assertEqual(self.transaction_deltas(result["id"]), [-2, -3])

def test_insufficient_second_lot_rolls_back_all_rows(self):
    with self.assertRaises(procurement_inventory.PurchaseInventoryConflict):
        procurement_inventory.post_purchase_outbound(self.conn, self.payload(a=2, b=999), "keeper", self.now)
    self.assertEqual((self.available(self.lot_a), self.available(self.lot_b)), (10, 7))
```

- [ ] **Step 2: Run tests and verify missing outbound services**

Run: `python -m unittest tests.test_purchase_inventory_outbound -v`

Expected: FAIL because outbound tables and functions are absent.

- [ ] **Step 3: Implement tables and exact validation rules**

Create `purchase_inventory_outbounds` and `purchase_inventory_outbound_items` with unique outbound/idempotency keys, `posted|voided` status checks, positive quantity checks, lot/location/item/supplier snapshots, and restrictive foreign keys.

```python
def normalize_outbound_rows(rows):
    normalized = []
    seen = set()
    for row in rows:
        lot_id = parse_positive_id(row.get("lot_id"))
        if lot_id in seen:
            raise ValueError("同一库存批次不能重复添加")
        seen.add(lot_id)
        normalized.append({"lot_id": lot_id,
                           "quantity": parse_purchase_quantity(row.get("quantity")),
                           "remark": str(row.get("remark") or "").strip()[:500]})
    if not normalized:
        raise ValueError("请至少选择一项采购库存")
    return normalized
```

- [ ] **Step 4: Implement atomic deduction, retry identity, and void reversal**

Reload every selected lot in one write transaction, require available quantity, lock identity to the exact selected lots/quantities/metadata, save header and snapshots, decrement lots, and insert `outbound` negative transactions. A matching retry returns the existing outbound; a reused key with changed payload conflicts. Void restores the exact lots with positive `reversal` transactions and refuses a second stock restoration.

- [ ] **Step 5: Run domain tests**

Run: `python -m unittest tests.test_purchase_inventory_outbound -v`

Expected: PASS for multi-lot deduction, insufficient stock rollback, duplicates, disabled locations, idempotent retry, changed retry conflict, and void restoration.

- [ ] **Step 6: Commit outbound domain**

```bash
git add procurement_inventory.py tests/test_purchase_inventory_outbound.py
git commit -m "feat: deduct purchase inventory for internal use"
```

### Task 2: Purchase-goods outbound pages under shipping

**Files:**
- Modify: `app.py`
- Modify: `templates/shipment_tabs.html`
- Create: `templates/purchase_inventory_outbounds.html`
- Create: `templates/purchase_inventory_outbound_form.html`
- Create: `templates/purchase_inventory_outbound_detail.html`
- Create: `static/purchase-inventory-outbound.js`
- Modify: `static/purchase-inventory.css`
- Test: `tests/test_purchase_inventory_outbound_routes.py`
- Test: `tests/js/purchase-inventory-outbound.test.js`

**Interfaces:**
- Consumes: `load_outbound_candidates`, `post_purchase_outbound`, `void_purchase_outbound`
- Produces routes `/admin/shipping/purchase-goods`, `/new`, `/<id>`, `/<id>/void`

- [ ] **Step 1: Write failing filter, form, CSRF, and permission tests**

```python
def test_candidates_filter_category_supplier_name_and_location(self):
    response = self.client.get("/admin/shipping/purchase-goods/new", query_string={
        "category": "other", "supplier_id": self.supplier_id,
        "q": "螺栓", "location_id": self.location_id,
    })
    html = response.get_data(as_text=True)
    self.assertEqual(response.status_code, 200)
    self.assertIn("M8螺栓", html)
    self.assertNotIn("304板材", html)

def test_outbound_post_requires_specific_permission_and_csrf(self):
    self.login_without("purchase_inventory_outbound")
    self.assertEqual(self.client.post("/admin/shipping/purchase-goods/new", json=self.payload).status_code, 302)
    self.assertEqual(self.outbound_count(), 0)
```

- [ ] **Step 2: Run tests and confirm missing routes**

Run: `python -m unittest tests.test_purchase_inventory_outbound_routes -v && node --test tests/js/purchase-inventory-outbound.test.js`

Expected: FAIL with 404/missing module.

- [ ] **Step 3: Implement the list, candidate search, and multi-select form**

Display category, item/drawing number, material/spec, supplier, lot, receipt date, location, and available quantity. Keep selected rows when search filters change on the client. Require recipient, date, checked row, and positive quantities.

```javascript
export function validateOutboundRows(rows) {
  const selected = rows.filter((row) => row.selected);
  if (!selected.length) return {valid: false, message: '请至少选择一项采购库存'};
  if (selected.some((row) => !/^\d+$/.test(row.quantity) || Number(row.quantity) <= 0))
    return {valid: false, message: '出库数量必须为正整数'};
  return {valid: true};
}
```

- [ ] **Step 4: Implement JSON submit, conflict refresh, detail, and void pages**

Translate validation to 400, stale/stock/idempotency conflicts to 409, successful new saves to 201, and matching retries to 200 with `duplicate: true`. On 409, return refreshed availability and preserve the user's entered quantities for comparison.

- [ ] **Step 5: Run route and JavaScript tests**

Run: `python -m unittest tests.test_purchase_inventory_outbound_routes -v && node --test tests/js/purchase-inventory-outbound.test.js`

Expected: PASS; no outbound route inserts delivery-note sources or sales-finance rows.

- [ ] **Step 6: Commit outbound UI**

```bash
git add app.py templates/shipment_tabs.html templates/purchase_inventory_outbounds.html templates/purchase_inventory_outbound_form.html templates/purchase_inventory_outbound_detail.html static/purchase-inventory-outbound.js static/purchase-inventory.css tests/test_purchase_inventory_outbound_routes.py tests/js/purchase-inventory-outbound.test.js
git commit -m "feat: add purchase goods outbound pages"
```

### Task 3: Outbound list Excel/PDF and print view

**Files:**
- Modify: `procurement_documents.py`
- Modify: `app.py`
- Modify: `templates/purchase_inventory_outbound_detail.html`
- Test: `tests/test_purchase_inventory_outbound_exports.py`

**Interfaces:**
- Produces: `build_purchase_outbound_workbook(outbound, items) -> BytesIO`
- Produces: `build_purchase_outbound_pdf(outbound, items) -> BytesIO`

- [ ] **Step 1: Write failing snapshot and page-setup tests**

```python
def test_outbound_workbook_uses_snapshots_and_landscape_a4(self):
    stream = build_purchase_outbound_workbook(self.outbound, self.items)
    sheet = load_workbook(stream).active
    self.assertEqual(sheet.page_setup.orientation, "landscape")
    self.assertEqual(sheet.page_setup.paperSize, sheet.PAPERSIZE_A4)
    values = [cell.value for row in sheet for cell in row]
    self.assertIn("领用人：张三", values)
    self.assertIn("旧供应商名称", values)
```

- [ ] **Step 2: Run export tests and confirm missing builders**

Run: `python -m unittest tests.test_purchase_inventory_outbound_exports -v`

Expected: FAIL because builders are missing.

- [ ] **Step 3: Implement printable page, workbook, and PDF**

Include outbound number/date, recipient, operator, category, item, supplier, lot, warehouse location, unit, quantity, row remark, and overall remark. Never include purchase price because this is a quantity handoff document. Use saved snapshots and repeat detail headers across pages.

- [ ] **Step 4: Add guarded routes and run tests**

Run: `python -m unittest tests.test_purchase_inventory_outbound_exports tests.test_purchase_inventory_outbound_routes -v`

Expected: PASS for active and voided documents; voided documents show a clear “已作废” mark.

- [ ] **Step 5: Commit outbound documents**

```bash
git add procurement_documents.py app.py templates/purchase_inventory_outbound_detail.html tests/test_purchase_inventory_outbound_exports.py
git commit -m "feat: export purchase outbound lists"
```

### Task 4: Cross-domain isolation and phase-three verification

**Files:**
- Create: `tests/test_purchase_inventory_isolation.py`
- Modify: `templates/base.html`
- Modify: `docs/superpowers/plans/2026-09-09-unified-procurement-phase-3-outbound.md`

**Interfaces:**
- Consumes: receipt, lot, outbound, product inventory, shipment, delivery-note, and finance tables
- Produces: verified proof that internal purchase outbound is isolated from customer shipment flows

- [ ] **Step 1: Write and run isolation tests**

```python
def test_purchase_outbound_does_not_touch_product_or_sales_tables(self):
    before = self.counts("inventory_balances", "inventory_transactions",
                         "product_order_shipments", "assembly_shipment_items",
                         "delivery_notes", "delivery_note_sources", "finance_invoice_items")
    self.post_purchase_outbound()
    self.assertEqual(before, self.counts(*before.keys()))
```

Run: `python -m unittest tests.test_purchase_inventory_isolation -v`

Expected: PASS; only purchase-outbound tables, purchase lots, and purchase transactions change.

- [ ] **Step 2: Run complete automated regression**

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.js`

Expected: all tests PASS.

- [ ] **Step 3: Perform local browser acceptance**

Verify shipping tabs, multi-selection, supplier/name/location filters, row remarks, stale-stock refresh, insufficient-stock rejection, duplicate-submit behavior, detail page, print, Excel, PDF, void recovery, and permission denial.

- [ ] **Step 4: Run database invariants on the acceptance database**

Verify for every lot: `received_quantity + adjustment deltas + outbound/reversal deltas = available_quantity`, no negative balances, no orphan rows, and no new product/sales rows from purchase outbound.

- [ ] **Step 5: Record evidence and commit**

```bash
git add tests/test_purchase_inventory_isolation.py templates/base.html docs/superpowers/plans/2026-09-09-unified-procurement-phase-3-outbound.md
git commit -m "test: verify purchase outbound isolation"
```
