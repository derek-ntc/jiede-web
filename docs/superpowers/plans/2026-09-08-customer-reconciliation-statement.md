# Customer Reconciliation Statement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build immutable customer reconciliation statements from selected ordinary and assembly shipment lines, with 13% tax back-calculation, A4 landscape Excel export, temporary password access, customer signature, and dispute submission.

**Architecture:** Keep database access and Flask routes in the existing `app.py` application, but place exact tax calculations and workbook construction in a focused `reconciliation.py` module. Reconciliation records snapshot shipment, customer, supplier, and price data at creation time; public access is guarded by a hashed random token, hashed six-digit password, 30-day expiry, lockout, and a versioned session. Existing finance invoices remain independent.

**Tech Stack:** Python 3, Flask, SQLite, Werkzeug password hashing, `Decimal`, OpenPyXL, Pillow, Jinja2, existing unittest suite, and Node-based browser smoke tests where available.

**Spec:** `docs/superpowers/specs/2026-09-08-customer-reconciliation-statement-design.md`

## Global Constraints

- One statement contains exactly one customer and one currency; default currency is `CNY` and the UI may label it RMB.
- Shipment unit prices are tax-inclusive; tax-exclusive values are derived using exactly 13% VAT.
- Ordinary shipments and individual assembly shipment items may be mixed on one statement when customer and currency match.
- A source may belong to at most one non-void reconciliation statement; voiding releases it for a replacement statement.
- Statements are immutable snapshots; customer, product, shipment, price, and company-profile edits must not rewrite history.
- Public access uses a cryptographically random token, a random six-digit password, 30-day expiry, five-failure/15-minute lockout, and password-version session invalidation.
- Customer confirmation and dispute are mutually exclusive terminal customer actions; confirmed signatures cannot be changed.
- Public access remains readable only until the original 30-day expiry; backend history and export remain available afterward.
- Excel output is `.xlsx`, A4 landscape, one page wide, with repeated table headers and dynamic vertical pagination.
- Backend reconciliation data is available only to administrators or users with `finance_manage`; company-profile editing is administrator-only.
- Do not couple reconciliation status to invoice, payment, shipment-signature, inventory, or order status.
- Preserve unrelated dirty-worktree changes. If shared files already contain unrelated edits, never stage or commit those edits as part of this feature.

## File Structure

- Create `reconciliation.py`: exact tax calculations, display conversion helpers, safe workbook population, dynamic A4-landscape workbook generation, and optional signature embedding.
- Modify `app.py`: upload directory constant, idempotent reconciliation schema, source snapshot queries, lifecycle transactions, access security, mutation guards, admin routes, public routes, and template context.
- Create `templates/reconciliation_statements.html`: searchable backend statement list.
- Create `templates/reconciliation_statement_detail.html`: immutable snapshot detail, credentials reveal, export, reset, and void controls.
- Create `templates/reconciliation_company_profile.html`: singleton supplier profile form.
- Create `templates/reconciliation_public.html`: password gate plus read-only/confirm/dispute customer experience.
- Create `static/reconciliation.css`: compact SAP-style backend table, public responsive table, credential panel, and signature/dispute states.
- Create `static/reconciliation.js`: copy controls, signature canvas, mutually exclusive customer actions, and submission validation.
- Modify `templates/shipped_orders.html`: assembly-item selectors and finance-only “生成对账单” action without disturbing the delivery-note PDF form.
- Modify `templates/base.html`: include reconciliation routes in the existing finance navigation active state.
- Modify `templates/admin.html`: administrator link to “我方对账资料设置”.
- Create `tests/test_reconciliation.py`: migrations, calculations, source validation, snapshot lifecycle, permissions, public security, signing/dispute, and workbook tests.
- Modify relevant existing shipment/finance tests only when the new reconciliation mutation guard intentionally changes an asserted behavior.

---

### Task 1: Exact Amount Calculations and Reconciliation Schema

**Files:**
- Create: `reconciliation.py`
- Modify: `app.py:99-125` (constants), `app.py:494-525` (`init_db`), and the schema helpers near `app.py:660-930`
- Create: `tests/test_reconciliation.py`

**Interfaces:**
- Consumes: `pricing.SUPPORTED_CURRENCIES`, SQLite connections returned by `app.get_db()`.
- Produces: `TAX_RATE_PPM = 130000`, `EX_TAX_UNIT_SCALE = 1_000_000`, `calculate_reconciliation_amounts(unit_price_minor: int, quantity: int, currency: str) -> dict[str, int]`, and `app.ensure_reconciliation_tables(conn) -> None`.

