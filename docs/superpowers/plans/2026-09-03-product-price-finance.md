# Product Pricing and Finance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将产品资料拆成基本信息与技术资料两个页签，并实现受权限保护的产品价格、发货价格快照、客户开票资料及与实际发货明细关联的财务开票/收款流程。

**Architecture:** 保持现有 Flask + SQLite 单体应用结构，在 `app.py` 中沿用幂等迁移、路由和事务模式；新增一个无数据库依赖的 `pricing.py` 负责 Decimal 金额解析、格式化和整数最小货币单位计算。发货表保存产品价格快照，财务开票表再保存发货与客户开票资料快照，并用有效占用键防止同一发货明细重复开票。

**Tech Stack:** Python 3、Flask、SQLite、Jinja2、`decimal.Decimal`、openpyxl、ReportLab、原生 JavaScript、`unittest`

**Spec:** `docs/superpowers/specs/2026-09-03-product-price-finance-design.md`

## Global Constraints

- 组装件图号仍只是配件属性，不创建组装件产品，也不设置组装件独立价格。
- 产品只保存一个当前单价和币种；修改产品价格不能改变历史发货快照。
- 金额以整数最小货币单位保存和计算，不使用二进制浮点数。
- 空价格表示“未设置”，价格 `0` 合法，负数非法。
- 没有价格仍允许下单和发货，但不能开票。
- 现有历史发货不自动回填当前产品价格。
- 每张开票单只能包含同一客户和同一币种；一条发货明细不能部分开票或重复开票。
- 普通送货单、公开签收页和公开签收接口永远不包含价格。
- 管理员始终拥有价格与财务权限；既有普通用户升级后默认没有新权限。
- 无价格权限的响应不得查询后再隐藏价格，HTML、JSON、Excel、PDF 和脚本变量均不得包含价格字段。
- 迁移只新增字段、表和索引，不覆盖现有产品、客户、订单或发货数据。

## File Structure

- Create `pricing.py`: 无数据库依赖的币种、金额解析、格式化和乘法规则。
- Create `tests/test_pricing.py`: 金额边界和币种精度单元测试。
- Create `tests/test_product_pricing.py`: 数据迁移、权限、产品页签、价格编辑、发货快照和价格补录测试。
- Create `tests/test_customer_billing.py`: 客户开票资料、只读财务访问和删除保护测试。
- Create `tests/test_finance.py`: 财务来源选择、合并开票、状态机、锁定、并发占用和导出测试。
- Create `templates/product_tabs.html`: 产品查看/编辑共用页签导航。
- Create `templates/detail_technical.html`: 产品技术资料查看页。
- Create `templates/edit_technical.html`: 产品技术资料编辑页。
- Create `templates/customer_billing.html`: 客户开票资料表单。
- Create `templates/finance_invoices.html`: 财务开票单列表与筛选。
- Create `templates/finance_invoice_new.html`: 待开票发货明细选择页。
- Create `templates/finance_invoice_detail.html`: 开票单详情和状态操作。
- Create `static/finance.js`: 发货明细选择、同币种约束和页面金额预览；服务器仍进行最终校验。
- Modify `app.py`: 引入金额帮助函数，增加迁移、权限、产品双页签路由、价格快照、客户开票资料、财务领域函数/路由、锁定检查和导出。
- Modify `templates/detail.html`: 只显示产品基本信息和授权价格。
- Modify `templates/edit.html`: 只编辑基本信息、组装关系和授权价格。
- Modify `templates/admin.html`: 新产品表单增加授权价格字段，后台增加财务入口。
- Modify `templates/users.html`: 增加价格查看和财务管理权限选项。
- Modify `templates/customers.html`: 增加开票资料入口和开票字段搜索提示。
- Modify `templates/shipped_orders.html`: 授权用户显示普通/组装发货价格、补录入口和财务状态。
- Modify `templates/base.html`: 增加财务导航及产品双页签 endpoint 激活规则。
- Modify `templates/shipment_edit.html`: 显示价格快照与开票锁定提示。
- Modify `static/style.css`: 产品页签、价格状态、客户开票表单和财务页面响应式布局。
- Modify `README.md`: 记录价格权限、客户开票资料和财务工作流。

---

### Task 1: Decimal 金额领域模块

**Files:**
- Create: `pricing.py`
- Create: `tests/test_pricing.py`

**Interfaces:**
- Consumes: Python 标准库 `decimal.Decimal`。
- Produces: `SUPPORTED_CURRENCIES: dict[str, int]`、`normalize_currency(raw: str) -> str`、`parse_money_minor(raw: str, currency: str) -> int | None`、`format_money_minor(value: int | None, currency: str) -> str`、`line_total_minor(unit_price_minor: int | None, quantity: int) -> int | None`。

- [ ] **Step 1: Write the failing money tests**

