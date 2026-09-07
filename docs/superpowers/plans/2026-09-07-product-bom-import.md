# 产品组装清单批量导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 直接解析按组装件分区的 Excel 物料清单，预览后批量创建或匹配产品，并正确建立共享零件的多组装件用量关系。

**Architecture:** 在现有 Flask 单体应用的订单 Excel 解析帮助函数附近增加产品组装清单解析、导入规划和事务写入函数；解析结果先经过签名令牌送到确认页，确认路由重新检查数据库再写入。沿用 `manuals` 和 `product_assembly_components`，只为产品增加 `unit` 基本字段，不引入组装件产品或临时上传文件。

**Tech Stack:** Python 3、Flask、SQLite、Jinja2、openpyxl、itsdangerous、unittest

**Spec:** `docs/superpowers/specs/2026-09-07-product-bom-import-design.md`

## Global Constraints

- 输入为活动工作表中连续排列的“组装件…”多区块 Excel；不解析图片，不依赖单元格颜色。
- “规格型号”映射产品图号，“物料编码”映射 SKU，“物料名称”映射产品名称，“单位”映射新增的产品单位。
- 本次导入客户为“宁波知了智能科技有限公司”，页面仍须允许选择其他已有客户。
- 产品按“客户 + 产品图号”不区分大小写匹配；同一客户存在多个匹配产品时拒绝整批导入。
- 共用零件只保留一条产品记录，并保存多个组装关系及各自每套数量。
- 已有附件、价格、技术资料和本次文件未涉及的组装关系不得覆盖或删除。
- 用户确认前不得修改数据库；确认写入必须为单一事务。
- 只有拥有 `product_create` 权限的账号可以预览和确认导入。

## File Structure

- Create `tests/test_product_bom_import.py`: Excel 多区块解析、冲突校验、规划、确认写入、权限和页面行为测试。
- Create `templates/product_bom_import.html`: 客户和 Excel 上传表单、校验错误及预览摘要/明细、确认按钮。
- Modify `app.py`: `unit` 幂等迁移、Excel 解析、导入规划与事务写入、签名令牌、预览和确认路由。
- Modify `templates/index.html`: 产品列表导入入口和单位列。
- Modify `templates/edit.html`: 产品基本信息单位字段。
- Modify `static/product-list.css`: 为新增单位列和导入按钮保持紧凑布局。

---

### Task 1: Excel 多区块解析

**Files:**
- Create: `tests/test_product_bom_import.py`
- Modify: `app.py`（订单导入解析函数附近）

**Interfaces:**
- Produces: `parse_product_bom_workbook(upload) -> tuple[list[dict], list[str]]`
- 每个结果字典包含 `row_number`, `assembly_drawing_no`, `sku`, `product_name`, `drawing_no`, `unit`, `quantity_per_set`。

- [ ] **Step 1: 写入解析共享零件和专用零件的失败测试**

```python
def test_parser_reads_repeated_blocks_and_does_not_use_fill_colors(self):
    upload = workbook_upload([
        ("组装件DZ-30",),
        ("物料编码", "物料名称", "规格型号", "单位", "每套数量"),
        ("21.12.015.00074", "后盖", "DZ-30-01-002T2S2", "PCS", 1),
        ("21.12.015.00139", "右前面板", "DZ-30-01-006T5", "PCS", 1),
        (),
        ("组装件DZ-31",),
        ("物料编码", "物料名称", "规格型号", "单位", "每套数量"),
        ("21.12.015.00074", "后盖", "DZ-30-01-002T2S2", "PCS", 1),
    ])
    rows, errors = app.parse_product_bom_workbook(upload)
    self.assertEqual(errors, [])
    self.assertEqual([row["assembly_drawing_no"] for row in rows], ["DZ-30", "DZ-30", "DZ-31"])
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomParserTests -v`

Expected: FAIL because `parse_product_bom_workbook` does not exist.

- [ ] **Step 3: 实现最小解析器**