- [ ] **Step 1: Write failing calculation and migration tests**

Add tests that pin exact half-up rounding and all required tables/constraints:

```python
from reconciliation import calculate_reconciliation_amounts


def test_13_percent_tax_is_back_calculated_from_tax_inclusive_price():
    amounts = calculate_reconciliation_amounts(11300, 2, "CNY")
    assert amounts == {
        "unit_price_incl_tax_minor": 11300,
        "unit_price_ex_tax_scaled": 100_000_000,
        "amount_incl_tax_minor": 22600,
        "amount_ex_tax_minor": 20000,
        "tax_amount_minor": 2600,
    }


def test_reconciliation_schema_is_idempotent_and_claim_is_unique():
    app.init_db()
    app.init_db()
    with app.get_db() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "reconciliation_company_profile",
            "reconciliation_statements",
            "reconciliation_statement_items",
        } <= names
```

Also cover zero price, JPY precision, invalid currency, non-positive quantity, negative price, and overflow beyond `2**63 - 1`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationCalculationTests tests.test_reconciliation.ReconciliationMigrationTests -v`

Expected: FAIL because `reconciliation.py` and `ensure_reconciliation_tables` do not exist.

- [ ] **Step 3: Implement exact calculation helpers**

Create `reconciliation.py` with `Decimal`, `ROUND_HALF_UP`, and these rules:

```python
TAX_RATE_PPM = 130_000
TAX_DIVISOR = Decimal("1.13")
EX_TAX_UNIT_SCALE = 1_000_000


def calculate_reconciliation_amounts(unit_price_minor, quantity, currency):
    currency = normalize_currency(currency)
    unit_price_minor = int(unit_price_minor)
    quantity = int(quantity)
    if unit_price_minor < 0 or quantity <= 0:
        raise ValueError("对账价格和数量必须有效")
    amount_incl_tax_minor = unit_price_minor * quantity
    if amount_incl_tax_minor > SQLITE_INTEGER_MAX:
        raise ValueError("对账金额过大")
    digits = SUPPORTED_CURRENCIES[currency]
    unit_major = Decimal(unit_price_minor).scaleb(-digits)
    unit_price_ex_tax_scaled = int(
        (unit_major / TAX_DIVISOR * EX_TAX_UNIT_SCALE)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    amount_ex_tax_minor = int(
        (Decimal(amount_incl_tax_minor) / TAX_DIVISOR)
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return {
        "unit_price_incl_tax_minor": unit_price_minor,
        "unit_price_ex_tax_scaled": unit_price_ex_tax_scaled,
        "amount_incl_tax_minor": amount_incl_tax_minor,
        "amount_ex_tax_minor": amount_ex_tax_minor,
        "tax_amount_minor": amount_incl_tax_minor - amount_ex_tax_minor,
    }
```

Keep conversion-to-display helpers in the same module so HTML and Excel use the same `unit_price_ex_tax_scaled` interpretation.

- [ ] **Step 4: Add the idempotent schema**

Add `RECONCILIATION_SIGNATURES_DIR = UPLOADS_DIR / "reconciliation-signatures"`, create it in `init_db`, call `ensure_reconciliation_tables(conn)` after finance tables, and create:

```sql
CREATE TABLE IF NOT EXISTS reconciliation_company_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    company_name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_no TEXT NOT NULL UNIQUE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    customer_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    customer_address TEXT NOT NULL DEFAULT '',
    customer_phone TEXT NOT NULL DEFAULT '',
    customer_email TEXT NOT NULL DEFAULT '',
    customer_purchase_contact TEXT NOT NULL DEFAULT '',
    customer_reconciliation_contact TEXT NOT NULL DEFAULT '',
    customer_invoice_title TEXT NOT NULL DEFAULT '',
    customer_tax_id TEXT NOT NULL DEFAULT '',
    customer_registered_address TEXT NOT NULL DEFAULT '',
    customer_registered_phone TEXT NOT NULL DEFAULT '',
    customer_bank_name TEXT NOT NULL DEFAULT '',
    customer_bank_account TEXT NOT NULL DEFAULT '',
    customer_invoice_email TEXT NOT NULL DEFAULT '',
    supplier_company_name TEXT NOT NULL,
    supplier_address TEXT NOT NULL DEFAULT '',
    supplier_contact TEXT NOT NULL DEFAULT '',
    supplier_phone TEXT NOT NULL DEFAULT '',
    supplier_email TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL,
    tax_rate_ppm INTEGER NOT NULL,
    amount_ex_tax_minor INTEGER NOT NULL,
    amount_incl_tax_minor INTEGER NOT NULL,
    tax_amount_minor INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    remark TEXT NOT NULL DEFAULT '',
    token_digest TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_version INTEGER NOT NULL DEFAULT 1,
    access_expires_at TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT NOT NULL DEFAULT '',
    confirmed_name TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT '',
    signature_image TEXT NOT NULL DEFAULT '',
    confirmed_ip TEXT NOT NULL DEFAULT '',
    confirmed_user_agent TEXT NOT NULL DEFAULT '',
    dispute_name TEXT NOT NULL DEFAULT '',
    dispute_content TEXT NOT NULL DEFAULT '',
    disputed_at TEXT NOT NULL DEFAULT '',
    disputed_ip TEXT NOT NULL DEFAULT '',
    disputed_user_agent TEXT NOT NULL DEFAULT '',
    void_reason TEXT NOT NULL DEFAULT '',
    voided_by TEXT NOT NULL DEFAULT '',
    voided_at TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id),
    CHECK (status IN ('pending', 'confirmed', 'disputed', 'void'))
);