```python
import unittest

from pricing import format_money_minor, line_total_minor, normalize_currency, parse_money_minor


class PricingTests(unittest.TestCase):
    def test_cny_round_trip_and_zero(self):
        self.assertEqual(parse_money_minor("12.34", "CNY"), 1234)
        self.assertEqual(parse_money_minor("0", "CNY"), 0)
        self.assertEqual(format_money_minor(1234, "CNY"), "12.34")

    def test_blank_is_missing_and_negative_is_rejected(self):
        self.assertIsNone(parse_money_minor("", "CNY"))
        with self.assertRaisesRegex(ValueError, "产品价格不能为负数"):
            parse_money_minor("-0.01", "CNY")

    def test_precision_and_currency_are_validated(self):
        with self.assertRaisesRegex(ValueError, "价格最多保留 2 位小数"):
            parse_money_minor("1.001", "CNY")
        self.assertEqual(parse_money_minor("120", "JPY"), 120)
        with self.assertRaisesRegex(ValueError, "价格最多保留 0 位小数"):
            parse_money_minor("120.5", "JPY")
        with self.assertRaisesRegex(ValueError, "请选择有效币种"):
            normalize_currency("ABC")

    def test_line_total_keeps_missing_distinct_from_zero(self):
        self.assertIsNone(line_total_minor(None, 3))
        self.assertEqual(line_total_minor(0, 3), 0)
        self.assertEqual(line_total_minor(250, 4), 1000)
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run: `python -m unittest tests.test_pricing -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pricing'`.

- [ ] **Step 3: Implement exact integer-minor-unit behavior**

Create `pricing.py` with `SUPPORTED_CURRENCIES = {"CNY": 2, "USD": 2, "EUR": 2, "HKD": 2, "GBP": 2, "JPY": 0}`. Parse with `Decimal`, require a finite non-negative value, compare it to `value.quantize(Decimal(1).scaleb(-digits))`, and return `int(value * (10 ** digits))`. `format_money_minor` must render the exact configured number of decimal places and return an empty string for `None`. `line_total_minor` must reject negative quantities and return `None` only when the unit price is `None`.

```python
from decimal import Decimal, InvalidOperation

SUPPORTED_CURRENCIES = {"CNY": 2, "USD": 2, "EUR": 2, "HKD": 2, "GBP": 2, "JPY": 0}


def normalize_currency(raw):
    currency = str(raw or "CNY").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("请选择有效币种")
    return currency


def parse_money_minor(raw, currency):
    text = str(raw or "").strip()
    if not text:
        return None
    currency = normalize_currency(currency)
    digits = SUPPORTED_CURRENCIES[currency]
    quantum = Decimal(1).scaleb(-digits)
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("价格格式不正确") from error
    if not value.is_finite():
        raise ValueError("价格格式不正确")
    if value < 0:
        raise ValueError("产品价格不能为负数")
    if value != value.quantize(quantum):
        raise ValueError(f"价格最多保留 {digits} 位小数")
    return int(value * (10 ** digits))
```

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_pricing -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pricing.py tests/test_pricing.py
git commit -m "feat: add exact product price calculations"
```

### Task 2: Additive 数据库迁移与财务约束

**Files:**
- Modify: `app.py:466-509` (`init_db`)
- Modify: `app.py:509-535` (`ensure_columns`)
- Modify: `app.py:611-733` (`ensure_assembly_shipping_tables`)
- Modify: `app.py:735-932` (`ensure_order_table`)
- Modify: `app.py:1051-1103` (`ensure_user_table`)
- Modify: `app.py:1105-1151` (`ensure_customer_table`)
- Modify: `app.py:1417-1557` (`ensure_indexes`)
- Create: `tests/test_product_pricing.py`
- Create: `tests/test_finance.py`

**Interfaces:**
- Consumes: existing idempotent `init_db()` / `ensure_*` migration convention.
- Produces: new price/customer/user columns; `ensure_finance_tables(conn) -> None`; tables `finance_invoices` and `finance_invoice_items`; unique active source claim.

- [ ] **Step 1: Write failing migration tests**

In a temporary database call `app.init_db()` twice and assert these exact columns exist:

```python
expected = {
    "manuals": {"unit_price_minor", "currency"},
    "product_order_shipments": {"unit_price_minor", "currency", "price_recorded_by", "price_recorded_at"},
    "assembly_shipment_items": {"unit_price_minor", "currency", "price_recorded_by", "price_recorded_at"},
    "customers": {"invoice_title", "tax_id", "registered_address", "registered_phone", "bank_name", "bank_account", "invoice_email"},
    "users": {"can_view_prices", "can_manage_finance"},
}
for table, columns in expected.items():
    actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    self.assertTrue(columns <= actual)