```python
def parse_product_bom_workbook(upload):
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".xlsx", ".xlsm"}:
        return [], ["请上传 .xlsx 或 .xlsm 格式的 Excel 文件"]
    try:
        workbook = load_workbook(upload, read_only=True, data_only=True)
    except (InvalidFileException, OSError, ValueError):
        return [], ["Excel 文件无法读取，请确认文件未损坏"]
    current_assembly = ""
    columns = None
    rows, errors = [], []
    required = {
        "物料编码": "sku", "物料名称": "product_name", "规格型号": "drawing_no",
        "单位": "unit", "每套数量": "quantity_per_set",
    }
    for row_number, values in enumerate(workbook.active.iter_rows(values_only=True), 1):
        cells = [normalize_import_drawing(value) for value in values]
        first = next((cell for cell in cells if cell), "")
        if first.startswith("组装件"):
            current_assembly = first[len("组装件"):].strip(" ：:").strip()
            columns = None
            continue
        normalized = [normalize_import_header(cell) for cell in cells]
        if current_assembly and all(header in cells for header in required):
            columns = {header: cells.index(header) for header in required}
            continue
        if not current_assembly or columns is None or not any(cells[:5]):
            continue
        item = {
            target: cells[columns[header]] if columns[header] < len(cells) else ""
            for header, target in required.items()
            if target != "quantity_per_set"
        }
        if not item["drawing_no"] or not item["product_name"]:
            errors.append(f"第 {row_number} 行缺少规格型号或物料名称")
            continue
        try:
            quantity = parse_import_quantity(values[columns["每套数量"]])
        except (TypeError, ValueError, OverflowError):
            errors.append(f"第 {row_number} 行每套数量必须是大于 0 的整数")
            continue
        rows.append({
            "row_number": row_number,
            "assembly_drawing_no": current_assembly,
            **item,
            "quantity_per_set": quantity,
        })
    if not rows and not errors:
        errors.append("没有找到可导入的组装清单")
    return rows, errors
```

- [ ] **Step 4: 增加并运行错误输入测试**

