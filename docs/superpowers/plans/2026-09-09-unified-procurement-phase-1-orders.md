# Unified Procurement Phase 1: Vendors and Purchase Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build unified vendor management and four-category purchase orders with editable delivery snapshots and landscape-A4 Excel/PDF exports.

**Architecture:** Add a focused `procurement.py` domain module for schema, validation, order persistence, legacy vendor normalization, and document view models. Keep Flask routing in `app.py` and render category-specific fields through shared templates. Old purchase tables remain intact while idempotent migration copies them into the unified model with source markers.

**Tech Stack:** Python 3, Flask, SQLite, Jinja, openpyxl 3.1.5, ReportLab 4.2.2, unittest

**Spec:** `docs/superpowers/specs/2026-09-09-unified-procurement-vendor-inventory-design.md`

## Global Constraints

- Purchase categories are exactly `raw_material`, `carton`, `outsourcing`, and `other`.
- External-processing items never link to `manuals` and never change product inventory.
- Money is stored as integer minor units; the database uses `CNY` and the UI labels it `RMB／人民币`.
- Historical source tables are not deleted or rewritten.
- Supplier and delivery details are snapshotted on every purchase order.
- Excel and PDF use landscape A4 and obey purchase-price visibility permissions.
- Deployment and formal-data migration require a separate explicit instruction.

---

### Task 1: Procurement schema and value validation

**Files:**
- Create: `procurement.py`
- Modify: `app.py:542-588`
- Test: `tests/test_procurement_schema.py`

**Interfaces:**
- Produces: `ensure_procurement_tables(conn) -> None`
- Produces: `parse_purchase_category(value: str) -> str`
- Produces: `parse_purchase_quantity(value: object) -> int`
- Produces: `parse_optional_money_minor(value: str, currency: str = "CNY") -> int | None`
- Produces: `next_purchase_order_no(conn, purchased_at: str) -> str`

- [ ] **Step 1: Write failing schema and parser tests**

```python
def test_schema_is_idempotent_and_order_number_is_unique(self):
    procurement.ensure_procurement_tables(self.conn)
    procurement.ensure_procurement_tables(self.conn)
    self.assertEqual(
        procurement.next_purchase_order_no(self.conn, "2026-09-09"),
        "PO-20260909-0001",
    )

def test_purchase_values_are_strict(self):
    self.assertEqual(procurement.parse_purchase_quantity("12"), 12)
    self.assertEqual(procurement.parse_optional_money_minor("18.50"), 1850)
    self.assertIsNone(procurement.parse_optional_money_minor(""))
    for value in ("0", "-1", "1.2", "1e2", True):
        with self.subTest(value=value), self.assertRaises(ValueError):
            procurement.parse_purchase_quantity(value)
```

- [ ] **Step 2: Run the focused tests and confirm the module is missing**

Run: `python -m unittest tests.test_procurement_schema -v`

Expected: FAIL because `procurement` or its functions do not exist.

- [ ] **Step 3: Implement tables, constraints, indexes, and strict parsers**

```python
PURCHASE_CATEGORIES = {"raw_material", "carton", "outsourcing", "other"}
PURCHASE_STATUSES = {"draft", "ordered", "partially_received", "received", "cancelled"}

def parse_purchase_quantity(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value or "")):
        raise ValueError("数量必须为正整数")
    quantity = int(value)
    if quantity > 2_147_483_647:
        raise ValueError("数量超出允许范围")
    return quantity

def parse_optional_money_minor(value, currency="CNY"):
    text = str(value or "").strip()
    return None if not text else parse_money_minor(text, currency)
```

Create `suppliers`, `supplier_legacy_links`, `purchase_delivery_profiles`, `purchase_orders`, and `purchase_order_items` exactly as specified, including category/status checks, unique order numbers, unique `(legacy_source, legacy_id)` links, supplier/order indexes, and foreign keys. One supplier may own many legacy links. Wire `ensure_procurement_tables(conn)` into `init_db()` after the existing customer/common-info tables.

- [ ] **Step 4: Run schema tests and database integrity checks**

Run: `python -m unittest tests.test_procurement_schema -v`

Expected: PASS, including two consecutive `init_db()` calls and `PRAGMA foreign_key_check` returning no new rows.

- [ ] **Step 5: Commit the schema foundation**

```bash
git add procurement.py app.py tests/test_procurement_schema.py
git commit -m "feat: add unified procurement schema"
```

### Task 2: Procurement permissions and account management

**Files:**
- Modify: `app.py:1176-1228, 2110-2180, 10063-10270`
- Modify: `templates/users.html`
- Modify: `templates/base.html`
- Test: `tests/test_procurement_permissions.py`

