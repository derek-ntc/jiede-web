# 组装件配件配置与组装发货 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为配件产品增加多组装件及单套用量配置，并在现有发货页面按客户、组装图号和套数生成可调整、可警告确认、可追溯的组装发货批次。

**Architecture:** 新建 `assembly_shipping.py` 承载无 Flask/SQLite 副作用的数量、订单分配、库存缺口和预览指纹逻辑；`app.py` 负责幂等建表、查询、事务保存、库存流水和路由。产品编辑与组装发货分别使用两个模板局部块，浏览器交互集中在 `static/assembly_shipping.js`，最终状态始终由服务器重新计算。

**Tech Stack:** Python 3、Flask、SQLite、Jinja2、原生 JavaScript、CSS、`unittest`

**Spec:** `docs/superpowers/specs/2026-08-30-assembly-bom-shipping-design.md`

## Global Constraints

- 组装件图号只是配件属性，不创建组装件产品、库存或虚假订单。
- 一个配件可属于多个组装件；同一配件不得重复相同组装图号。
- 组装配置通过配件当前客户隔离；存在组装配置时产品客户不能为空。
- 单套用量、组装套数、计算数量和最终发货数量均为正整数。
- 订单按计划发货日期、下单日期、创建时间和 ID 的稳定顺序先进先出分配；剩余量保存为空订单 allocation。
- 库存不足允许确认发货，但库存最低扣到零，并保存实际扣减量和缺口量。
- 浏览器计算仅用于交互；服务器在预览和最终保存时重新校验全部数据。
- 一个组装发货批次必须在单一数据库事务中成功或全部回滚。
- 现有普通订单发货行为、签收、图片、库存同步和导出不得回归。
- 当前工作区不是 Git 仓库；不得擅自执行 `git init`。各任务的提交步骤仅在仓库恢复后执行，否则以测试输出作为检查点。

## File Structure

- Create `assembly_shipping.py`: 纯领域逻辑；解析正整数、规范化配置、展开配件、FIFO 分配、库存结果和预览指纹。
- Create `templates/assembly_components_editor.html`: 产品编辑页的多行组装关系编辑器。
- Create `templates/assembly_shipping_form.html`: 已发货页面中的按组装件发货表单和预览明细容器。
- Create `static/assembly_shipping.js`: 产品编辑器动态行、客户/组装选项加载、预览、警告确认及最终提交。
- Create `tests/test_assembly_shipping.py`: 纯领域、数据库、路由、库存、事务和界面回归测试。
- Modify `app.py`: 幂等迁移、组装配置 CRUD、组装预览/保存/编辑/删除、订单汇总、库存流水和图片支持。
- Modify `templates/edit.html`: 引入组装关系编辑器并加载脚本。
- Modify `templates/shipped_orders.html`: 增加发货模式、组装表单、组装历史批次并加载脚本。
- Modify `static/style.css`: 组装配置表、发货预览、警告和移动端样式。
- Modify `tests/test_inventory.py`: 保留并扩展普通订单发货库存回归断言。
- Modify `tests/test_inspection_reports.py`: 保留产品编辑与现有发货相关回归断言。

---

### Task 1: 纯组装发货领域逻辑

**Files:**
- Create: `assembly_shipping.py`
- Create: `tests/test_assembly_shipping.py`

**Interfaces:**
- Produces: `parse_positive_int(value, label) -> int`
- Produces: `normalize_component_rows(drawing_numbers, quantities) -> list[dict]`
- Produces: `expand_components(components, set_quantity, overrides=None) -> list[dict]`
- Produces: `allocate_quantity(quantity, orders) -> list[dict]`
- Produces: `inventory_result(requested_quantity, available_quantity) -> dict`
- Produces: `preview_token(preview) -> str`

- [ ] **Step 1: Write failing unit tests for row normalization and quantity expansion**

```python
class AssemblyShippingDomainTests(unittest.TestCase):
    def test_normalize_component_rows_ignores_blank_rows_and_rejects_duplicates(self):
        rows = assembly_shipping.normalize_component_rows(
            [" ASM-100 ", "", "ASM-200"], ["2", "", "3"]
        )
        self.assertEqual(rows, [
            {"assembly_drawing_no": "ASM-100", "quantity_per_set": 2, "sort_order": 0},
            {"assembly_drawing_no": "ASM-200", "quantity_per_set": 3, "sort_order": 1},
        ])
        with self.assertRaisesRegex(ValueError, "不能重复"):
            assembly_shipping.normalize_component_rows(["ASM-100", "ASM-100"], ["1", "2"])

    def test_expand_components_uses_override_without_losing_calculated_quantity(self):
        result = assembly_shipping.expand_components(
            [{"manual_id": 7, "drawing_no": "P1", "product_name": "配件1", "quantity_per_set": 2}],
            100,
            {7: 180},
        )
        self.assertEqual(result[0]["calculated_quantity"], 200)
        self.assertEqual(result[0]["shipped_quantity"], 180)
```