CREATE TABLE IF NOT EXISTS reconciliation_statement_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    active_claim_key TEXT UNIQUE,
    shipped_at TEXT NOT NULL,
    delivery_no TEXT NOT NULL DEFAULT '',
    order_no TEXT NOT NULL DEFAULT '',
    assembly_batch_id INTEGER,
    assembly_drawing_no TEXT NOT NULL DEFAULT '',
    manual_id INTEGER,
    sku TEXT NOT NULL DEFAULT '',
    drawing_no TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL,
    currency TEXT NOT NULL,
    unit_price_incl_tax_minor INTEGER NOT NULL,
    unit_price_ex_tax_scaled INTEGER NOT NULL,
    amount_incl_tax_minor INTEGER NOT NULL,
    amount_ex_tax_minor INTEGER NOT NULL,
    tax_amount_minor INTEGER NOT NULL,
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (statement_id) REFERENCES reconciliation_statements(id) ON DELETE CASCADE,
    CHECK (source_type IN ('ordinary', 'assembly_item'))
);
```

Seed singleton ID 1 with the confirmed company name and blank contact fields. Add indexes on statement customer/date/status and item statement/source.

- [ ] **Step 5: Run focused tests and commit the task checkpoint**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationCalculationTests tests.test_reconciliation.ReconciliationMigrationTests -v`

Expected: PASS.

Commit only if the shared worktree can be staged without unrelated changes:

```bash
git add reconciliation.py tests/test_reconciliation.py
git add -p app.py
git commit -m "feat: add reconciliation schema and tax calculations"
```

If an `app.py` hunk contains pre-existing user work, do not stage it; record the verified checkpoint without committing.

---

### Task 2: Source Normalization, Snapshot Creation, and Immutability

**Files:**
- Modify: `app.py:5740-6205` (finance-source neighborhood and mutation guards)
- Modify: `tests/test_reconciliation.py`

**Interfaces:**
- Consumes: `calculate_reconciliation_amounts`, existing source types `ordinary` and `assembly_item`, customers, and singleton company profile.
- Produces: `reconciliation_claim_key`, `parse_reconciliation_source_refs`, `_reconciliation_source_select`, `_load_reconciliation_sources`, `next_reconciliation_statement_no`, `create_reconciliation_statement`, `fetch_reconciliation_statement`, `fetch_reconciliation_statement_items`, `void_reconciliation_statement`, and `assert_reconciliation_sources_mutable`.

- [ ] **Step 1: Write failing source and creation tests**

Construct ordinary and assembly fixtures with `manuals.sku`, `manuals.unit`, product model, order numbers, allocations, and snapshot prices. Add tests equivalent to:

```python
def test_create_statement_snapshots_mixed_sources_and_exact_totals(self):
    with app.get_db() as conn:
        result = app.create_reconciliation_statement(
            conn,
            [
                ("ordinary", self.ordinary_a_cny),
                ("assembly_item", self.assembly_a_cny),
            ],
            "finance-user",
        )
        statement = app.fetch_reconciliation_statement(conn, result["statement_id"])
        items = app.fetch_reconciliation_statement_items(conn, result["statement_id"])
    self.assertEqual(statement["customer_name"], "客户A")
    self.assertEqual(statement["currency"], "CNY")
    self.assertEqual(len(items), 2)
    self.assertEqual(statement["amount_incl_tax_minor"], sum(row["amount_incl_tax_minor"] for row in items))
    self.assertRegex(result["token"], r"^[A-Za-z0-9_-]{40,}$")
    self.assertRegex(result["password"], r"^[0-9]{6}$")
```