self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'finance_invoices'").fetchone())
self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'finance_invoice_items'").fetchone())
```

Also insert an ordinary user without specifying either new permission and assert both database defaults are `0`; insert a shipment without specifying price and assert its `unit_price_minor` is `NULL`. Call `init_db()` a second time and assert the same data remains unchanged.

- [ ] **Step 2: Run migration tests and verify missing columns**

Run: `python -m unittest tests.test_product_pricing.ProductPricingMigrationTests tests.test_finance.FinanceMigrationTests -v`

Expected: FAIL because the columns and finance tables do not exist.

- [ ] **Step 3: Add idempotent columns and finance tables**

Use nullable `INTEGER` for unit-price fields and `TEXT NOT NULL DEFAULT 'CNY'` for currency. Audit fields use empty-string defaults. Add the seven customer invoice fields and `can_view_prices INTEGER NOT NULL DEFAULT 0`, `can_manage_finance INTEGER NOT NULL DEFAULT 0`.

Add `ensure_finance_tables(conn)` and call it after customer, order and assembly tables exist. The tables must include these exact facts:

```sql
CREATE TABLE IF NOT EXISTS finance_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    invoice_title TEXT NOT NULL DEFAULT '',
    tax_id TEXT NOT NULL DEFAULT '',
    registered_address TEXT NOT NULL DEFAULT '',
    registered_phone TEXT NOT NULL DEFAULT '',
    bank_name TEXT NOT NULL DEFAULT '',
    bank_account TEXT NOT NULL DEFAULT '',
    invoice_email TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL,
    total_minor INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invoice_no TEXT NOT NULL DEFAULT '',
    invoice_date TEXT NOT NULL DEFAULT '',
    payment_date TEXT NOT NULL DEFAULT '',
    finance_remark TEXT NOT NULL DEFAULT '',
    voided_by TEXT NOT NULL DEFAULT '',
    voided_at TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
)
```

```sql
CREATE TABLE IF NOT EXISTS finance_invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    active_claim_key TEXT UNIQUE,
    order_no TEXT NOT NULL DEFAULT '',
    assembly_batch_id INTEGER,
    assembly_drawing_no TEXT NOT NULL DEFAULT '',
    shipped_at TEXT NOT NULL,
    drawing_no TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL,
    unit_price_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    line_total_minor INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES finance_invoices(id) ON DELETE CASCADE,
    CHECK (source_type IN ('ordinary', 'assembly_item'))
)
```

Create indexes for invoice customer/status/date, item invoice ID, and source lookup. A live item uses `active_claim_key = source_type || ':' || source_id`; voiding clears the key but retains the history row.

- [ ] **Step 4: Run migration tests twice**

Run: `python -m unittest tests.test_product_pricing.ProductPricingMigrationTests tests.test_finance.FinanceMigrationTests -v`

Expected: PASS, including the second initialization and legacy `NULL` price assertion.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_product_pricing.py tests/test_finance.py
git commit -m "feat: add pricing and finance schema"
```

### Task 3: Price and finance permission enforcement

**Files:**
- Modify: `app.py:326-374` (`upload_limits` context processor)
- Modify: `app.py:1665-1799` (admin access and `user_has_permission`)
- Modify: `app.py:6277-6474` (user create/update)
- Modify: `templates/users.html`
- Modify: `templates/base.html`
- Test: `tests/test_product_pricing.py`
- Test: `tests/test_finance.py`

**Interfaces:**
- Consumes: `users.can_view_prices`, `users.can_manage_finance` from Task 2.
- Produces: `user_can_view_prices() -> bool`; permission names `price_view` and `finance_manage`; template flags `can_view_prices` and `can_manage_finance`.

- [ ] **Step 1: Write failing permission tests**

Create operator users with all old permissions disabled and assert:

```python
self.login_as("plain")
self.assertFalse(app.user_has_permission("price_view"))
self.assertFalse(app.user_has_permission("finance_manage"))

self.login_as("price-reader")
self.assertTrue(app.user_has_permission("price_view"))
self.assertFalse(app.user_has_permission("finance_manage"))

self.login_as("finance")
self.assertTrue(app.user_has_permission("finance_manage"))
self.assertTrue(app.user_can_view_prices())
```

POST the admin user create and edit forms and verify both flags persist. Directly wrap a small test view with `permission_required("finance_manage")` inside a Flask request context and verify an operator without that permission receives a redirect plus “当前账号没有权限访问该模块”.

- [ ] **Step 2: Run focused permission tests**

Run: `python -m unittest tests.test_product_pricing.ProductPricePermissionTests tests.test_finance.FinancePermissionTests -v`

Expected: FAIL because the new permission names and form fields are not handled.

- [ ] **Step 3: Implement permission composition**

Add:

```python
def user_can_view_prices():
    return user_has_permission("price_view") or user_has_permission("finance_manage")
```

Map `price_view` to `can_view_prices` and `finance_manage` to `can_manage_finance`; admins remain true through the existing role short-circuit. Include `finance_manage` in `user_can_access_admin_modules()`. Add both flags to the context processor, user INSERT, both user UPDATE branches, and both create/edit checkbox groups. Label them “价格查看” and “财务管理”; neither checkbox is checked by default for a new ordinary user.

- [ ] **Step 4: Run permission and existing account tests**

Run: `python -m unittest tests.test_product_pricing.ProductPricePermissionTests tests.test_finance.FinancePermissionTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py templates/users.html templates/base.html tests/test_product_pricing.py tests/test_finance.py
git commit -m "feat: add price and finance permissions"
```

### Task 4: Split product viewing/editing and add current price

**Files:**
- Create: `templates/product_tabs.html`
- Create: `templates/detail_technical.html`
- Create: `templates/edit_technical.html`
- Modify: `app.py:6179-6205` (`manual_detail`)
- Modify: `app.py:12493-12629` (`upload_manual`)
- Modify: `app.py:12631-12876` (`edit_manual`, render helpers and attachment redirect)
- Modify: `templates/detail.html`
- Modify: `templates/edit.html`
- Modify: `templates/admin.html`
- Modify: `templates/base.html`
- Modify: `static/style.css`
- Test: `tests/test_product_pricing.py`

