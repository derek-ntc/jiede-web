# Unified Procurement Phase 4: Customer Shipment Invoice Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make customer-filtered shipped lines easy to find, select, and turn into a non-duplicated finance invoice list without changing purchase inventory or existing invoice lifecycle behavior.

**Architecture:** Extend the existing finance source query rather than creating a second invoice system. Add date/order/delivery-number filters around `fetch_available_finance_sources`, keep the existing source claim key as the concurrency boundary, and update the new-invoice page to expose line selection and missing-price diagnostics. Purchase invoice-status tracking remains separate from sales invoices.

**Tech Stack:** Python 3, Flask, SQLite, Jinja, vanilla JavaScript, unittest, Node test runner

**Spec:** `docs/superpowers/specs/2026-09-09-unified-procurement-vendor-inventory-design.md`

## Global Constraints

- Use existing `finance_invoices` and `finance_invoice_items`; do not add a parallel sales-invoice model.
- Sources remain ordinary, assembly-item, and supplemental shipped lines.
- Quantity-zero delivery-note display rows are never finance sources.
- A shipped source can be actively claimed by only one non-void invoice.
- Missing-price positive lines cannot be submitted as a complete invoice list.
- The database default remains `CNY`; the UI labels it `RMB／人民币`, and historical currency values remain unchanged.
- Purchase receipt invoice status does not create or update sales invoice rows.
- Deployment requires a separate explicit instruction.

---

### Task 1: Filterable finance-source query contract

**Files:**
- Modify: `app.py:6443-6577`
- Test: `tests/test_finance_source_filters.py`

**Interfaces:**
- Replaces: `fetch_available_finance_sources(conn, customer_name, currency="")`
- Produces: `fetch_available_finance_sources(conn, customer_name: str, currency: str = "", *, shipped_from: str = "", shipped_to: str = "", order_no: str = "", delivery_no: str = "") -> list[dict]`

- [ ] **Step 1: Write failing source filter and exclusion tests**

```python
def test_sources_filter_customer_date_order_and_delivery_number(self):
    rows = app.fetch_available_finance_sources(
        self.conn, "客户A", "CNY", shipped_from="2026-09-01",
        shipped_to="2026-09-30", order_no="SO-100", delivery_no="DN-100",
    )
    self.assertEqual([row["source_key"] for row in rows], [self.expected_source])

def test_zero_quantity_claimed_voided_and_other_customer_sources_are_excluded(self):
    rows = app.fetch_available_finance_sources(self.conn, "客户A", "CNY")
    self.assertNotIn(self.zero_source, self.keys(rows))
    self.assertNotIn(self.active_claimed_source, self.keys(rows))
    self.assertIn(self.void_invoice_source, self.keys(rows))
    self.assertNotIn(self.customer_b_source, self.keys(rows))
```

- [ ] **Step 2: Run tests and verify filter-signature failure**

Run: `python -m unittest tests.test_finance_source_filters -v`

Expected: FAIL because the query does not accept or apply all filters.

- [ ] **Step 3: Extend all three source branches consistently**

Normalize dates as ISO date strings, use bound parameters only, and apply customer/currency/date/order constraints in each ordinary/assembly/supplemental branch. Join delivery-note sources to expose a stable `delivery_no` without duplicating rows when a source is connected to one active note. Preserve source keys:

```python
source_key = f"{source_type}:{source_id}"
```

Exclude quantities `<= 0` and source keys held by non-void finance items. Return `price_missing` instead of coercing NULL prices to zero.

- [ ] **Step 4: Run finance source and legacy finance tests**

Run: `python -m unittest tests.test_finance_source_filters tests.test_finance -v`

Expected: PASS with existing totals, currency separation, claims, and void-release behavior unchanged.

- [ ] **Step 5: Commit source filters**

```bash
git add app.py tests/test_finance_source_filters.py
git commit -m "feat: filter shipped invoice sources"
```

### Task 2: Customer-first invoice-list selection interface

**Files:**
- Modify: `app.py:10900-11031`
- Modify: `templates/finance_invoice_new.html`
- Modify: `templates/finance_invoices.html`
- Modify: `static/finance.js`
- Test: `tests/test_finance_invoice_selection.py`
- Test: `tests/js/finance-source-selection.test.js`

**Interfaces:**
- Consumes: `fetch_available_finance_sources(conn, customer_name, currency="", *, shipped_from="", shipped_to="", order_no="", delivery_no="")`
- Produces: GET/POST filters `customer_id`, `shipped_from`, `shipped_to`, `order_no`, `delivery_no`, `currency`

- [ ] **Step 1: Write failing customer and filter UI tests**

```python
def test_new_invoice_requires_customer_before_showing_sources(self):
    response = self.client.get("/admin/finance/new")
    html = response.get_data(as_text=True)
    self.assertIn("请先选择客户", html)
    self.assertNotIn(self.source_drawing_no, html)

def test_selected_customer_lists_filterable_shipped_lines(self):
    response = self.client.get("/admin/finance/new", query_string={
        "customer_id": self.customer_id, "order_no": "SO-100"
    })
    html = response.get_data(as_text=True)
    self.assertIn("SO-100", html)
    self.assertNotIn("SO-200", html)
```

- [ ] **Step 2: Run route and JavaScript tests and confirm failure**

Run: `python -m unittest tests.test_finance_invoice_selection -v && node --test tests/js/finance-source-selection.test.js`

Expected: FAIL until filters and selection behavior are implemented.

- [ ] **Step 3: Render customer-first search and selectable shipped-line table**