```python
def test_parser_reports_missing_headers_and_invalid_quantities(self):
    upload = workbook_upload([
        ("组装件DZ-30",),
        ("物料编码", "物料名称", "规格型号", "单位", "每套数量"),
        ("21.12.015.1", "右前面板", "DZ-30-01-006T5", "PCS", 0),
    ])
    rows, errors = app.parse_product_bom_workbook(upload)
    self.assertEqual(rows, [])
    self.assertIn("第 3 行每套数量必须是大于 0 的整数", errors)
```

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomParserTests -v`

Expected: PASS.

### Task 2: 产品单位迁移和无副作用导入规划

**Files:**
- Modify: `app.py`（`ensure_columns`、产品查询和导入帮助函数）
- Test: `tests/test_product_bom_import.py`

**Interfaces:**
- Produces: `build_product_bom_import_plan(conn, customer, rows) -> dict`
- Plan contains `customer`, deduplicated `products`, `relationships`, `new_product_count`, `matched_product_count`, `new_relationship_count`, `updated_relationship_count`, and `errors`.

- [ ] **Step 1: 写入单位迁移和共享零件规划失败测试**

```python
def test_plan_deduplicates_shared_part_and_keeps_two_assembly_relationships(self):
    rows = [
        bom_row(3, "DZ-30", "MAT-1", "后盖", "PART-1", "PCS", 1),
        bom_row(9, "DZ-31", "MAT-1", "后盖", "PART-1", "PCS", 1),
    ]
    with app.get_db() as conn:
        plan = app.build_product_bom_import_plan(conn, "宁波知了智能科技有限公司", rows)
    self.assertEqual(plan["new_product_count"], 1)
    self.assertEqual(len(plan["products"]), 1)
    self.assertEqual(
        [(item["assembly_drawing_no"], item["quantity_per_set"]) for item in plan["relationships"]],
        [("DZ-30", 1), ("DZ-31", 1)],
    )
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomPlanTests -v`

Expected: FAIL because the `unit` column and planning function do not exist.

- [ ] **Step 3: 增加幂等单位字段并实现规划函数**

Add to `ensure_columns`:

```python
"unit": "ALTER TABLE manuals ADD COLUMN unit TEXT NOT NULL DEFAULT ''",
```

```python
def build_product_bom_import_plan(conn, customer, rows):
    customer = str(customer or "").strip()
    products_by_key, relationships_by_key, errors = {}, {}, []
    if not customer:
        errors.append("请选择客户")
    for row in rows:
        product_key = row["drawing_no"].casefold()
        existing = products_by_key.get(product_key)
        basics = {key: row[key] for key in ("drawing_no", "product_name", "sku", "unit")}
        if existing and any(existing[key] != basics[key] for key in ("product_name", "sku", "unit")):
            errors.append(f"产品图号 {row['drawing_no']} 的基本信息不一致")
            continue
        products_by_key.setdefault(
            product_key, {**basics, "product_key": product_key, "source_rows": []}
        )["source_rows"].append(row["row_number"])
        relation_key = (product_key, row["assembly_drawing_no"].casefold())
        prior = relationships_by_key.get(relation_key)
        if prior and prior["quantity_per_set"] != row["quantity_per_set"]:
            errors.append(f"产品图号 {row['drawing_no']} 在组装件 {row['assembly_drawing_no']} 的每套数量不一致")
            continue
        relationships_by_key.setdefault(relation_key, {
            "product_key": product_key,
            "assembly_drawing_no": row["assembly_drawing_no"],
            "quantity_per_set": row["quantity_per_set"],
        })
    existing_by_key = {}
    for manual in conn.execute(
        "SELECT id, drawing_no FROM manuals WHERE customer = ? COLLATE NOCASE",
        (customer,),
    ).fetchall():
        existing_by_key.setdefault(manual["drawing_no"].strip().casefold(), []).append(manual)
    matched_ids = []
    for product_key, product in products_by_key.items():
        matches = existing_by_key.get(product_key, [])
        if len(matches) > 1:
            errors.append(f"产品图号 {product['drawing_no']} 在该客户下存在多条产品记录")
        elif matches:
            product["manual_id"] = matches[0]["id"]
            matched_ids.append(matches[0]["id"])
        else:
            product["manual_id"] = None
    existing_links = set()
    if matched_ids:
        placeholders = ",".join("?" for _ in matched_ids)
        for link in conn.execute(
            f"SELECT manual_id, assembly_drawing_no FROM product_assembly_components WHERE manual_id IN ({placeholders})",
            matched_ids,
        ).fetchall():
            existing_links.add((link["manual_id"], link["assembly_drawing_no"].casefold()))
    relationships = list(relationships_by_key.values())
    for relation in relationships:
        manual_id = products_by_key[relation["product_key"]]["manual_id"]
        relation["manual_id"] = manual_id
        relation["exists"] = bool(
            manual_id and (manual_id, relation["assembly_drawing_no"].casefold()) in existing_links
        )
    products = list(products_by_key.values())
    return {
        "customer": customer, "products": products,
        "relationships": relationships, "errors": errors,
        "new_product_count": sum(product["manual_id"] is None for product in products),
        "matched_product_count": sum(product["manual_id"] is not None for product in products),
        "new_relationship_count": sum(not relation["exists"] for relation in relationships),
        "updated_relationship_count": sum(relation["exists"] for relation in relationships),
    }
```

- [ ] **Step 4: 增加冲突和已有产品测试并运行**

```python
def test_plan_rejects_conflicting_shared_part_basics(self):
    rows = [
        bom_row(3, "DZ-30", "MAT-1", "后盖", "PART-1", "PCS", 1),
        bom_row(9, "DZ-31", "MAT-2", "后盖", "PART-1", "PCS", 1),
    ]
    with app.get_db() as conn:
        plan = app.build_product_bom_import_plan(conn, "宁波知了智能科技有限公司", rows)
    self.assertIn("产品图号 PART-1 的物料编码不一致", plan["errors"])
```

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomPlanTests -v`

Expected: PASS and no database rows added by planning.

### Task 3: 确认后单事务写入