Add negative tests for an empty list, duplicate reference, invalid type, missing source, mixed customer, mixed currency, missing price, unknown customer, and duplicate active claim. Modify source product/customer/profile data after creation and assert fetched snapshots do not change.

- [ ] **Step 2: Run creation tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationCreationTests -v`

Expected: FAIL because creation helpers are missing.

- [ ] **Step 3: Implement normalized source queries**

Implement strict reference syntax `ordinary:<positive-id>` or `assembly_item:<positive-id>`. Extend the existing finance-source SQL concept so each normalized row includes:

```python
{
    "source_type": "ordinary" | "assembly_item",
    "source_id": int,
    "customer_name": str,
    "order_no": str,
    "assembly_batch_id": int | None,
    "assembly_drawing_no": str,
    "shipped_at": "YYYY-MM-DD",
    "delivery_no": str,
    "manual_id": int | None,
    "sku": str,
    "drawing_no": str,
    "product_name": str,
    "model": str,
    "unit": str,
    "quantity": int,
    "unit_price_minor": int | None,
    "currency": str,
    "remark": str,
}
```

For ordinary shipments use a stable delivery number such as `FH-<shipment id>` when no explicit delivery number exists. For assembly items use `ZP-<batch id>` and concatenate distinct allocation order numbers in deterministic order, appending “无订单” if an allocation has no order.

- [ ] **Step 4: Implement atomic statement creation**

Within `BEGIN IMMEDIATE` plus a savepoint:

1. Normalize and de-duplicate references.
2. Re-read every source.
3. Enforce one normalized source customer name, then resolve that exact name to one customer record; do not trust a posted customer ID.
4. Normalize currencies and enforce exactly one currency.
5. Reject missing unit-price snapshots and occupied `active_claim_key` values.
6. Snapshot all customer fields and singleton supplier profile.
7. Derive `period_start`/`period_end` from min/max shipment dates.
8. Generate the monthly sequence `DZ-YYYYMM-NNNN`, a 32-byte URL-safe token, `sha256(token).hexdigest()`, a zero-padded six-digit password using `secrets.randbelow(1_000_000)`, Werkzeug password hash, and UTC expiry `now + timedelta(days=30)`.
9. Insert items with `active_claim_key = f"{source_type}:{source_id}"` and tax fields returned by `calculate_reconciliation_amounts`.
10. Sum rounded item amounts into the statement totals and return only:

```python
{
    "statement_id": int,
    "token": str,
    "password": str,
    "access_expires_at": str,
}
```

Never persist the raw token or plaintext password.

- [ ] **Step 5: Add voiding and source mutation guards**

`void_reconciliation_statement(conn, statement_id, reason, actor)` must require a non-empty reason, reject already-void records, set status/audit fields, and set every item `active_claim_key = NULL` in the same transaction.

`assert_reconciliation_sources_mutable(conn, refs)` must reject shipment date, quantity, price, or deletion changes while a source has an active claim. Call it alongside `assert_finance_sources_mutable` in:

- ordinary shipment edit, price backfill, and delete;
- assembly shipment edit/delete paths for all batch items;
- order/product deletion paths that already check finance references.

Keep logistics remarks and shipment images editable because they do not alter the statement snapshot.

- [ ] **Step 6: Run domain and regression tests and commit the checkpoint**

Run:

```bash
.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationCreationTests tests.test_reconciliation.ReconciliationLifecycleTests -v
.venv/bin/python -m unittest tests.test_finance tests.test_assembly_shipping -v
```

Expected: PASS, including a test that voiding releases the source and a test that an active reconciliation blocks quantity/price/deletion changes.

Commit only isolated hunks:

```bash
git add tests/test_reconciliation.py
git add -p app.py
git commit -m "feat: create immutable reconciliation snapshots"
```

---

### Task 3: Backend Management UI and Supplier Profile

**Files:**
- Modify: `app.py:8830-9140` (finance routes), `app.py:15000-15600` (shipped list route/context)
- Create: `templates/reconciliation_statements.html`
- Create: `templates/reconciliation_statement_detail.html`
- Create: `templates/reconciliation_company_profile.html`
- Modify: `templates/shipped_orders.html:40-180, 375-390`
- Modify: `templates/base.html:25-30`
- Modify: `templates/admin.html:10-30`
- Create: `static/reconciliation.css`
- Modify: `tests/test_reconciliation.py`

**Interfaces:**
- Consumes: lifecycle helpers from Task 2 and existing `permission_required("finance_manage")` / `admin_required` decorators.
- Produces: endpoints `reconciliation_statements`, `create_reconciliation_statement_route`, `reconciliation_statement_detail`, `reset_reconciliation_access`, `void_reconciliation_statement_route`, and `reconciliation_company_profile`.

- [ ] **Step 1: Write failing backend route and permission tests**

Add route tests for:

```python
response = self.client.post(
    "/admin/reconciliation-statements",
    data={
        "source_ref": [
            f"ordinary:{self.ordinary_a_cny}",
            f"assembly_item:{self.assembly_a_cny}",
        ],
    },
)
self.assertEqual(response.status_code, 302)
self.assertRegex(response.location, r"/admin/reconciliation-statements/[0-9]+$")
```

Verify an operator without `finance_manage` cannot list, create, detail, export, reset, or void. Verify a finance manager can do those operations but cannot edit `/admin/reconciliation-company-profile`; an admin can edit the profile. Verify cross-customer errors create zero rows.

- [ ] **Step 2: Run backend route tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationBackendRouteTests -v`