**Interfaces:**
- Consumes: money functions from Task 1 and `user_can_view_prices()` from Task 3.
- Produces: `fetch_manual_by_id(conn, manual_id, include_price=False)`; `posted_product_price(existing_unit_price_minor=None, existing_currency="CNY") -> tuple[int | None, str]`; routes `manual_detail`, `manual_technical`, `edit_manual`, `edit_manual_technical`; Jinja filter `money_minor`; split basic/technical save behavior.

- [ ] **Step 1: Write failing page and price tests**

Cover these exact behaviors:

```python
response = self.client.get(f"/manual/{manual_id}")
self.assertIn("基本信息", response.get_data(as_text=True))
self.assertNotIn("作业指导书", response.get_data(as_text=True))

response = self.client.get(f"/manual/{manual_id}/technical")
self.assertIn("技术资料", response.get_data(as_text=True))
self.assertIn("作业指导书", response.get_data(as_text=True))

response = self.client.post(
    f"/admin/{manual_id}/edit",
    data=self.valid_basic_form(unit_price="12.34", currency="CNY"),
)
self.assertEqual(response.status_code, 302)
with app.get_db() as conn:
    manual = conn.execute("SELECT unit_price_minor, currency FROM manuals WHERE id = ?", (manual_id,)).fetchone()
self.assertEqual((manual["unit_price_minor"], manual["currency"]), (1234, "CNY"))
```

Also assert a price reader sees `12.34 CNY`, a public/unauthorized viewer does not see the amount or “当前单价”, and a product editor without price permission cannot alter the existing price even by posting `unit_price=0.01` directly.

- [ ] **Step 2: Run product tests and verify route/template failures**

Run: `python -m unittest tests.test_product_pricing.ProductPageSplitTests tests.test_product_pricing.ProductCurrentPriceTests -v`

Expected: FAIL because the technical routes and price fields do not exist.

- [ ] **Step 3: Add explicit basic and technical route boundaries**

Keep `/manual/<id>` and `/admin/<id>/edit` as the basic routes. Add `/manual/<id>/technical` and `/admin/<id>/edit/technical`. Move files, materials, work instruction and inspection requirements to the technical templates and technical POST handler; keep identity, packaging, inventory, assembly relationships, remark and price in basic templates and basic POST.

The common tabs partial receives `manual`, `mode` (`view` or `edit`) and `active_tab` (`basic` or `technical`). Attachment deletion must redirect to `edit_manual_technical`. Both edit forms must preserve existing validation and only update fields owned by that tab. `fetch_manual_by_id` must use an explicit safe column list and append `unit_price_minor, currency` only when `include_price=True`; public and unauthorized routes must not use `SELECT *` for a product row.

- [ ] **Step 4: Gate all product price reads and writes**

Register `format_money_minor` as `money_minor`. Only include the price projection/context when `user_can_view_prices()` is true. Centralize form handling so an unauthorized direct POST preserves an existing price and produces no new price:

```python
def posted_product_price(existing_unit_price_minor=None, existing_currency="CNY"):
    if not user_can_view_prices():
        return existing_unit_price_minor, existing_currency
    currency = normalize_currency(request.form.get("currency", "CNY"))
    unit_price_minor = parse_money_minor(request.form.get("unit_price", ""), currency)
    return unit_price_minor, currency
```

For creation, call it with the defaults after `product_create` authorization. For editing, call it with the existing values after `product_edit` authorization. Never place price values in hidden inputs for unauthorized users and never include price columns in an unauthorized UPDATE statement.

- [ ] **Step 5: Run product-focused and attachment regression tests**

Run: `python -m unittest tests.test_product_pricing tests.test_pdf_preview tests.test_assembly_shipping.ProductAssemblyEditorTests -v`