**Interfaces:**
- Consumes: `ensure_procurement_tables(conn) -> None`
- Produces: permission keys `purchase_view`, `purchase_manage`, `purchase_receipt`, `purchase_inventory_view`, `purchase_inventory_adjust`, `purchase_inventory_outbound`, `purchase_price_view`, `supplier_manage`
- Produces: `user_can_view_purchase_prices() -> bool`

- [ ] **Step 1: Add failing permission tests**

```python
def test_purchase_view_does_not_grant_purchase_price(self):
    self.login("buyer")
    self.assertEqual(self.client.get("/admin/purchases/raw-material").status_code, 200)
    self.assertNotIn("单价", self.client.get("/admin/purchases/raw-material").get_data(as_text=True))

def test_supplier_mutation_requires_supplier_manage(self):
    self.login("buyer")
    response = self.client.post("/admin/business-partners/suppliers", data={"name": "供应商A"})
    self.assertEqual(response.status_code, 302)
    self.assertEqual(self.supplier_count(), 0)
```

- [ ] **Step 2: Run tests and confirm missing permission behavior**

Run: `python -m unittest tests.test_procurement_permissions -v`

Expected: FAIL because the columns, permission mappings, and routes are absent.

- [ ] **Step 3: Add user columns and server-side permission mapping**

```python
PROCUREMENT_PERMISSION_COLUMNS = {
    "can_view_purchases": "purchase_view",
    "can_manage_purchases": "purchase_manage",
    "can_receive_purchases": "purchase_receipt",
    "can_view_purchase_inventory": "purchase_inventory_view",
    "can_adjust_purchase_inventory": "purchase_inventory_adjust",
    "can_outbound_purchase_inventory": "purchase_inventory_outbound",
    "can_view_purchase_prices": "purchase_price_view",
    "can_manage_suppliers": "supplier_manage",
}
```

Map existing permissions conservatively: old purchase/carton access grants purchase view/manage; warehouse inventory grants receipt/inventory view but not adjustment/outbound unless the account already manages inventory; existing price or finance access grants purchase-price view. Administrators always pass all checks. Update create/edit account SQL and checkbox forms atomically.

- [ ] **Step 4: Run permission and existing account tests**

Run: `python -m unittest tests.test_procurement_permissions tests.test_finance.FinancePermissionTests -v`

Expected: PASS with no accidental price disclosure.

- [ ] **Step 5: Commit permission changes**

```bash
git add app.py templates/users.html templates/base.html tests/test_procurement_permissions.py
git commit -m "feat: add procurement permissions"
```

### Task 3: Unified supplier and delivery-profile management

**Files:**
- Modify: `procurement.py`
- Modify: `app.py`
- Create: `templates/business_partner_tabs.html`
- Create: `templates/suppliers.html`
- Create: `templates/purchase_delivery_profiles.html`
- Modify: `templates/customers.html`
- Modify: `templates/common_info.html`
- Modify: `templates/base.html`
- Test: `tests/test_suppliers.py`

**Interfaces:**
- Produces: `normalize_supplier_payload(form) -> dict[str, object]`
- Produces: `supplier_snapshot(row) -> dict[str, str]`
- Produces routes `/admin/business-partners/customers`, `/suppliers`, `/purchase-delivery-profiles`

- [ ] **Step 1: Write failing CRUD, uniqueness, snapshot, and delete-protection tests**

```python
def test_used_supplier_is_deactivated_not_deleted(self):
    supplier_id = self.create_supplier("供方A")
    self.create_purchase_order(supplier_id)
    response = self.client.post(f"/admin/business-partners/suppliers/{supplier_id}/delete")
    self.assertEqual(response.status_code, 302)
    with app.get_db() as conn:
        self.assertEqual(conn.execute("SELECT active FROM suppliers WHERE id=?", (supplier_id,)).fetchone()[0], 0)

def test_only_one_delivery_profile_is_default(self):
    first = self.create_profile("一厂", default=True)
    second = self.create_profile("二厂", default=True)
    self.assertEqual(self.default_profile_ids(), [second])
```

- [ ] **Step 2: Run the supplier tests and confirm route failures**

Run: `python -m unittest tests.test_suppliers -v`

Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement normalized supplier/profile services and routes**

```python
def supplier_snapshot(row):
    return {
        "supplier_code": row["code"],
        "supplier_name": row["name"],
        "supplier_contact": row["contact"],
        "supplier_phone": row["phone"],
        "supplier_email": row["email"],
        "supplier_address": row["address"],
    }
```