Expected: FAIL/404 because routes and templates do not exist.

- [ ] **Step 3: Implement list, create, detail, reset, void, and profile routes**

Use these URL contracts:

```text
GET  /admin/reconciliation-statements
POST /admin/reconciliation-statements
GET  /admin/reconciliation-statements/<statement_id>
POST /admin/reconciliation-statements/<statement_id>/reset-access
POST /admin/reconciliation-statements/<statement_id>/void
GET|POST /admin/reconciliation-company-profile
```

The list supports `statement_no`, `customer`, `status`, and `date` query parameters. Creation parses only repeated `source_ref`; it derives the customer from authoritative source rows and never trusts posted names, quantities, currencies, or totals.

After creation/reset, generate both a fresh token and password, store this short-lived payload in Flask session, and immediately redirect:

```python
session["reconciliation_credentials"] = {
    "statement_id": statement_id,
    "token": token,
    "password": password,
    "access_expires_at": expires_at,
}
```

The detail route pops it once, constructs the external link with `url_for(..., _external=True)`, and renders a warning that the link and password cannot be retrieved later. Later visits offer “重置访问凭证”; resetting replaces both the token digest and password hash, increments `password_version`, and invalidates the old link and session.

- [ ] **Step 4: Integrate selection into the shipped history without breaking PDF export**

Keep `shipped-export-form` unchanged for its ordinary `shipment_id` checkboxes. Add a separate `reconciliation-create-form` containing repeated `source_ref` fields populated by JavaScript from checkboxes marked `data-reconciliation-source`; the server derives the customer from the selected sources.

Render these only when `can_manage_finance`:

```html
<input type="checkbox"
       data-reconciliation-source
       data-customer="{{ customer_name }}"
       name="reconciliation_source_ref"
       value="assembly_item:{{ item.id }}">
```

Use `ordinary:<shipment.id>` for ordinary rows. The client may pre-check same-customer selection for faster feedback, but the server remains authoritative. Disable already-claimed items and show their statement number/status. Add “生成对账单” next to the existing export controls and route finance users to the new detail after creation.

- [ ] **Step 5: Build backend templates and styles**

Match current `erp-page`, `erp-query-panel`, black text, darker borders, compact controls, and responsive table conventions. The detail page must show:

- statement number, customer, period, currency, status, creator, and dates;
- customer and supplier snapshots;
- every ordinary/assembly line and source label;
- tax-exclusive, tax-inclusive, and tax totals;
- signature metadata or dispute content;
- one-time credentials panel, Excel download, reset, and void controls gated by status.

Add reconciliation endpoints to the existing finance navigation active-state expression. Add the supplier-profile card only for `current_user_role == "admin"`.

- [ ] **Step 6: Run backend and existing list-layout tests and commit the checkpoint**

Run:

```bash
.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationBackendRouteTests -v
.venv/bin/python -m unittest tests.test_business_list_layout tests.test_product_list_layout -v
```

Expected: PASS.

Commit only feature files and isolated hunks:

```bash
git add templates/reconciliation_statements.html templates/reconciliation_statement_detail.html templates/reconciliation_company_profile.html static/reconciliation.css tests/test_reconciliation.py
git add -p app.py templates/shipped_orders.html templates/base.html templates/admin.html
git commit -m "feat: add reconciliation management screens"
```