- [ ] **Step 2: Run the domain tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyShippingDomainTests -v`

Expected: FAIL because `assembly_shipping` and its functions do not exist.

- [ ] **Step 3: Implement strict normalization and expansion**

```python
def parse_positive_int(value, label):
    text = str(value or "").strip()
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须为大于 0 的整数")
    if parsed <= 0 or str(parsed) != text:
        raise ValueError(f"{label}必须为大于 0 的整数")
    return parsed


def normalize_component_rows(drawing_numbers, quantities):
    result = []
    seen = set()
    for drawing, quantity in zip_longest(drawing_numbers, quantities, fillvalue=""):
        drawing = str(drawing or "").strip()
        quantity = str(quantity or "").strip()
        if not drawing and not quantity:
            continue
        if not drawing or not quantity:
            raise ValueError("组装件图号和每套用量必须同时填写")
        key = drawing.casefold()
        if key in seen:
            raise ValueError(f"组装件图号 {drawing} 不能重复")
        seen.add(key)
        result.append({
            "assembly_drawing_no": drawing,
            "quantity_per_set": parse_positive_int(quantity, "每套用量"),
            "sort_order": len(result),
        })
    return result
```

Implement `expand_components()` so it copies product snapshot fields, sets `calculated_quantity = set_quantity * quantity_per_set`, and uses a validated override when present.

```python
def expand_components(components, set_quantity, overrides=None):
    set_quantity = parse_positive_int(set_quantity, "组装件套数")
    overrides = overrides or {}
    expanded = []
    for component in components:
        item = dict(component)
        calculated = set_quantity * parse_positive_int(
            component["quantity_per_set"], "每套用量"
        )
        override = overrides.get(int(component["manual_id"]))
        item["calculated_quantity"] = calculated
        item["shipped_quantity"] = (
            parse_positive_int(override, "配件实际发货数量")
            if override is not None else calculated
        )
        expanded.append(item)
    return expanded


def allocate_quantity(quantity, orders):
    remaining = parse_positive_int(quantity, "配件实际发货数量")
    allocations = []
    for order in orders:
        available = max(0, int(order["unshipped_quantity"] or 0))
        used = min(remaining, available)
        if used:
            allocations.append({"order_id": int(order["id"]), "quantity": used})
            remaining -= used
        if remaining == 0:
            break
    if remaining:
        allocations.append({"order_id": None, "quantity": remaining})
    return allocations


def inventory_result(requested_quantity, available_quantity):
    requested = parse_positive_int(requested_quantity, "配件实际发货数量")
    available = max(0, int(available_quantity or 0))
    deducted = min(requested, available)
    return {
        "requested_quantity": requested,
        "deducted_quantity": deducted,
        "shortage_quantity": requested - deducted,
    }
```

- [ ] **Step 4: Add and run FIFO allocation, inventory and deterministic-token tests**

```python
def test_allocate_quantity_splits_orders_and_leaves_direct_remainder(self):
    orders = [
        {"id": 11, "unshipped_quantity": 60},
        {"id": 12, "unshipped_quantity": 80},
    ]
    self.assertEqual(
        assembly_shipping.allocate_quantity(200, orders),
        [
            {"order_id": 11, "quantity": 60},
            {"order_id": 12, "quantity": 80},
            {"order_id": None, "quantity": 60},
        ],
    )

def test_inventory_result_never_produces_negative_stock(self):
    self.assertEqual(
        assembly_shipping.inventory_result(280, 250),
        {"requested_quantity": 280, "deducted_quantity": 250, "shortage_quantity": 30},
    )

def test_preview_token_is_stable_and_changes_with_order_or_stock_state(self):
    first = {"customer": "客户A", "items": [{"manual_id": 1, "stock": 5}]}
    self.assertEqual(assembly_shipping.preview_token(first), assembly_shipping.preview_token(first))
    changed = {"customer": "客户A", "items": [{"manual_id": 1, "stock": 4}]}
    self.assertNotEqual(assembly_shipping.preview_token(first), assembly_shipping.preview_token(changed))
```

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyShippingDomainTests -v`