Expected: PASS. Existing attachment preview/delete and assembly relation editing remain functional.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/product_tabs.html templates/detail.html templates/detail_technical.html templates/edit.html templates/edit_technical.html templates/admin.html templates/base.html static/style.css tests/test_product_pricing.py
git commit -m "feat: split product pages and add protected pricing"
```

### Task 5: Capture immutable prices on ordinary and assembly shipments

**Files:**
- Modify: `app.py:4320-4467` (assembly item save/replace)
- Modify: `app.py:10638-10886` (assembly create/edit)
- Modify: `app.py:10889-11007` (ordinary shipment create)
- Test: `tests/test_product_pricing.py`
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Consumes: `manuals.unit_price_minor`, `manuals.currency`; shipment columns from Task 2.
- Produces: `current_product_price_snapshot(conn, manual_id, recorded_by, recorded_at) -> dict`; immutable snapshot values on both shipment source types.

- [ ] **Step 1: Write failing snapshot tests**

Test an ordinary product priced at 250 minor units shipped in quantity 4, then change the product to 999 and assert the shipment remains `(250, "CNY")` and totals 1000. Test `0` and `NULL` separately.

For assembly shipping, configure two products at 100 and 250 minor units, ship quantities 2 and 3, and assert each `assembly_shipment_items` row contains its own snapshot. Change current product prices, edit only the batch date/quantity, and assert pre-existing item prices remain unchanged.

- [ ] **Step 2: Run snapshot tests**

Run: `python -m unittest tests.test_product_pricing.ShipmentPriceSnapshotTests -v`

Expected: FAIL because shipment inserts do not populate snapshot columns.

- [ ] **Step 3: Snapshot server-side during creation**

Implement `current_product_price_snapshot` to select price directly from `manuals` inside the same write transaction. Add price columns to ordinary shipment INSERT. In `_save_assembly_shipment_items`, load prices for all submitted `manual_id` values from the database and ignore any browser-provided price.

When replacing an assembly batch, read old item prices keyed by existing item ID/manual ID before deleting rows. Preserve the old snapshot for retained components; only newly introduced components use the current product price. Set `price_recorded_by` and `price_recorded_at` only when the snapshot is non-null.

- [ ] **Step 4: Run snapshot and assembly lifecycle tests**

Run: `python -m unittest tests.test_product_pricing.ShipmentPriceSnapshotTests tests.test_assembly_shipping.AssemblySaveTests tests.test_assembly_shipping.AssemblyEditDeleteTests -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_product_pricing.py tests/test_assembly_shipping.py
git commit -m "feat: snapshot prices on shipments"
```

### Task 6: Show and backfill shipment prices without leaking them

**Files:**
- Modify: `app.py:3249-3299` (`get_shipped_orders_query`)
- Modify: `app.py:4681-4897` (ordinary/assembly fetch helpers)
- Modify: `app.py:10520-10557` (`shipped_orders`)
- Modify: `app.py:11449-11560` (ordinary edit/delete)
- Modify: `templates/shipped_orders.html`
- Modify: `templates/shipment_edit.html`
- Modify: `static/style.css`
- Test: `tests/test_product_pricing.py`

**Interfaces:**
- Consumes: price snapshot columns and money functions.
- Produces: `get_shipped_orders_query(include_prices=False)`; `fetch_shipped_orders(conn, query="", selected_customer="", shipped_at="", include_prices=False)`; `fetch_assembly_shipment_batches(conn, query="", selected_customer="", shipped_at="", include_prices=False)`; `backfill_shipment_price(source_type, source_id)` route.

- [ ] **Step 1: Write failing visibility and backfill tests**

As an authorized user, assert the shipped page contains the unit price, total and “未记录价格” state. As an unauthorized shipped viewer, patch the connection with a SQLite trace callback and assert generated SELECT statements do not mention `unit_price_minor`, `currency` or finance tables; also assert response HTML contains neither the labels nor the value.

POST a backfill as:

```python
response = self.client.post(
    f"/admin/shipped-orders/prices/ordinary/{shipment_id}",
    data={"unit_price": "8.50", "currency": "CNY"},
)
self.assertEqual(response.status_code, 302)
```

Verify it succeeds only when the user has both price visibility and `shipped_manage`, accepts zero, rejects negative values, and rejects an unknown source type.

- [ ] **Step 2: Run focused tests**

Run: `python -m unittest tests.test_product_pricing.ShipmentPriceVisibilityTests tests.test_product_pricing.ShipmentPriceBackfillTests -v`

Expected: FAIL because fetch helpers and the backfill endpoint are not price-aware.

- [ ] **Step 3: Make query projection permission-aware**

Replace unconditional `SELECT *` in assembly fetches with explicit safe columns. Append price columns only when `include_prices=True`. Keep signature/photo/public lookup helpers on their default `False`. On the shipped admin page pass `include_prices=user_can_view_prices()` and calculate display totals from integer snapshots.

- [ ] **Step 4: Add audited historical price backfill**

The POST route validates `source_type in {"ordinary", "assembly_item"}`, loads the source, verifies its price is currently null, parses the submitted value, and updates unit price, currency, `price_recorded_by`, and `price_recorded_at`. Never change quantity or product current price. Render forms only when `can_view_prices and can_manage_shipped`.

- [ ] **Step 5: Run visibility, public-link and shipped-page regression tests**

Run: `python -m unittest tests.test_product_pricing tests.test_assembly_shipping.AssemblyHistoryTests tests.test_assembly_shipping.AssemblyShippingPageTests -v`

Expected: PASS, including public signature/photo responses without price data.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/shipped_orders.html templates/shipment_edit.html static/style.css tests/test_product_pricing.py
git commit -m "feat: add protected shipment price history"
```

### Task 7: Add structured customer invoice details

**Files:**
- Modify: `app.py:1105-1151` (customer helpers)
- Modify: `app.py:6536-6644` (customer routes)
- Create: `templates/customer_billing.html`
- Modify: `templates/customers.html`
- Modify: `static/style.css`
- Create: `tests/test_customer_billing.py`

**Interfaces:**
- Consumes: customer invoice columns and `customers` / `finance_manage` permissions.
- Produces: `customer_billing(customer_id)` GET/POST route; `customer_invoice_snapshot(row) -> dict[str, str]`.

- [ ] **Step 1: Write failing customer billing tests**

POST all seven fields, reload the customer, and compare exact values. Assert customer managers can GET/POST, finance managers can GET but receive 403/redirect on POST, and unrelated users cannot GET. Search by invoice title and tax ID. Create a finance invoice reference and assert customer deletion returns “该客户已有财务记录，不能删除”.