---

### Task 4: Password-Protected Customer Confirmation and Dispute Flow

**Files:**
- Modify: `app.py` near existing `/sign/<token>` routes at `app.py:14380-14440`
- Create: `templates/reconciliation_public.html`
- Create: `static/reconciliation.js`
- Modify: `static/reconciliation.css`
- Modify: `tests/test_reconciliation.py`

**Interfaces:**
- Consumes: hashed token/password fields, `decode_signature_image`, `client_ip`, Task 2 statement fetchers.
- Produces: `reconciliation_token_digest`, `find_reconciliation_by_token`, `verify_reconciliation_password`, `grant_reconciliation_session_access`, `has_reconciliation_session_access`, `confirm_reconciliation_statement`, `dispute_reconciliation_statement`, and public endpoint `GET|POST /reconciliation/<token>` named `public_reconciliation_statement`.

- [ ] **Step 1: Write failing public access security tests**

Cover:

```python
def test_public_statement_requires_password_then_grants_versioned_session(self):
    response = self.client.get(self.public_path)
    self.assertIn("请输入6位临时密码", response.get_data(as_text=True))
    response = self.client.post(self.public_path, data={"password": self.password})
    self.assertEqual(response.status_code, 302)
    response = self.client.get(self.public_path)
    self.assertIn("客户A", response.get_data(as_text=True))
```

Also assert invalid token returns 404 without revealing whether it ever existed, expired/void returns 410, five wrong attempts trigger a 15-minute lock, correct password clears failures, reset invalidates the old session, and the raw token/password are absent from database rows.

- [ ] **Step 2: Write failing terminal action tests**

Use a valid minimal PNG signature data URL and test:

- confirmation requires name, action nonce, and valid signature;
- confirmation saves a file under the reconciliation signature directory and records timestamp/IP/User-Agent;
- a second signature POST cannot overwrite the first file or metadata;
- dispute requires name and non-empty content;
- confirmation after dispute and dispute after confirmation are rejected;
- after either terminal action the page is read-only until expiry;
- reset/void invalidates the public session and void prevents further reads.

- [ ] **Step 3: Run public-flow tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationPublicAccessTests tests.test_reconciliation.ReconciliationCustomerActionTests -v`

Expected: FAIL because public helpers/routes are missing.

- [ ] **Step 4: Implement token lookup, lockout, versioned sessions, and action nonce**

Use `sha256(raw_token.encode("utf-8")).hexdigest()` for lookup. Perform password failure increments and lock calculation inside an immediate transaction. Store only one compact browser authorization:

```python
session["reconciliation_access"] = {
    "statement_id": statement_id,
    "password_version": password_version,
    "verified_until": access_expires_at,
}
```

After verification generate `session["reconciliation_action_nonce"] = secrets.token_urlsafe(24)`. Confirmation/dispute POSTs must compare a hidden nonce using `secrets.compare_digest`, consume it before state change, and rotate it when redisplaying the form.

- [ ] **Step 5: Implement immutable confirmation/dispute updates and protected signature delivery**

Use a conditional update:

```sql
UPDATE reconciliation_statements
SET status = 'confirmed', confirmed_name = ?, confirmed_at = ?,
    signature_image = ?, confirmed_ip = ?, confirmed_user_agent = ?, updated_at = ?
WHERE id = ? AND status = 'pending' AND access_expires_at > ?;
```

If `rowcount == 0`, remove the just-written signature file and render the current terminal state. Implement an equivalent `pending -> disputed` update. Do not reuse the ordinary shipment signature filename namespace.

Serve signature images only when the requester is an authorized backend user or has a valid session for that exact statement and password version. Apply `secure_filename`, `.png` suffix validation, record-to-file ownership validation, `X-Content-Type-Options: nosniff`, and no-store/private cache headers.

- [ ] **Step 6: Build the public template and JavaScript**

The same endpoint renders four explicit states: password, pending, confirmed, disputed/expired. Use `reconciliation.js` to:

- resize the canvas for device pixel ratio;
- support pointer/touch drawing;
- clear the signature;
- refuse empty signatures before submission;
- populate `signature_data` with PNG data URL;
- keep confirmation and dispute in separate forms;
- require an explicit confirmation prompt before either terminal action.

Do not include backend navigation on the public page. Use a responsive table wrapper; never hide amount columns on mobile.

- [ ] **Step 7: Run public tests and commit the checkpoint**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationPublicAccessTests tests.test_reconciliation.ReconciliationCustomerActionTests -v`