Expected: PASS. Implement `preview_token()` with canonical JSON and SHA-256:

```python
def preview_token(preview):
    canonical = json.dumps(
        preview, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
```

- [ ] **Step 5: Run the full new test module and record the checkpoint**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping -v`

Expected: PASS. If Git is available, commit with `git add assembly_shipping.py tests/test_assembly_shipping.py && git commit -m "feat: add assembly shipping domain rules"`; otherwise do not initialize Git.

---

### Task 2: 幂等数据库迁移和产品组装关系持久化

**Files:**
- Modify: `app.py:285-330` (`init_db`)
- Modify: `app.py:410-445` (new assembly table helpers near product helpers)
- Modify: `app.py:10652-10720` (`delete_manual` cleanup)
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Consumes: `normalize_component_rows()` from Task 1
- Produces: `ensure_assembly_shipping_tables(conn) -> None`
- Produces: `get_product_assembly_components(conn, manual_id) -> list[sqlite3.Row]`
- Produces: `save_product_assembly_components(conn, manual_id, components, now) -> None`

- [ ] **Step 1: Add the reusable Flask/database test fixture**

```python
class AssemblyAppTestCase(unittest.TestCase):
    def setUp(self):
        self.original_db_path = app.DB_PATH
        self.original_database_ready = app.DATABASE_READY
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.tmpdir.name) / "manuals.db"
        app.DATABASE_READY = False
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_username"] = "admin"
            session["admin_role"] = "admin"
        self.order_ids = {}

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        app.DATABASE_READY = self.original_database_ready
        self.tmpdir.cleanup()

    def create_product(self, drawing_no="P1", customer="客户A"):
        now = "2026-08-30T09:00:00"
        with app.get_db() as conn:
            return conn.execute(
                """
                INSERT INTO manuals (
                    product_name, customer, model, category, version, remark,
                    filename, original_filename, created_at, updated_at,
                    drawing_no, supplier, pack_quantity, pack_carton_size,
                    sku, barcode, min_stock
                ) VALUES (?, ?, '', '', '', '', '', '', ?, ?, ?, '', '', '', '', '', 0)
                """,
                (f"产品-{drawing_no}", customer, now, now, drawing_no),
            ).lastrowid

    def valid_product_form(self, customer="客户A", drawing_no="P1"):
        return {
            "drawing_no": drawing_no,
            "product_name": f"产品-{drawing_no}",
            "supplier": "",
            "customer": customer,
            "pack_quantity": "",
            "pack_carton_size": "",
            "pack_weight": "",
            "sku": "",
            "barcode": "",
            "default_location_id": "",
            "min_stock": "0",
            "remark": "",
            "description_html": "",
        }
```

All database/route test classes below inherit `AssemblyAppTestCase`. Add focused helpers in the class that first needs them: `create_order(manual_id, order_no, quantity, planned_ship_at)`, `stock_product(manual_id, quantity)`, `configure_component(manual_id, assembly_drawing_no, quantity_per_set)`, and `post_preview(customer, assembly_drawing_no, set_quantity, overrides=None)`. Each helper must insert through parameterized SQL or call the production route and return the created IDs/JSON; helpers must not duplicate production allocation or inventory logic.

- [ ] **Step 2: Write a failing idempotent migration test**

```python
def test_init_db_creates_assembly_tables_and_indexes_idempotently(self):
    app.init_db()
    app.init_db()
    with app.get_db() as conn:
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        self.assertTrue({
            "product_assembly_components",
            "assembly_shipment_batches",
            "assembly_shipment_items",
            "assembly_shipment_allocations",
            "assembly_shipment_images",
        }.issubset(tables))
```

- [ ] **Step 3: Run the migration test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyDatabaseTests.test_init_db_creates_assembly_tables_and_indexes_idempotently -v`

Expected: FAIL because the tables are absent.

- [ ] **Step 4: Implement `ensure_assembly_shipping_tables(conn)` and call it from `init_db()`**

Create the five tables exactly as specified. Add indexes on component `manual_id`, normalized lookup columns `(assembly_drawing_no, manual_id)`, batch `(shipped_at, customer)`, item `batch_id`, allocation `item_id`/`order_id`, and image `batch_id`. Use `ON DELETE CASCADE` in declarations and retain explicit application cleanup because current connections do not globally enable foreign keys.

- [ ] **Step 5: Write failing persistence tests**