- [ ] **Step 2: Run customer billing tests**

Run: `python -m unittest tests.test_customer_billing -v`

Expected: FAIL because the billing route/template and deletion guard do not exist.

- [ ] **Step 3: Implement the dedicated billing form**

Add `/admin/customers/<int:customer_id>/billing` with `@login_required`. GET requires `customers` or `finance_manage`; POST requires `customers`. Read fields only from an allowlist:

```python
CUSTOMER_INVOICE_FIELDS = (
    "invoice_title", "tax_id", "registered_address", "registered_phone",
    "bank_name", "bank_account", "invoice_email",
)
values = {field: request.form.get(field, "").strip() for field in CUSTOMER_INVOICE_FIELDS}
```

Validate non-empty `invoice_email` on the server with `^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$` and show “收票邮箱格式不正确” on failure. Add a “开票信息” link per customer row and include invoice title/tax ID in customer search. Do not attempt to migrate unstructured records from `common_infos`.

- [ ] **Step 4: Guard customer deletion and snapshot helper**

Before DELETE, query `finance_invoices` by `customer_id`; block deletion if any record exists, including void records. `customer_invoice_snapshot` returns a plain dictionary containing customer name and all seven billing fields for finance creation.

- [ ] **Step 5: Run customer and existing admin tests**

Run: `python -m unittest tests.test_customer_billing tests.test_product_pricing.ProductPricePermissionTests -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/customers.html templates/customer_billing.html static/style.css tests/test_customer_billing.py
git commit -m "feat: add customer invoice details"
```

### Task 8: Implement finance source aggregation and invoice state machine

**Files:**
- Modify: `app.py` after shipment fetch helpers
- Test: `tests/test_finance.py`

**Interfaces:**
- Consumes: shipment snapshots, customer invoice snapshot, finance tables.
- Produces: `finance_claim_key(source_type, source_id) -> str`; `fetch_available_finance_sources(conn, customer_name, currency="") -> list[dict]`; `create_finance_invoice(conn, customer_id, source_refs, created_by) -> int`; `replace_pending_invoice_items(conn, invoice_id, source_refs, updated_by) -> None`; `mark_finance_invoice_invoiced(conn, invoice_id, invoice_no, invoice_date, finance_remark, updated_by) -> None`; `mark_finance_invoice_paid(conn, invoice_id, payment_date, updated_by) -> None`; `reopen_finance_invoice_payment(conn, invoice_id, updated_by) -> None`; `void_finance_invoice(conn, invoice_id, updated_by) -> None`; `delete_pending_finance_invoice(conn, invoice_id) -> None`; `finance_source_is_claimed(conn, source_type, source_id) -> bool`.

- [ ] **Step 1: Write failing source and creation tests**

Seed ordinary and assembly item sources for two customers and two currencies. Assert available sources exclude null prices and active claims. Create an invoice from ordinary plus assembly sources belonging to one customer/currency and assert:

```python
self.assertEqual(invoice["status"], "pending")
self.assertEqual(invoice["currency"], "CNY")
self.assertEqual(invoice["total_minor"], sum(item["line_total_minor"] for item in items))
self.assertEqual({item["active_claim_key"] for item in items}, {f"ordinary:{shipment_id}", f"assembly_item:{assembly_item_id}"})
```

Assert cross-customer, cross-currency, null-price and duplicate references raise the exact user-facing `ValueError` messages from the spec.

- [ ] **Step 2: Run creation tests**

Run: `python -m unittest tests.test_finance.FinanceSourceTests tests.test_finance.FinanceInvoiceCreationTests -v`

Expected: FAIL because finance domain functions are undefined.

- [ ] **Step 3: Implement normalized source loading**

Use only two allowed source types. Ordinary source customer is `COALESCE(NULLIF(TRIM(product_orders.customer), ''), TRIM(manuals.customer))`; assembly source customer is `assembly_shipment_batches.customer`. Return the same normalized keys for both source types: `source_type`, `source_id`, `customer_name`, `order_no`, `assembly_batch_id`, `assembly_drawing_no`, `shipped_at`, `drawing_no`, `product_name`, `quantity`, `unit_price_minor`, `currency`, `line_total_minor`.

Reject duplicate request references before SQL. Re-read every source inside `BEGIN IMMEDIATE`; never trust page-provided customer, currency, quantity, unit price or totals.

- [ ] **Step 4: Implement atomic claims and draft replacement**

Build the invoice header from the selected `customers` row and `customer_invoice_snapshot`. Insert each item with its live `active_claim_key`; translate uniqueness failures to “该发货记录已加入其他开票单”. Recalculate and persist `total_minor` after every pending-item change. `replace_pending_invoice_items` must validate status `pending`, partition references into retained/added/removed sets, insert added claims before deleting removed draft rows, leave retained rows untouched, and finish within the same transaction.

- [ ] **Step 5: Write and run failing state-transition tests**

Test exact legal transitions:

```text
pending -> invoiced -> paid -> invoiced -> void
pending -> deleted
invoiced -> void
```

Test illegal transitions: pending to paid, paid directly to void, editing items after invoiced, empty invoice number/date, and deleting invoiced records.