Columns: selection, shipping date, delivery number, order number, assembly number, drawing number, product name, specification, quantity, tax-inclusive unit price, line total, currency, and warning. Preserve filter values after validation errors. Disable a missing-price row and show “缺少发货含税单价，请先补录”。

```javascript
export function invoiceSelectionSummary(rows) {
  const selected = rows.filter((row) => row.selected && !row.priceMissing);
  return {
    count: selected.length,
    quantity: selected.reduce((sum, row) => sum + Number(row.quantity), 0),
    totalMinor: selected.reduce((sum, row) => sum + Number(row.lineTotalMinor), 0),
  };
}
```

- [ ] **Step 4: Harden POST validation against tampered selections**

On submit, reload all selected source keys through the available-source query scoped to the posted customer and currency. Reject missing, claimed, zero-quantity, cross-customer, cross-currency, or unpriced rows before opening the invoice transaction. Never trust browser totals.

- [ ] **Step 5: Run selection and existing finance tests**

Run: `python -m unittest tests.test_finance_invoice_selection tests.test_finance -v && node --test tests/js/finance-source-selection.test.js`

Expected: PASS for empty selection, customer switching, filter persistence, missing price, tampered keys, and calculated summaries.

- [ ] **Step 6: Commit invoice-list selection UI**

```bash
git add app.py templates/finance_invoice_new.html templates/finance_invoices.html static/finance.js tests/test_finance_invoice_selection.py tests/js/finance-source-selection.test.js
git commit -m "feat: select shipped lines for invoicing"
```

### Task 3: Concurrency, claim release, and cross-domain isolation

**Files:**
- Modify: `app.py:6578-6687, 7464-7628`
- Create: `tests/test_finance_source_claims.py`
- Create: `tests/test_procurement_finance_isolation.py`

**Interfaces:**
- Consumes: `create_finance_invoice`, `replace_pending_invoice_items`, `void_finance_invoice`
- Produces: invariant that each active shipped source has at most one active claim
- Produces: `FinanceSourceConflict(ValueError)` for stale or duplicate source claims

- [ ] **Step 1: Write failing stale-selection and isolation tests**

```python
def test_second_invoice_from_stale_page_cannot_claim_same_source(self):
    first = app.create_finance_invoice(self.conn, self.customer_id, [self.source_ref], "finance-a")
    with self.assertRaises(app.FinanceSourceConflict):
        app.create_finance_invoice(self.conn, self.customer_id, [self.source_ref], "finance-b")
    self.assertEqual(self.active_claim_count(self.source_ref), 1)

def test_sales_invoice_does_not_change_purchase_invoice_status(self):
    before = self.purchase_invoice_statuses()
    app.create_finance_invoice(self.conn, self.customer_id, [self.source_ref], "finance")
    self.assertEqual(self.purchase_invoice_statuses(), before)
```

- [ ] **Step 2: Run tests and identify any non-atomic path**

Run: `python -m unittest tests.test_finance_source_claims tests.test_procurement_finance_isolation -v`

Expected: stale claims are translated into a deterministic domain conflict; purchase receipt/lot rows are unchanged.

- [ ] **Step 3: Make claim creation and replacement explicitly transactional**

Catch the unique active-claim constraint and raise `FinanceSourceConflict("所选发货明细已被另一张开票清单占用")`. Pending-item replacement first validates the complete new source set, then deletes/inserts inside one transaction. Voiding clears active claim keys according to existing semantics so a source can be selected again.

- [ ] **Step 4: Run claim, invoice lifecycle, and reconciliation regressions**

Run: `python -m unittest tests.test_finance_source_claims tests.test_procurement_finance_isolation tests.test_finance tests.test_customer_billing -v`

Expected: PASS; issue/pay/reopen/void/delete and reconciliation behavior remain unchanged.

- [ ] **Step 5: Commit finance invariants**

```bash
git add app.py tests/test_finance_source_claims.py tests/test_procurement_finance_isolation.py
git commit -m "fix: protect shipped invoice source claims"
```

### Task 4: End-to-end finance acceptance and full verification

**Files:**
- Modify: `templates/base.html`
- Modify: `docs/superpowers/plans/2026-09-09-unified-procurement-phase-4-finance.md`

**Interfaces:**
- Consumes: all four procurement phases and existing sales finance lifecycle
- Produces: a deployable, regression-tested unified procurement release

- [ ] **Step 1: Run complete automated suites**

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.js`

Expected: all tests PASS.

- [ ] **Step 2: Run the migration report against a copied current database**

Run: `python scripts/report_procurement_migration.py --database /private/tmp/jiede-procurement-final-rehearsal.db`

Expected: source coverage and quantity/amount invariants pass; existing foreign-key findings are reported separately from new findings.

- [ ] **Step 3: Perform real-browser end-to-end acceptance**

Create/select a customer shipment, filter it by customer/date/order/delivery number, generate an invoice list, confirm totals, reject a concurrent duplicate selection, void the invoice, and select the released line again. Confirm missing-price and zero-quantity lines are handled as specified and purchase invoice statuses never change.

- [ ] **Step 4: Perform cross-module smoke acceptance**

Verify unified navigation, customer pages, suppliers, all purchase categories, purchase documents, partial arrival, purchase inventory, internal purchase outbound, product inventory, existing customer shipment, delivery notes, finance, and reconciliation.

- [ ] **Step 5: Record evidence and commit**

```bash
git add templates/base.html docs/superpowers/plans/2026-09-09-unified-procurement-phase-4-finance.md
git commit -m "test: verify unified procurement release"
```