Expected: PASS.

Commit isolated changes:

```bash
git add templates/reconciliation_public.html static/reconciliation.js static/reconciliation.css tests/test_reconciliation.py
git add -p app.py
git commit -m "feat: add secure customer reconciliation approval"
```

---

### Task 5: Template-Compatible A4 Landscape Excel Export

**Files:**
- Modify: `reconciliation.py`
- Modify: `app.py` near export routes at `app.py:8985-9015`
- Modify: `tests/test_reconciliation.py`
- Generate for QA only: `/private/tmp/jiede-reconciliation-qa/sample-reconciliation.xlsx`

**Interfaces:**
- Consumes: statement/item dictionaries from Tasks 1-2 and optional authorized signature file path.
- Produces: `build_reconciliation_workbook(statement: Mapping, items: Sequence[Mapping], signature_path: Path | None = None) -> openpyxl.Workbook` and GET endpoint `/admin/reconciliation-statements/<statement_id>/export.xlsx`.

- [ ] **Step 1: Write failing workbook structure and safety tests**

Assert the generated workbook has:

```python
workbook = build_reconciliation_workbook(statement, items)
sheet = workbook.active
self.assertEqual(sheet.title, "对账单")
self.assertEqual(sheet.page_setup.orientation, "landscape")
self.assertEqual(sheet.page_setup.paperSize, sheet.PAPERSIZE_A4)
self.assertEqual(sheet.page_setup.fitToWidth, 1)
self.assertEqual(sheet.print_title_rows, "8:8")
self.assertEqual([sheet.cell(8, c).value for c in range(1, 15)], [
    "序号", "送货日期", "送货单号", "采购订单号", "物料编码", "物料名称",
    "规格型号", "单位", "送货数量", "不含税单价", "含税单价",
    "不含税金额", "含税金额", "备注",
])
```

Verify dynamic summary-row placement, numeric cell types for quantities/prices/amounts, formulas refer to the actual item range, print area ends after signature blocks, frozen/repeated headers, and formula-injection strings such as `=1+1`, `+CMD`, `-1+2`, and `@SUM(A1:A2)` are stored as literal strings. Confirm snapshot content appears and later product/customer changes do not.