**Files:**
- Modify: `app.py`（导入帮助函数）
- Test: `tests/test_product_bom_import.py`

**Interfaces:**
- Produces: `apply_product_bom_import(conn, customer, rows, current_user, now) -> dict`
- Consumes a fresh plan; raises `ValueError` if the plan has errors; returns inserted/updated counts.

- [ ] **Step 1: 写入写入共享零件和保留既有关联的失败测试**

```python
def test_apply_creates_one_product_with_two_relationships_and_preserves_existing_data(self):
    with app.get_db() as conn:
        result = app.apply_product_bom_import(conn, CUSTOMER, SHARED_ROWS, "admin", NOW)
        products = conn.execute("SELECT drawing_no, sku, unit FROM manuals").fetchall()
        links = conn.execute(
            "SELECT assembly_drawing_no, quantity_per_set FROM product_assembly_components ORDER BY assembly_drawing_no"
        ).fetchall()
    self.assertEqual(result["new_product_count"], 1)
    self.assertEqual([tuple(row) for row in products], [("PART-1", "MAT-1", "PCS")])
    self.assertEqual([tuple(row) for row in links], [("DZ-30", 1), ("DZ-31", 1)])
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomApplyTests -v`

Expected: FAIL because `apply_product_bom_import` does not exist.

- [ ] **Step 3: 实现创建、基本字段更新和关系 upsert**

```python
def apply_product_bom_import(conn, customer, rows, current_user, now):
    plan = build_product_bom_import_plan(conn, customer, rows)
    if plan["errors"]:
        raise ValueError("\n".join(plan["errors"]))
    manual_ids = {}
    for product in plan["products"]:
        if product["manual_id"] is None:
            product["manual_id"] = conn.execute(
                """
                INSERT INTO manuals (
                    drawing_no, product_name, customer, sku, unit,
                    model, category, version, remark, filename, original_filename,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', '', ?, '', '', '', ?, ?, ?, ?)
                """,
                (
                    product["drawing_no"], product["product_name"], plan["customer"],
                    product["sku"], product["unit"], now, current_user,
                    current_user, now, now,
                ),
            ).lastrowid
            ensure_product_inventory_code(conn, product["manual_id"])
        else:
            conn.execute(
                """
                UPDATE manuals
                SET product_name = ?, sku = ?, unit = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    product["product_name"], product["sku"], product["unit"],
                    current_user, now, product["manual_id"],
                ),
            )
        manual_ids[product["product_key"]] = product["manual_id"]
    for sort_order, relation in enumerate(plan["relationships"]):
        manual_id = manual_ids[relation["product_key"]]
        existing = conn.execute(
            """
            SELECT id FROM product_assembly_components
            WHERE manual_id = ? AND assembly_drawing_no = ? COLLATE NOCASE
            """,
            (manual_id, relation["assembly_drawing_no"]),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE product_assembly_components
                SET assembly_drawing_no = ?, quantity_per_set = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    relation["assembly_drawing_no"], relation["quantity_per_set"],
                    now, existing["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO product_assembly_components (
                    manual_id, assembly_drawing_no, quantity_per_set, sort_order,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    manual_id, relation["assembly_drawing_no"],
                    relation["quantity_per_set"], sort_order, now, now,
                ),
            )
    return plan
```

- [ ] **Step 4: 增加回滚和保留附件/价格/其他组装件的测试**