```python
def test_save_product_assembly_components_replaces_rows_in_display_order(self):
    manual_id = self.create_product(customer="客户A")
    now = "2026-08-30T10:00:00"
    components = [
        {"assembly_drawing_no": "ASM-100", "quantity_per_set": 2, "sort_order": 0},
        {"assembly_drawing_no": "ASM-200", "quantity_per_set": 3, "sort_order": 1},
    ]
    with app.get_db() as conn:
        app.save_product_assembly_components(conn, manual_id, components, now)
        rows = app.get_product_assembly_components(conn, manual_id)
    self.assertEqual([(row["assembly_drawing_no"], row["quantity_per_set"]) for row in rows], [
        ("ASM-100", 2), ("ASM-200", 3)
    ])
```

- [ ] **Step 6: Implement persistence and explicit product-delete cleanup**

`save_product_assembly_components()` must delete rows for the product and insert the normalized submitted set inside the caller's transaction. Update `delete_manual()` to delete component rows before deleting `manuals`; do not change global SQLite foreign-key behavior.

- [ ] **Step 7: Run database tests and checkpoint**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyDatabaseTests -v`

Expected: PASS. If Git is available, commit `app.py` and the test file with message `feat: persist product assembly components`.

---

### Task 3: 产品编辑页组装关系管理

**Files:**
- Create: `templates/assembly_components_editor.html`
- Modify: `templates/edit.html:1-190`
- Modify: `app.py:10375-10531` (`edit_manual`)
- Create: `static/assembly_shipping.js`
- Modify: `static/style.css`
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Consumes: `normalize_component_rows()`, `get_product_assembly_components()`, `save_product_assembly_components()`
- Produces form fields: `assembly_drawing_no[]`, `assembly_quantity_per_set[]`

- [ ] **Step 1: Write failing route tests for render, save and validation**

```python
def test_edit_product_renders_and_saves_multiple_assembly_memberships(self):
    manual_id = self.create_product(customer="客户A")
    html = self.client.get(f"/admin/{manual_id}/edit").get_data(as_text=True)
    self.assertIn("所属组装件", html)
    response = self.client.post(f"/admin/{manual_id}/edit", data={
        **self.valid_product_form(customer="客户A"),
        "assembly_drawing_no": ["ASM-100", "ASM-200"],
        "assembly_quantity_per_set": ["2", "3"],
    })
    self.assertEqual(response.status_code, 302)
    with app.get_db() as conn:
        rows = app.get_product_assembly_components(conn, manual_id)
    self.assertEqual(len(rows), 2)

def test_edit_product_rejects_assembly_membership_without_customer(self):
    manual_id = self.create_product(customer="")
    response = self.client.post(f"/admin/{manual_id}/edit", data={
        **self.valid_product_form(customer=""),
        "assembly_drawing_no": ["ASM-100"],
        "assembly_quantity_per_set": ["2"],
    }, follow_redirects=True)
    self.assertIn("请先选择客户", response.get_data(as_text=True))
```

- [ ] **Step 2: Run the route tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.ProductAssemblyEditorTests -v`

Expected: FAIL because the editor and POST handling are missing.

- [ ] **Step 3: Implement server-side edit handling**

In `edit_manual()` parse both lists before saving uploads. Reject invalid/duplicate rows and reject non-empty components when `customer == ""`. Within the existing product update transaction, call `save_product_assembly_components()`. On GET, pass `assembly_components` to `edit.html`.

- [ ] **Step 4: Build the reusable editor partial and dynamic-row behavior**

The partial must render at least one row, preserve database order, use numeric inputs with `min="1" step="1"`, and provide `data-assembly-component-*` hooks. `static/assembly_shipping.js` must clone a hidden row, clear its inputs, remove rows, and leave one blank row when the last visible row is cleared.

- [ ] **Step 5: Add focused styling and run tests**

Add `.assembly-component-editor`, `.assembly-component-row`, and mobile stacking rules without changing existing product-form widths. Run:

`.venv/bin/python -m unittest tests.test_assembly_shipping.ProductAssemblyEditorTests tests.test_inspection_reports.InspectionReportTests.test_product_materials_can_be_created_displayed_searched_and_updated -v`

Expected: PASS.

- [ ] **Step 6: Checkpoint**

If Git is available, commit the product editor files with message `feat: manage assembly memberships on products`; otherwise retain the passing test output as the checkpoint.

---

### Task 4: 客户组装选项与只读发货预览 API