Run: `python -m unittest tests.test_finance.FinanceInvoiceStateTests -v`

Expected: FAIL until lifecycle functions exist.

- [ ] **Step 6: Implement lifecycle functions**

`mark_finance_invoice_invoiced` requires non-empty invoice number/date and at least one line. `mark_finance_invoice_paid` requires `invoiced` and a payment date. `reopen_finance_invoice_payment` clears the payment date and returns `paid` to `invoiced`. `void_finance_invoice` accepts only `invoiced`, sets status/void audit fields, and sets all item `active_claim_key` values to `NULL`. Draft deletion deletes the header so cascade removes draft lines and claims.

- [ ] **Step 7: Add concurrency regression**

Open two direct SQLite connections to a temporary database. Attempt to claim the same source in two transactions and assert only one effective invoice owns the non-null claim. The loser must surface “该发货记录已加入其他开票单”, not a raw `sqlite3.IntegrityError`.

- [ ] **Step 8: Run all finance domain tests**

Run: `python -m unittest tests.test_finance.FinanceSourceTests tests.test_finance.FinanceInvoiceCreationTests tests.test_finance.FinanceInvoiceStateTests tests.test_finance.FinanceConcurrencyTests -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app.py tests/test_finance.py
git commit -m "feat: add finance invoice domain workflow"
```

### Task 9: Build finance pages and navigation

**Files:**
- Modify: `app.py` near other admin module routes
- Create: `templates/finance_invoices.html`
- Create: `templates/finance_invoice_new.html`
- Create: `templates/finance_invoice_detail.html`
- Create: `static/finance.js`
- Modify: `templates/base.html`
- Modify: `templates/admin.html`
- Modify: `templates/dashboard.html`
- Modify: `static/style.css`
- Test: `tests/test_finance.py`

**Interfaces:**
- Consumes: Task 8 finance domain functions and `finance_manage` permission.
- Produces: routes `finance_invoices`, `new_finance_invoice`, `finance_invoice_detail`, `edit_finance_invoice_items`, `issue_finance_invoice`, `pay_finance_invoice`, `reopen_finance_invoice`, `void_finance_invoice_route`, `delete_finance_invoice`.

- [ ] **Step 1: Write failing route and page tests**

Assert a finance manager can:

- filter invoice list by customer, status, invoice number and date;
- choose one customer and see only its unclaimed priced shipments;
- submit ordinary and assembly refs using `source_ref=ordinary:12` / `source_ref=assembly_item:34`;
- create a draft, edit draft items, issue, mark paid, reopen and void;
- see customer billing snapshots and audit fields on detail.

Assert a price-only user and an unrelated operator cannot access any `/admin/finance` route.

- [ ] **Step 2: Run route tests**

Run: `python -m unittest tests.test_finance.FinanceRouteTests -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement GET pages and POST actions**

Protect every finance route with `@permission_required("finance_manage")`. Keep status changes as POST-only endpoints. Carry customer and currency filters through redirects. Parse source references strictly with `^(ordinary|assembly_item):([1-9][0-9]*)$`; duplicate or malformed values produce a clear flash and no writes.

The new-invoice page groups available sources by ordinary/assembly context, displays quantity, unit price and server-provided line total, and disables submission until at least one item is selected. `static/finance.js` may preview totals and stop mixed-currency selection, but the POST route must repeat all validation.

- [ ] **Step 4: Add navigation and responsive styles**

Show the “财务” primary/admin/dashboard links only when `can_manage_finance`. Add compact status badges for 待开票、已开票、已收款 and 已作废. On narrow screens, turn invoice tables into the same labeled-card pattern used by existing pages.

- [ ] **Step 5: Run route and template tests**

Run: `python -m unittest tests.test_finance.FinanceRouteTests tests.test_finance.FinancePermissionTests -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py templates/finance_invoices.html templates/finance_invoice_new.html templates/finance_invoice_detail.html templates/base.html templates/admin.html templates/dashboard.html static/finance.js static/style.css tests/test_finance.py
git commit -m "feat: add finance invoice interface"
```

### Task 10: Enforce finance locks across shipment/order/product lifecycle

**Files:**
- Modify: `app.py:4447-4500` (`replace_assembly_shipment`)
- Modify: `app.py:10739-10886` (assembly edit/delete routes)
- Modify: `app.py:11449-11560` (ordinary edit/delete routes)
- Modify: `app.py:12145-12491` (order edit/delete routes)
- Modify: `app.py:12955-12975` (product delete route)
- Modify: `templates/shipment_edit.html`
- Modify: `templates/assembly_shipment_edit.html`
- Test: `tests/test_finance.py`
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Consumes: `finance_source_is_claimed` from Task 8.
- Produces: `assert_finance_sources_mutable(conn, refs) -> None`; complete mutation guards for invoice-linked sources.

- [ ] **Step 1: Write failing lock tests**

For both pending and issued invoices, assert ordinary shipment quantity/date changes and deletion are rejected, assembly batch replacement/deletion is rejected when any item is claimed, and price backfill is rejected. Logistics remarks may still change when quantity and date are unchanged. Assert removing a source from a pending invoice or voiding an issued invoice releases it for editing/deletion.

Also attempt to delete an order/product whose cascade would remove an actively claimed source and assert the operation is blocked before any inventory, files or database rows are changed.

- [ ] **Step 2: Run lock tests**

Run: `python -m unittest tests.test_finance.FinanceShipmentLockTests -v`

Expected: FAIL because current mutation routes ignore finance claims.

- [ ] **Step 3: Add one shared lock guard before side effects**

Implement:

```python
def assert_finance_sources_mutable(conn, refs):
    for source_type, source_id in refs:
        if finance_source_is_claimed(conn, source_type, source_id):
            raise ValueError("该发货记录已加入开票单，不能修改发货日期、数量或价格")