Generate supplier codes as `SUP-00001`, reject blank/duplicate names, normalize email/phone as display text without silently altering them, and make default-profile changes transactional. Preserve `/admin/customers` and `/admin/common-info` as compatibility redirects so saved links continue working.

- [ ] **Step 4: Build the three-tab business-partner UI and run tests**

Run: `python -m unittest tests.test_suppliers tests.test_customer_billing -v`

Expected: PASS; existing customer UI and billing behavior remain unchanged.

- [ ] **Step 5: Commit business-partner management**

```bash
git add procurement.py app.py templates/business_partner_tabs.html templates/suppliers.html templates/purchase_delivery_profiles.html templates/customers.html templates/common_info.html templates/base.html tests/test_suppliers.py
git commit -m "feat: unify customer and supplier management"
```

### Task 4: Purchase-order domain and four category pages

**Files:**
- Modify: `procurement.py`
- Modify: `app.py`
- Create: `templates/purchase_tabs.html`
- Create: `templates/purchase_orders.html`
- Create: `templates/purchase_order_form.html`
- Create: `templates/purchase_order_detail.html`
- Create: `static/purchase-orders.js`
- Create: `static/purchase-orders.css`
- Test: `tests/test_purchase_orders.py`
- Test: `tests/js/purchase-orders.test.js`

**Interfaces:**
- Produces: `normalize_purchase_order_payload(payload, *, can_view_prices: bool) -> dict`
- Produces: `create_purchase_order(conn, payload, actor, now) -> int`
- Produces: `update_purchase_order(conn, order_id, payload, actor, now) -> None`
- Produces: `load_purchase_order(conn, order_id) -> tuple[row, list[row]]`
- Produces: `purchase_order_filters_from_request() -> dict[str, object]`
- Produces: `fetch_purchase_orders(conn, category: str, filters: dict) -> list[row]`

- [ ] **Step 1: Write failing domain tests for all categories and snapshots**

```python
def test_carton_order_calculates_minor_unit_totals(self):
    payload = self.payload("carton", rows=[{
        "item_name": "五层纸箱", "material": "AB", "length": "400",
        "width": "300", "height": "200", "quantity": "12",
        "unit_price": "3.25", "expected_at": "2026-09-20",
    }])
    order_id = procurement.create_purchase_order(self.conn, payload, "buyer", self.now)
    item = self.conn.execute("SELECT * FROM purchase_order_items WHERE purchase_order_id=?", (order_id,)).fetchone()
    self.assertEqual((item["unit_price_minor"], item["line_total_minor"]), (325, 3900))

def test_supplier_edit_does_not_change_order_snapshot(self):
    order_id = self.create_order_with_supplier("旧地址")
    self.rename_supplier_address("新地址")
    order, _ = procurement.load_purchase_order(self.conn, order_id)
    self.assertEqual(order["supplier_address"], "旧地址")
```

- [ ] **Step 2: Run domain tests and confirm missing implementation**

Run: `python -m unittest tests.test_purchase_orders -v`

Expected: FAIL because order services are absent.

- [ ] **Step 3: Implement validation and transactional CRUD**

```python
CATEGORY_VISIBLE_FIELDS = {
    "raw_material": ("material", "length", "width", "thickness", "surface"),
    "carton": ("material", "length", "width", "height", "unit_price"),
    "outsourcing": ("item_name", "drawing_no", "material", "dimension_text", "thickness", "surface", "unit_price"),
    "other": ("item_name", "spec", "material", "dimension_text", "thickness", "surface", "unit_price"),
}
```

Require supplier/date/at least one row, preserve explicit blank optional fields, calculate line totals server-side, and refuse quantities below already received totals. Saving an `ordered` order captures current supplier and delivery-profile values. When a user lacks price permission, ignore posted price fields and preserve existing stored prices during edits. Use a transaction for header plus all lines.

- [ ] **Step 4: Write failing route and JavaScript tests**

```javascript
test('category switch preserves entered common row values', () => {
  const state = normalizeRows([{ item_name: '螺栓', quantity: '10' }], 'other');
  assert.equal(state[0].item_name, '螺栓');
  assert.equal(state[0].quantity, '10');
});
```

Run: `node --test tests/js/purchase-orders.test.js && python -m unittest tests.test_purchase_orders.PurchaseOrderRouteTests -v`

Expected: FAIL until pages and client module exist.

- [ ] **Step 5: Implement shared order pages and category adapters**

Routes:

```python
@app.route("/admin/purchases/<category>")
@permission_required("purchase_view")
def purchase_orders(category):
    category = parse_purchase_category(category.replace("-", "_"))
    filters = purchase_order_filters_from_request()
    with get_db() as conn:
        orders = fetch_purchase_orders(conn, category, filters)
    return render_template("purchase_orders.html", category=category, orders=orders, **filters)

@app.route("/admin/purchases/<category>/new", methods=["GET", "POST"])
@permission_required("purchase_manage")
def new_purchase_order(category):
    category = parse_purchase_category(category.replace("-", "_"))
    if request.method == "POST":
        payload = normalize_purchase_order_payload(request.form, can_view_prices=user_can_view_purchase_prices())
        with get_db() as conn:
            order_id = create_purchase_order(
                conn, payload, current_admin_username(),
                datetime.utcnow().isoformat(timespec="seconds"),
            )
        return redirect(url_for("purchase_order_detail", order_id=order_id))
    return render_template("purchase_order_form.html", category=category)
```

Add edit/detail/cancel routes, filters for supplier/order/item/date/status, dynamic row addition/removal/reordering, and server-rendered category labels. Never rely on hidden client fields for category or totals.

- [ ] **Step 6: Run domain, route, and JavaScript tests**

Run: `python -m unittest tests.test_purchase_orders -v && node --test tests/js/purchase-orders.test.js`

Expected: PASS for four categories, permission gates, invalid IDs, snapshot stability, and multi-row atomicity.

- [ ] **Step 7: Commit purchase-order workflow**

```bash
git add procurement.py app.py templates/purchase_tabs.html templates/purchase_orders.html templates/purchase_order_form.html templates/purchase_order_detail.html static/purchase-orders.js static/purchase-orders.css tests/test_purchase_orders.py tests/js/purchase-orders.test.js
git commit -m "feat: add unified purchase orders"
```

### Task 5: Landscape-A4 Excel and PDF purchase orders

**Files:**
- Create: `procurement_documents.py`
- Modify: `app.py`
- Modify: `templates/purchase_order_detail.html`
- Test: `tests/test_purchase_order_exports.py`

**Interfaces:**
- Consumes: `load_purchase_order(conn, order_id)`
- Produces: `build_purchase_order_workbook(order, items, *, include_prices: bool) -> BytesIO`
- Produces: `build_purchase_order_pdf(order, items, *, include_prices: bool) -> BytesIO`

- [ ] **Step 1: Write failing workbook and PDF contract tests**

```python
def test_workbook_is_landscape_a4_and_contains_delivery_snapshot(self):
    stream = procurement_documents.build_purchase_order_workbook(self.order, self.items, include_prices=True)
    sheet = load_workbook(stream)["采购订单"]
    self.assertEqual(sheet.page_setup.orientation, "landscape")
    self.assertEqual(sheet.page_setup.paperSize, sheet.PAPERSIZE_A4)
    self.assertEqual(sheet.sheet_properties.pageSetUpPr.fitToPage, True)
    self.assertIn("宁波市余姚市", " ".join(str(cell.value or "") for row in sheet for cell in row))

def test_price_hidden_export_omits_price_headers_and_values(self):
    workbook = load_workbook(procurement_documents.build_purchase_order_workbook(self.order, self.items, include_prices=False))
    values = [cell.value for row in workbook.active for cell in row]
    self.assertNotIn("单价", values)
    self.assertNotIn("合计", values)
```

- [ ] **Step 2: Run export tests and confirm missing builders**

Run: `python -m unittest tests.test_purchase_order_exports -v`

Expected: FAIL because `procurement_documents.py` is absent.

- [ ] **Step 3: Implement a shared document view model and both renderers**

```python
def purchase_document_columns(category, include_prices):
    columns = CATEGORY_DOCUMENT_COLUMNS[category]
    if not include_prices:
        columns = [column for column in columns if column.key not in {"unit_price", "line_total"}]
    return columns
```

Match the approved layout: centered title/order number, supplier block, category-specific detail table, delivery address/recipient/phone, standard remarks, and company footer. Excel sets print area, fit-to-one-page-wide, repeating rows, borders, Chinese fonts, and numeric formats. PDF uses registered CJK font and repeated table headers.

- [ ] **Step 4: Add guarded export routes and run tests**

Routes return `attachment` only when `download=1`; otherwise PDF uses inline preview. Both routes derive `include_prices` from the logged-in user, not a query parameter.

Run: `python -m unittest tests.test_purchase_order_exports tests.test_procurement_permissions -v`

Expected: PASS; invalid/cancelled/missing orders have deterministic responses and no price leak.

- [ ] **Step 5: Commit document exports**

```bash
git add procurement_documents.py app.py templates/purchase_order_detail.html tests/test_purchase_order_exports.py
git commit -m "feat: export purchase orders"
```