**Files:**
- Modify: `app.py` near `get_unshipped_order_options()` and `shipped_orders()`
- Modify: `assembly_shipping.py`
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Produces: `get_assembly_options_for_customer(conn, customer) -> list[str]`
- Produces: `get_assembly_definition(conn, customer, assembly_drawing_no) -> list[sqlite3.Row]`
- Produces: `get_component_open_orders(conn, manual_id, customer, exclude_batch_id=None) -> list[dict]`
- Produces: `build_assembly_shipment_preview(conn, customer, assembly_drawing_no, set_quantity, overrides=None, exclude_batch_id=None) -> dict`
- Produces routes: `GET /admin/shipped-orders/assembly-options`, `POST /admin/shipped-orders/assembly-preview`

- [ ] **Step 1: Write failing customer-isolation and FIFO preview tests**

```python
def test_preview_is_customer_scoped_and_splits_oldest_orders(self):
    p1 = self.create_product("P1", "客户A")
    self.configure_component(p1, "ASM-100", 2)
    other = self.create_product("OTHER", "客户B")
    self.configure_component(other, "ASM-100", 9)
    self.create_order(p1, "SO-2", 80, planned_ship_at="2026-09-02")
    self.create_order(p1, "SO-1", 60, planned_ship_at="2026-09-01")
    preview = self.post_preview("客户A", "ASM-100", 100)
    item = preview["items"][0]
    self.assertEqual(item["calculated_quantity"], 200)
    self.assertEqual(item["allocations"], [
        {"order_id": self.order_ids["SO-1"], "order_no": "SO-1", "quantity": 60},
        {"order_id": self.order_ids["SO-2"], "order_no": "SO-2", "quantity": 80},
        {"order_id": None, "order_no": "", "quantity": 60},
    ])
    self.assertEqual(item["no_order_quantity"], 60)
```

- [ ] **Step 2: Run preview tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyPreviewTests -v`

Expected: FAIL because query helpers and API routes are absent.

- [ ] **Step 3: Implement deterministic database queries**

`get_assembly_definition()` joins components to `manuals`, filters exact customer and case-insensitive exact assembly number, and orders by component `sort_order`, product drawing number, then product ID. `get_component_open_orders()` computes remaining quantity from the updated order shipment summary and applies the approved FIFO sort, with empty `planned_ship_at` last.

- [ ] **Step 4: Implement `build_assembly_shipment_preview()`**

Return this stable JSON shape:

```python
{
    "customer": "客户A",
    "assembly_drawing_no": "ASM-100",
    "set_quantity": 100,
    "items": [{
        "manual_id": 1,
        "drawing_no": "P1",
        "product_name": "配件1",
        "quantity_per_set": 2,
        "calculated_quantity": 200,
        "shipped_quantity": 200,
        "available_inventory": 170,
        "inventory_deducted_quantity": 170,
        "inventory_shortage_quantity": 30,
        "no_order_quantity": 60,
        "allocations": [],
    }],
    "warnings": [],
    "preview_token": "sha256-hex",
}
```

Warnings must contain structured `code`, `manual_id`, `drawing_no`, `quantity`, and human-readable `message` fields for `no_order` and `inventory_shortage`.

- [ ] **Step 5: Implement permission-protected API routes**

Options route uses `shipped_view`; preview uses `shipped_manage`. Return HTTP 400 JSON for invalid customer, assembly, set quantity or overrides; return 404 JSON when the selected assembly has no valid components.

- [ ] **Step 6: Run preview tests and checkpoint**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyPreviewTests -v`

Expected: PASS. If Git is available, commit with message `feat: preview customer assembly shipments`.

---

### Task 5: 原子保存、订单汇总和允许缺货的库存扣减

**Files:**
- Modify: `app.py:2447-2555` (order shipment summary/options)
- Modify: `app.py:2769-3035` (inventory helpers)
- Modify: `app.py:8847-8965` (new assembly save route adjacent to normal create route)
- Test: `tests/test_assembly_shipping.py`
- Test: `tests/test_inventory.py`

**Interfaces:**
- Produces: `assembly_inventory_related_id(item_id) -> str`
- Produces: `deduct_inventory_allow_shortage(conn, item_id, manual_id, quantity, customer, reference) -> int`
- Produces: `reverse_assembly_inventory_deduction(conn, item_id) -> int`
- Produces: `save_assembly_shipment(conn, preview, shipped_at, logistics_no, created_by) -> int`
- Produces route: `POST /admin/shipped-orders/assembly/new`

- [ ] **Step 1: Write failing save tests for mixed ordered/direct allocation**