```python
def test_apply_updates_basic_fields_without_overwriting_price_files_or_other_assemblies(self):
    existing_id = create_existing_product(
        drawing_no="PART-1", filename="drawing.pdf", unit_price_minor=1234
    )
    apply_rows = [bom_row(3, "DZ-30", "MAT-NEW", "新名称", "PART-1", "PCS", 2)]
    with app.get_db() as conn:
        app.apply_product_bom_import(conn, CUSTOMER, apply_rows, "admin", NOW)
        product = conn.execute("SELECT * FROM manuals WHERE id = ?", (existing_id,)).fetchone()
        other = conn.execute(
            "SELECT quantity_per_set FROM product_assembly_components WHERE manual_id = ? AND assembly_drawing_no = 'OLD-ASM'",
            (existing_id,),
        ).fetchone()
    self.assertEqual(product["filename"], "drawing.pdf")
    self.assertEqual(product["unit_price_minor"], 1234)
    self.assertIsNotNone(other)
```

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomApplyTests -v`

Expected: PASS.

### Task 4: 预览、签名确认和权限路由

**Files:**
- Create: `templates/product_bom_import.html`
- Modify: `app.py`（产品路由附近）
- Test: `tests/test_product_bom_import.py`

**Interfaces:**
- Produces routes `GET/POST /admin/products/import` and `POST /admin/products/import/confirm`.
- Produces a signed token with payload `{"customer": str, "rows": list[dict]}` using salt `product-bom-import`.

- [ ] **Step 1: 写入预览不落库和确认落库的失败测试**

```python
def test_upload_previews_without_writing_then_confirm_imports(self):
    preview = self.client.post(
        "/admin/products/import",
        data={"customer": CUSTOMER, "file": workbook_upload(SCREENSHOT_ROWS)},
        content_type="multipart/form-data",
    )
    self.assertEqual(preview.status_code, 200)
    with app.get_db() as conn:
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM manuals").fetchone()[0], 0)
    token = extract_hidden_value(preview.get_data(as_text=True), "import_token")
    confirmed = self.client.post("/admin/products/import/confirm", data={"import_token": token})
    self.assertEqual(confirmed.status_code, 302)
    with app.get_db() as conn:
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM manuals").fetchone()[0], 3)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomRouteTests -v`

Expected: FAIL with 404 because the routes do not exist.

- [ ] **Step 3: 实现上传预览、签名和确认路由**

Use `URLSafeSerializer(app.config["SECRET_KEY"], salt="product-bom-import")`. The preview route parses and plans without writes, renders errors when present, and only emits a token for a valid plan. The confirm route rejects `BadSignature`, rebuilds the plan inside `get_db()`, calls the apply function, flashes counts, and redirects to `products_index`.

- [ ] **Step 4: 增加无效令牌和权限测试并运行**

```python
def test_confirm_rejects_tampered_token_without_writing(self):
    response = self.client.post(
        "/admin/products/import/confirm", data={"import_token": "tampered"}, follow_redirects=True
    )
    self.assertIn("导入确认已失效", response.get_data(as_text=True))
    with app.get_db() as conn:
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM manuals").fetchone()[0], 0)
```

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomRouteTests -v`

Expected: PASS.

### Task 5: 产品页面入口和单位展示

**Files:**
- Modify: `templates/index.html`
- Modify: `templates/edit.html`
- Modify: `app.py`（产品编辑字段和查询）
- Modify: `static/product-list.css`
- Test: `tests/test_product_bom_import.py`

**Interfaces:**
- Product list exposes the import route to `can_create_products` users and renders each product unit.
- Product edit persists `request.form["unit"]` without changing price/technical permissions.

- [ ] **Step 1: 写入产品列表和编辑页失败测试**

```python
def test_product_pages_offer_import_and_show_editable_unit(self):
    listing = self.client.get("/admin/products")
    self.assertIn("Excel导入组装清单", listing.get_data(as_text=True))
    edit = self.client.get(f"/admin/{self.product_id}/edit")
    self.assertIn('name="unit"', edit.get_data(as_text=True))
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv/bin/python -m unittest tests.test_product_bom_import.ProductBomPageTests -v`

Expected: FAIL because the button and unit field are absent.

- [ ] **Step 3: 增加入口、单位表单和列表列并调整列宽**

Update the product query projection to include `manuals.unit`, add the import button beside “新增产品”, persist `unit` in create/edit SQL, add a compact “单位” table column, and keep the existing “所属组装件” details UI.

- [ ] **Step 4: 运行页面测试和完整回归**

Run: `.venv/bin/python -m unittest tests.test_product_bom_import tests.test_product_list_layout tests.test_product_batch_assembly -v`

Expected: PASS.

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests PASS with zero failures and zero errors.