### Task 6: Idempotent legacy vendor and order migration

**Files:**
- Modify: `procurement.py`
- Create: `scripts/report_procurement_migration.py`
- Test: `tests/test_procurement_migration.py`
- Modify: `templates/base.html`
- Modify: `templates/admin.html`
- Modify: `templates/dashboard.html`

**Interfaces:**
- Produces: `migrate_legacy_procurement(conn, now: str) -> dict[str, object]`
- Produces: `procurement_migration_report(conn) -> dict[str, object]`

- [ ] **Step 1: Write failing migration fixtures and reconciliation assertions**

```python
def test_legacy_migration_is_idempotent_and_preserves_sources(self):
    self.seed_carton_purchase(id=7, quantity=12, unit_price=3.25)
    self.seed_purchase_followup(id=8, quantity=4, purchased_at="")
    first = procurement.migrate_legacy_procurement(self.conn, self.now)
    second = procurement.migrate_legacy_procurement(self.conn, self.now)
    self.assertEqual(first["orders_created"], 2)
    self.assertEqual(second["orders_created"], 0)
    self.assertEqual(self.count_orders(), 2)
    self.assertSourceExists("carton_purchases", 7)
    self.assertSourceExists("purchase_followups", 8)
```

- [ ] **Step 2: Run migration tests and confirm failure**

Run: `python -m unittest tests.test_procurement_migration -v`

Expected: FAIL because migration functions do not exist.

- [ ] **Step 3: Implement deterministic supplier merge and order backfill**

Use exact `(legacy_source, legacy_id)` uniqueness for retries. Trim supplier names only and attach every old source row through `supplier_legacy_links`. Merge non-conflicting non-empty fields; store conflicts in `procurement_migration_conflicts` with source/value pairs. Link blank historical supplier names to one inactive `LEGACY-UNKNOWN` / “历史未指定供应商” record and count those links in the report. Convert cartons to `carton`; convert purchase followups to `other`, preserving images through a legacy attachment reference. A missing purchase date yields `draft`; a present date yields `ordered`. Do not delete old rows.

- [ ] **Step 4: Add read-only migration report command**

```python
report = procurement.procurement_migration_report(conn)
print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
```

Report old/new row counts, quantity sums, amount sums, source coverage, vendor conflicts, duplicate sources, and `PRAGMA foreign_key_check` output. It must not mutate data.

- [ ] **Step 5: Replace top-level navigation with unified entries and compatibility redirects**

Make “采购” and “客商管理” active across their child routes. Redirect old purchase/carton/arrival entry URLs to the matching new tab without deleting the old tables or silently posting legacy forms.

- [ ] **Step 6: Run phase-one regression and inspect migrations twice**

Run: `python -m unittest tests.test_procurement_schema tests.test_procurement_permissions tests.test_suppliers tests.test_purchase_orders tests.test_purchase_order_exports tests.test_procurement_migration -v`

Run: `python scripts/report_procurement_migration.py --database data/manuals.db`

Expected: tests PASS; the report is read-only, reports every legacy source row once, and the second isolated migration creates zero additional rows.

- [ ] **Step 7: Commit migration and navigation**

```bash
git add procurement.py scripts/report_procurement_migration.py tests/test_procurement_migration.py templates/base.html templates/admin.html templates/dashboard.html
git commit -m "feat: migrate legacy purchase orders"
```

### Task 7: Phase-one full verification

**Files:**
- Modify: `docs/superpowers/plans/2026-09-09-unified-procurement-phase-1-orders.md`

**Interfaces:**
- Consumes: all phase-one routes, migrations, exports, and permissions
- Produces: a reviewed, testable phase-one baseline for phase two

- [ ] **Step 1: Run all Python and JavaScript tests**

Run: `python -m unittest discover -s tests -v`

Run: `node --test tests/js/*.test.js`

Expected: all tests PASS.

- [ ] **Step 2: Run database integrity and migration rehearsal on a copied database**

Run: `python scripts/report_procurement_migration.py --database /private/tmp/jiede-procurement-rehearsal.db`

Expected: no new foreign-key errors; totals and source coverage match the copied legacy database.

- [ ] **Step 3: Perform local browser acceptance**

Verify supplier CRUD, delivery templates, all four order categories, multiple rows, filters, permissions, Excel print settings, PDF pagination, legacy redirects, and unchanged customer pages. Confirm no purchase order changes product inventory.

- [ ] **Step 4: Record verification evidence and commit only if the plan checkbox log changed**

```bash
git add docs/superpowers/plans/2026-09-09-unified-procurement-phase-1-orders.md
git commit -m "docs: record procurement phase one verification"
```