```python
def test_confirmed_assembly_shipment_saves_ordered_and_direct_allocations(self):
    manual_id = self.create_product("P1", "客户A")
    self.configure_component(manual_id, "ASM-100", 2)
    order_id = self.create_order(
        manual_id, "SO-1", 140, planned_ship_at="2026-09-01"
    )
    self.stock_product(manual_id, 170)
    preview = self.post_preview("客户A", "ASM-100", 100)
    response = self.client.post("/admin/shipped-orders/assembly/new", data={
        "customer": "客户A",
        "assembly_drawing_no": "ASM-100",
        "set_quantity": "100",
        "manual_id": [str(preview["items"][0]["manual_id"])],
        "shipped_quantity": ["200"],
        "shipped_at": "2026-08-30",
        "logistics_no": "测试批次",
        "preview_token": preview["preview_token"],
        "confirm_warnings": "1",
    })
    self.assertEqual(response.status_code, 201)
    with app.get_db() as conn:
        allocations = conn.execute(
            "SELECT order_id, quantity FROM assembly_shipment_allocations ORDER BY id"
        ).fetchall()
        item = conn.execute("SELECT * FROM assembly_shipment_items").fetchone()
    self.assertEqual([(row["order_id"], row["quantity"]) for row in allocations], [(order_id, 140), (None, 60)])
    self.assertEqual(item["inventory_deducted_quantity"], 170)
    self.assertEqual(item["inventory_shortage_quantity"], 30)
```

- [ ] **Step 2: Run save tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblySaveTests -v`

Expected: FAIL because the save route and persistence are absent.

- [ ] **Step 3: Update order shipped summaries to include assembly allocations**

Change `order_shipment_summary_subquery()` to aggregate a `UNION ALL` of normal `product_order_shipments` and non-null `assembly_shipment_allocations`, using the parent batch `shipped_at`. Change `sync_order_shipment_summary()` to use the same source. Re-run existing ordinary order tests immediately.

Run: `.venv/bin/python -m unittest tests.test_inventory.InventoryTests.test_shipment_create_edit_and_delete_keep_inventory_in_sync -v`

Expected: PASS with unchanged normal shipment behavior.

- [ ] **Step 4: Implement shortage-tolerant inventory deduction dedicated to assembly items**

Iterate positive balances in default-location-first order. Deduct only `min(remaining, location quantity)`, write inventory transactions with `related_order_type="组装发货"` and `related_order_id=f"assembly-item:{item_id}"`, and return the total deducted. Do not weaken `validate_shipment_inventory()` or `deduct_inventory_for_shipment()` used by ordinary shipment routes.

- [ ] **Step 5: Implement atomic batch persistence**

Inside the route's existing `with get_db() as conn` transaction:

1. Parse the submitted manual IDs and quantities.
2. Rebuild the preview from current database state.
3. If its token differs, return HTTP 409 JSON with the new preview and save nothing.
4. If warnings exist and `confirm_warnings != "1"`, return HTTP 409 JSON and save nothing.
5. Insert batch, each item snapshot, each order/direct allocation, inventory transactions, and update order summaries.
6. Return HTTP 201 JSON with `batch_id` and `redirect_url`.

- [ ] **Step 6: Add rollback and stale-preview tests**

Patch `save_assembly_shipment()` at the allocation-insert boundary to raise `sqlite3.DatabaseError`; assert zero batch rows, unchanged order summaries, and unchanged inventory. Add a test that changes stock after preview and expects HTTP 409 plus the new shortage warning.

- [ ] **Step 7: Run focused and regression tests, then checkpoint**

Run:

```bash
.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblySaveTests -v
.venv/bin/python -m unittest tests.test_inventory.InventoryTests.test_shipment_create_edit_and_delete_keep_inventory_in_sync -v
```

Expected: PASS. If Git is available, commit with message `feat: save assembly shipments atomically`.

---

### Task 6: 组装发货表单、动态预览与警告确认

**Files:**
- Create: `templates/assembly_shipping_form.html`
- Modify: `templates/shipped_orders.html:340-550`
- Modify: `static/assembly_shipping.js`
- Modify: `static/style.css`
- Modify: `app.py:8815-8846` (`shipped_orders` context)
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Consumes APIs from Task 4 and save route from Task 5
- Produces DOM hooks: `data-assembly-shipment-form`, `data-assembly-customer`, `data-assembly-drawing`, `data-assembly-set-quantity`, `data-assembly-preview`, `data-assembly-warning-summary`

- [ ] **Step 1: Write failing HTML contract tests**

```python
def test_shipped_orders_page_contains_order_and_assembly_modes(self):
    html = self.client.get("/admin/shipped-orders").get_data(as_text=True)
    self.assertIn("按订单发货", html)
    self.assertIn("按组装件发货", html)
    self.assertIn("data-assembly-shipment-form", html)
    self.assertIn("data-assembly-warning-summary", html)
    self.assertIn("assembly_shipping.js", html)