- [ ] **Step 2: Run workbook tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationWorkbookTests -v`

Expected: FAIL because the workbook builder/export route does not exist.

- [ ] **Step 3: Implement the dynamic template layout**

Build one sheet with columns A:N and these stable regions:

- rows 1-2: merged title and statement metadata;
- rows 3-6: customer details on the left and supplier details on the right;
- row 8: repeated 14-column detail header;
- rows 9 through `8 + len(items)`: detail values;
- following rows: sales total, invoice/settlement summary placeholders labelled “未登记”, notes, preparer/reviewer, and both-party signature areas.

Use black Song/SimSun-compatible fonts, dark thin borders, centered headers, wrapped text, alternating light fill only when it improves scanning, and widths tuned for landscape A4. Use `set_excel_literal_value` or an equivalent local helper for every untrusted string. Set:

```python
sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
sheet.page_setup.fitToWidth = 1
sheet.page_setup.fitToHeight = 0
sheet.sheet_properties.pageSetUpPr.fitToPage = True
sheet.print_title_rows = "8:8"
sheet.freeze_panes = "A9"
sheet.sheet_properties.pageSetUpPr.autoPageBreaks = True
```

Write ex-tax unit price as `unit_price_ex_tax_scaled / 1_000_000`, price/amounts as real numeric values, and set six-decimal/fixed-currency formats. Add `SUM` formulas to the three monetary total cells and assert their referenced ranges match stored totals in tests. When a confirmed signature path exists, embed it in the customer signature area with a bounded size and also write signer/date text.

- [ ] **Step 4: Add the finance-only export route**

Fetch only snapshot rows, resolve the signature path through `secure_filename`, call `build_reconciliation_workbook`, save to `BytesIO`, and return:

```python
send_file(
    buffer,
    as_attachment=True,
    download_name=f"{statement['statement_no']}-{safe_customer_name}.xlsx",
    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
```

Do not query current product prices or recompute source rows during export.

- [ ] **Step 5: Run workbook tests and create a real QA artifact**

Immediately before the first command that writes the QA workbook, run the spreadsheet-operation marker exactly once as required by the spreadsheet skill. Then generate `/private/tmp/jiede-reconciliation-qa/sample-reconciliation.xlsx` from representative ordinary and assembly snapshot data, including a long product name and enough rows to test pagination.

Run:

```bash
.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationWorkbookTests -v
.venv/bin/python -m unittest tests.test_reconciliation.ReconciliationBackendRouteTests.test_finance_user_can_export_xlsx -v
```

Expected: PASS and the HTTP response starts with ZIP/XLSX bytes.

- [ ] **Step 6: Inspect, recalculate, and render the workbook**

Use the bundled spreadsheet runtime discovered by `load_workspace_dependencies` and artifact-tool inspection to check `对账单!A1:N<last-row>`, formula errors, numeric cell types, and page settings. Recalculate with LibreOffice if available, then render to PNG/PDF and visually verify:

- A4 landscape orientation;
- no clipped title, headers, totals, notes, or signature blocks;
- row headers repeat after a page break;
- long Chinese text wraps without covering adjacent cells;
- signature is contained in its assigned area;
- totals match the database snapshot values.

If any check fails, adjust widths/heights/print area and repeat inspection and rendering until clean.

- [ ] **Step 7: Commit the Excel checkpoint**

Do not add QA artifacts from `/private/tmp`. Commit only isolated code/test changes:

```bash
git add reconciliation.py tests/test_reconciliation.py
git add -p app.py
git commit -m "feat: export A4 customer reconciliation workbooks"
```

---

### Task 6: Full Regression, Browser Verification, and Handoff

**Files:**
- Modify if verification exposes defects: only the files listed in Tasks 1-5
- Review: `docs/superpowers/specs/2026-09-08-customer-reconciliation-statement-design.md`
- Review: `docs/superpowers/plans/2026-09-08-customer-reconciliation-statement.md`

**Interfaces:**
- Consumes: all prior task deliverables.
- Produces: verified end-to-end feature with no known spec gaps.

- [ ] **Step 1: Run the complete reconciliation and affected-domain suites**

Run:

```bash
.venv/bin/python -m unittest tests.test_reconciliation -v
.venv/bin/python -m unittest tests.test_finance tests.test_assembly_shipping tests.test_business_list_layout -v
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests PASS. If any test fails, diagnose the root cause before changing production code and rerun the smallest failing test first.

- [ ] **Step 2: Perform route and authorization smoke checks**

With a temporary test database or local development server, verify:

1. plain operator gets redirected/denied for all reconciliation backend URLs;
2. finance manager can select one ordinary plus one assembly item for the same customer/CNY;
3. credentials appear once, the copy buttons work, and a refresh does not reveal the password;
4. wrong passwords lock at five failures and the correct password works outside lockout;
5. customer signature changes status to confirmed and the canvas/forms disappear;
6. a separate statement can submit an immutable dispute;
7. reset invalidates an existing session, and void invalidates the link and releases claims;
8. existing delivery-note PDF, shipment signature, finance invoice, and shipment edit paths still work.

- [ ] **Step 3: Check desktop/mobile visual behavior**

Use browser automation or the in-app browser at desktop and phone-width viewports. Confirm the reconciliation list/detail/public pages follow the current SAP-inspired styling, all text is solid black, borders are visible, active controls are distinct, tables scroll rather than truncate, and signature input works with pointer events.

- [ ] **Step 4: Run repository hygiene checks**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors, no temporary spreadsheet/PDF/PNG artifacts inside the repository, no plaintext access password/token fixtures, and no accidental changes outside the planned files.

- [ ] **Step 5: Review implementation against every spec section**

Check off: source selection, one-customer/one-currency validation, 13% back-calculation, snapshot immutability, company profile, one-time credentials, 30-day expiry, lockout, session reset, signature immutability, dispute flow, finance permissions, A4 landscape Excel, signature embedding, error messages, and independent invoice state. Add a focused test before fixing any uncovered gap.

- [ ] **Step 6: Commit the final verified checkpoint when safe**

If no unrelated changes would be included:

```bash
git add reconciliation.py app.py templates/reconciliation_*.html static/reconciliation.css static/reconciliation.js templates/shipped_orders.html templates/base.html templates/admin.html tests/test_reconciliation.py docs/superpowers/specs/2026-09-08-customer-reconciliation-statement-design.md docs/superpowers/plans/2026-09-08-customer-reconciliation-statement.md
git commit -m "feat: add customer reconciliation workflow"
```

If shared files contain unrelated user changes, do not create a mixed commit. Report the working-tree file list and verification evidence instead.