```

Call it before reversing inventory, deleting images/files, resetting signatures, changing order bindings or deleting rows. For assembly batches, expand all item IDs first. For order/product deletion, collect all descendant ordinary shipments and assembly items before any mutation; use “该产品或订单包含已进入财务的发货记录，不能删除” for those broader operations.

- [ ] **Step 4: Run lifecycle and inventory regressions**

Run: `python -m unittest tests.test_finance.FinanceShipmentLockTests tests.test_assembly_shipping.AssemblyEditDeleteTests tests.test_inventory -v`

Expected: PASS and inventory remains unchanged on rejected operations.

- [ ] **Step 5: Commit**

```bash
git add app.py templates/shipment_edit.html templates/assembly_shipment_edit.html tests/test_finance.py tests/test_assembly_shipping.py
git commit -m "feat: protect invoiced shipment history"
```

### Task 11: Financial exports, leak checks and end-to-end verification

**Files:**
- Modify: `app.py:5233-5630` (workbook/PDF helpers)
- Modify: `app.py` finance routes from Task 9
- Modify: `templates/finance_invoice_detail.html`
- Modify: `README.md`
- Test: `tests/test_finance.py`
- Test: `tests/test_shipped_pdf_company.py`

**Interfaces:**
- Consumes: immutable finance header/items and existing Excel/PDF font/layout helpers.
- Produces: `build_finance_invoice_workbook(invoice, items) -> Workbook`; `build_finance_invoice_pdf(invoice, items) -> BytesIO`; `export_finance_invoice(invoice_id, format)` route.

- [ ] **Step 1: Write failing export and non-leakage tests**

Generate a finance workbook and assert it contains invoice/customer snapshots, ordinary order number, assembly batch/drawing references, product, quantity, formatted unit price, currency, line total and header total. Extract PDF text and assert the same critical fields and exact total.

For an unauthorized user, assert finance exports are denied even with a guessed invoice ID. Generate the existing delivery-note PDF and workbook and assert they do not contain “单价”, “总价”, the price value, tax ID or bank account. Re-run the company-name test to retain “宁波市杰德机械科技有限公司”.

- [ ] **Step 2: Run export tests**

Run: `python -m unittest tests.test_finance.FinanceExportTests tests.test_shipped_pdf_company -v`

Expected: FAIL because finance export builders/routes do not exist.

- [ ] **Step 3: Implement finance-only Excel and PDF exports**

Build documents exclusively from `finance_invoices` and `finance_invoice_items` snapshots, never from current product/customer prices. Use `format_money_minor` for display. Include the invoice currency beside all monetary columns and a footer total. Protect download routes with `finance_manage`; support only `xlsx` and `pdf`, returning 404/400 for other formats.

Do not add price parameters to `build_shipped_orders_workbook`, `build_shipped_orders_pdf`, email delivery-note generation, public signature fetches or public photo routes.

- [ ] **Step 4: Update operator documentation**

Document the two product tabs, who can see/edit prices, how shipment snapshots work, how to maintain customer invoice details, the pending → invoiced → paid flow, how voiding releases shipment rows, and why ordinary delivery notes remain price-free.

- [ ] **Step 5: Run the complete test suite**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS with no warnings indicating schema, template or PDF failures.

- [ ] **Step 6: Run structural verification**

Run: `git diff --check`

Expected: no output.

Run: `rg -n "unit_price_minor|total_minor|bank_account|tax_id" templates/sign.html app.py`

Expected: matches in protected product/shipment/finance code only; no price or billing fields in public signature rendering/query paths or ordinary delivery-note builders.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/finance_invoice_detail.html README.md tests/test_finance.py tests/test_shipped_pdf_company.py
git commit -m "feat: add protected financial exports"
```

## Final Acceptance

- [ ] Start from a temporary copy of a legacy database, run application initialization twice, and verify no existing record was overwritten.
- [ ] Manually verify desktop and narrow-screen product basic/technical tabs, customer billing form, shipped-price columns and finance pages.
- [ ] Verify with three accounts: admin, price-only operator, and finance operator.
- [ ] Change a product price after shipping and confirm shipment and invoice totals remain unchanged.
- [ ] Create a mixed ordinary/assembly invoice, mark it invoiced and paid, reopen it, void it, and confirm its sources become selectable again.
- [ ] Export a finance PDF/Excel and an ordinary delivery-note PDF; verify only the finance documents contain prices and billing details.
- [ ] Re-run `python -m unittest discover -s tests -v` and `git diff --check` immediately before declaring completion.