```

- [ ] **Step 2: Run HTML tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyShippingPageTests -v`

Expected: FAIL because the assembly form is absent.

- [ ] **Step 3: Build the form and mode switch**

Keep the existing ordinary form markup intact inside its mode panel. Add a second panel containing customer, assembly drawing, set quantity, date, image input, remark, preview table, warning summary and submit button. The server passes customer options from both product customers and order customers, de-duplicated case-insensitively.

- [ ] **Step 4: Implement browser state flow in `static/assembly_shipping.js`**

Implement these named functions so behavior can be inspected independently:

```javascript
async function loadAssemblyOptions(customer) {
  const response = await fetch(`/admin/shipped-orders/assembly-options?customer=${encodeURIComponent(customer)}`);
  if (!response.ok) throw new Error((await response.json()).error);
  return (await response.json()).assembly_drawing_numbers;
}

async function requestAssemblyPreview(form) {
  const payload = {
    customer: form.querySelector("[data-assembly-customer]").value,
    assembly_drawing_no: form.querySelector("[data-assembly-drawing]").value,
    set_quantity: form.querySelector("[data-assembly-set-quantity]").value,
    overrides: Object.fromEntries(
      Array.from(form.querySelectorAll("[data-assembly-item-quantity]"))
        .map((input) => [input.dataset.manualId, input.value])
    ),
  };
  const response = await fetch("/admin/shipped-orders/assembly-preview", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error);
  return body;
}

function renderAssemblyWarnings(container, warnings) {
  container.replaceChildren(...warnings.map((warning) => {
    const item = document.createElement("p");
    item.className = "assembly-warning";
    item.textContent = warning.message;
    return item;
  }));
}

async function submitAssemblyShipment(form, preview, confirmed) {
  const payload = new FormData(form);
  payload.set("preview_token", preview.preview_token);
  payload.set("confirm_warnings", confirmed ? "1" : "0");
  const response = await fetch(form.action, {method: "POST", body: payload});
  return {status: response.status, body: await response.json()};
}
```

`renderAssemblyPreview(preview)` must rebuild the preview body with DOM APIs (not HTML string concatenation), create one number input per item with `data-assembly-item-quantity` and `data-manual-id`, and render order allocation labels from the server response.

`requestAssemblyPreview()` sends customer, assembly number, set quantity and current per-product overrides. Changing customer clears assembly and preview; changing assembly or set quantity refreshes preview; editing a quantity updates overrides and requests a fresh preview after a short debounce. Submission shows the exact warning messages, uses `window.confirm`, then resubmits with the current token and `confirm_warnings=1`. HTTP 409 replaces the preview and requires a fresh confirmation; HTTP 201 navigates to `redirect_url`.

- [ ] **Step 5: Add accessible status and responsive styling**

Use an `aria-live="polite"` status area, table labels for desktop, stacked item cards below 760px, `.assembly-warning` for no-order/shortage messages, and disabled/loading button states. Do not use color as the sole warning indicator.

- [ ] **Step 6: Run page and API tests, then checkpoint**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyShippingPageTests tests.test_assembly_shipping.AssemblyPreviewTests -v`

Expected: PASS. If Git is available, commit with message `feat: add assembly shipment workflow UI`.

---

### Task 7: 历史展示、批次图片、修改与删除

**Files:**
- Modify: `app.py` near `fetch_shipped_orders()`, shipment image helpers, and shipment edit/delete routes
- Modify: `templates/shipped_orders.html`
- Create: `templates/assembly_shipment_edit.html`
- Modify: `static/assembly_shipping.js`
- Modify: `static/style.css`
- Test: `tests/test_assembly_shipping.py`

**Interfaces:**
- Produces: `fetch_assembly_shipment_batches(conn, query="", selected_customer="", shipped_at="") -> list[dict]`
- Produces: `save_assembly_shipment_images(conn, batch_id, files) -> list[dict]`
- Produces: `reverse_assembly_shipment_batch(conn, batch_id) -> set[int]`
- Produces routes: `GET|POST /admin/shipped-orders/assembly/<int:batch_id>/edit`, `POST /admin/shipped-orders/assembly/<int:batch_id>/delete`

- [ ] **Step 1: Write failing history rendering tests**

Create a confirmed batch and assert the history contains `ASM-100`, `100 套`, both component drawing numbers, matched order numbers, “无订单直接发货”, and “库存缺口 30”. Assert customer/query/date filters include or exclude the batch correctly.

- [ ] **Step 2: Run history tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyHistoryTests -v`

Expected: FAIL because batches are not fetched or rendered.

- [ ] **Step 3: Implement batch fetch and grouped history rendering**

Fetch batches first, then attach ordered items, allocations and images with one parameterized `IN` query per child table, using exactly one `?` placeholder per selected parent ID. Never issue one query per row. Add a grouped history section before ordinary shipment rows and apply the existing page filters to batch customer, assembly number, component drawing/name and shipped date.

- [ ] **Step 4: Write failing edit/delete synchronization tests**

Test that editing 200 down to 120 restores prior inventory deductions, reallocates current open orders, recomputes shortage, and does not double-count the batch's own old allocations. Test that deleting restores only `inventory_deducted_quantity`, removes allocations, updates every affected order summary, and removes database image metadata.

- [ ] **Step 5: Implement reverse, edit preview and transactional replacement**

`reverse_assembly_shipment_batch()` restores inventory transactions identified by each item ID, deletes those transactions, collects affected order IDs, and deletes allocations/items only after the caller has loaded the immutable history needed for re-preview. Edit preview passes `exclude_batch_id` so the batch's own allocations and deductions are treated as available. Final edit follows the same preview-token and warning confirmation protocol as create.

- [ ] **Step 6: Implement batch images and safe deletion**

Reuse existing image validation and filename generation, but store metadata in `assembly_shipment_images`. On database failure remove only files created by the failed request. Batch deletion removes only filenames selected from its own image table and validates each basename before unlinking.

- [ ] **Step 7: Run history/edit/delete tests and checkpoint**

Run: `.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyHistoryTests tests.test_assembly_shipping.AssemblyEditDeleteTests -v`

Expected: PASS. If Git is available, commit with message `feat: manage assembly shipment history`.

---

### Task 8: 全量回归、界面验证与交付检查

**Files:**
- Modify only files required to correct failures found by the commands below
- Test: `tests/test_assembly_shipping.py`
- Test: `tests/test_inventory.py`
- Test: `tests/test_inspection_reports.py`
- Test: `tests/test_pdf_preview.py`

**Interfaces:**
- Consumes all prior tasks
- Produces a verified implementation satisfying the spec acceptance scenario

- [ ] **Step 1: Run the complete automated suite**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`

Expected: all tests PASS with zero errors and zero failures.

- [ ] **Step 2: Run a fresh acceptance-scenario test by exact name**

Add or retain this integration test name in `tests/test_assembly_shipping.py`:

`AssemblyAcceptanceTests.test_100_sets_support_manual_override_fifo_direct_shipping_and_stock_floor`

Run:

`.venv/bin/python -m unittest tests.test_assembly_shipping.AssemblyAcceptanceTests.test_100_sets_support_manual_override_fifo_direct_shipping_and_stock_floor -v`

Expected: PASS and assert P1 defaults to 200, P2 defaults to 300 then saves 280, P1 splits across orders, P2 records no-order allocation, P2 stock 250 becomes zero, shortage 30 is retained, and no partial state remains after an injected failure.

- [ ] **Step 3: Start the local app for browser verification**

Run: `.venv/bin/python app.py`

Verify in the in-app browser at the configured local URL:

1. Edit a product and add/remove two组装关系 rows.
2. Open已发货订单, switch between both shipment modes, and confirm the ordinary form is unchanged.
3. Select customer and assembly, enter 100 sets, and verify calculated quantities update.
4. Modify one component quantity and verify preview/order/inventory statuses refresh.
5. Confirm warning text names the exact no-order and shortage quantities.
6. Check desktop and narrow mobile layouts for clipped controls or horizontal overflow.

- [ ] **Step 4: Inspect database invariants after the acceptance test**

Using the temporary test database inside the automated test, assert in code rather than opening the production database:

- each batch item allocation total equals `shipped_quantity`;
- inventory balances are never negative;
- `inventory_deducted_quantity + inventory_shortage_quantity == shipped_quantity`;
- ordinary plus assembly order shipment totals do not exceed order quantity;
- deleting the batch restores order totals and the exact deducted stock.

- [ ] **Step 5: Run the full suite again after any verification fix**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`

Expected: all tests PASS with zero errors and zero failures.

- [ ] **Step 6: Final checkpoint**

If Git is available, review `git diff --check`, then commit all remaining scoped files with message `feat: complete assembly BOM shipping workflow`. If Git remains unavailable, do not initialize it; report the exact test count, browser checks, changed files and the missing repository metadata to the user.
