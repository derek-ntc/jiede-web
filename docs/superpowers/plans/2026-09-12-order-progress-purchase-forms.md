# Order Progress and Purchase Forms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the five approved shipping, production order/follow-up, and purchasing improvements locally.

**Architecture:** Preserve all existing order/item IDs and ledger relationships. Group order lines for presentation, link new process cards to an exact order line, and keep legacy cards separate. Share recipient default resolution between server and browser inputs; retain supplier/document snapshots while changing purchase presentation.

**Tech Stack:** Flask/Jinja, SQLite, browser JavaScript, openpyxl, ReportLab, Python unittest and Node tests.

**Spec:** `docs/superpowers/specs/2026-09-12-order-progress-purchase-forms-design.md` (approved by user).

## Global Constraints

- 本轮先本地实施与验证，不部署 NAS/VPS，不发送邮件，不补写实际客户资料，不创建真实采购或生产订单。
- 数据库仅做幂等的增量迁移，旧 `order_id` 留空；不猜测历史对应关系，不清理历史孤立记录。
- 不自动入库、不自动发货、不据此修改库存余额。
- 金额列严格沿用采购价格权限：无权用户的页面数据、接口和导出都不含隐藏价格，不能只靠 CSS 隐藏。
- Work only in `/Users/derek/Documents/jiede-web/.worktrees/shared-products`, not the older main checkout. Python: `/Users/derek/Documents/jiede-web/.venv/bin/python`. Use temporary DB fixtures. Do not change live/local business data or user templates.
- Follow TDD; retain RED/GREEN evidence in task reports. Each task commits only its own application/tests/docs. User requested implementation, so continue through all tasks without repeated confirmation.

---

### Task 1: Resolve shipping recipient defaults consistently

**Files:** Modify `shipping_workflow.py`, `app.py` (`shipment_operations`), `static/order_shipping.js`, `static/assembly_shipping.js`, `templates/assembly_shipping_form.html`; create `tests/test_recipient_defaults.py`. Reuse existing shipping integration/JS fixtures where needed.

**Interfaces:** Add `shipping_workflow.customer_recipient_defaults(row) -> dict` returning `recipient_name`, `recipient_phone`, `address`, `recipient_source` (`recipient`, `contact`, `missing`). Existing `create_delivery_notes(...)` and page defaults consume it. Preserve customer ID separately.

- [x] Write unit and route regression tests before production changes. Start with a behavioral assertion:

```python
row = dict(contact='王工', phone='012345', recipient_name='', recipient_phone='', address='仓库')
actual = shipping_workflow.customer_recipient_defaults(row)
self.assertEqual((actual['recipient_name'], actual['recipient_phone']), ('王工', '012345'))
```

Also test explicit recipient precedence, one dedicated field missing (no mixed contact fallback), whitespace, absent customer, explicit empty overrides, multi-customer isolation, persisted note unchanged after customer edits, and DOM customer switching/manual edits surviving preview.

- [x] Run `/Users/derek/Documents/jiede-web/.venv/bin/python -W ignore::DeprecationWarning -m unittest tests.test_recipient_defaults -v`; record the expected missing-function/default behavior failure.
- [x] Implement a single resolver. Its core branch is:

```python
name = str(row.get('recipient_name') or '').strip()
phone = str(row.get('recipient_phone') or '').strip()
source = 'recipient' if name or phone else 'missing'
if not name and not phone:
    name = str(row.get('contact') or '').strip()
    phone = str(row.get('phone') or '').strip()
    source = 'contact' if name or phone else 'missing'
```

Convert sqlite rows to dict before use. Query contact/phone in both page and note creation. Keep explicit per-customer/assembly overrides last, including empty strings. Show contact-source warning or missing-field warning, without copying source metadata into customer records. Do not alter confirmation tokens or inventory behavior.
- [x] Run focused tests plus `tests.test_delivery_note_workflow`; inspect real DOM behavior tests for both modes. Run full unittest suite once before commit.
- [x] Commit scoped changes and report RED/GREEN, browser/default source checks, exact files and tests.

### Task 2: Order summary navigation and order-linked quantity progress

**Files:** Create `production_order_views.py` (read-only grouping/query), `order_production.py` (schema/progress/validation), `templates/order_groups.html`, `templates/order_group_detail.html`, `templates/order_production_detail.html`, `tests/test_order_production.py`, `tests/test_order_groups.py`. Modify `app.py` order and production routes, `production_processes.py` mutation guards, `templates/orders.html`, `templates/production_followups.html`, `templates/production_process_card.html`, and scoped business CSS. Split route registration into `order_production_routes.py` if needed to keep `app.py` additions narrow; no unrelated refactor.

**Interfaces:**

- `production_order_views.order_group_key(row) -> tuple` of effective customer, order_no, ordered_at, assembly_drawing_no, assembly_set_quantity.
- `production_order_views.group_order_rows(rows) -> list[dict]` returns anchor ID, rows, line_count, sets (not sum), dates and completion. `fetch_order_group(conn, anchor_id)` resolves exact full group, raising LookupError if anchor absent. Reuse existing shipment summary calculation instead of inventing shipped counts.
- `order_production.ensure_order_production_schema(conn)` adds nullable unique order_id, process completed_quantity/version, and append-only quantity audit records (without destructive cascade).
- `order_production.start_order_followup(conn, order_id, operator, now) -> int` obtains canonical order/product snapshot, creates once in transaction and calls existing process snapshot builder.
- `order_production.set_process_quantity(conn, followup_id, step_id, quantity, expected_version, operator, now)` validates ownership/current demand/version, updates quantity/status and audit atomically.
- `order_production.validate_order_production_change(conn, order_id, manual_id, quantity)` prevents swapping product with linked card and quantity below recorded completion; post-update recomputation normalizes completion flags.
- Routes: `/admin/orders/groups/<int:anchor_id>`; `/admin/production-followups/orders/<int:anchor_id>`; POST `/admin/production-followups/orders/<int:order_id>/start`; POST `/admin/production-followups/<int:followup_id>/processes/<int:step_id>/quantity`. Legacy cards accessible through `view=legacy` on existing follow-up page.

- [x] Write failing tests for grouping and persisted progress. Group fixture:

```python
rows = [dict(id=1, customer='A', order_no='SO1', ordered_at='2026-09-12', assembly_drawing_no='DZ30', assembly_set_quantity=100),
        dict(id=2, customer='A', order_no='SO1', ordered_at='2026-09-12', assembly_drawing_no='DZ30', assembly_set_quantity=100),
        dict(id=3, customer='B', order_no='SO1', ordered_at='2026-09-12', assembly_drawing_no='DZ30', assembly_set_quantity=100)]
self.assertEqual([group['line_count'] for group in group_order_rows(rows)], [2, 1])
self.assertEqual(group_order_rows(rows)[0]['assembly_set_quantity'], 100)
```

Exercise actual temporary DB and Flask routes: same product across orders/cards stays independent; old cards remain order_id NULL; no GET writes; duplicate start creates one card; 80/200 gives 40%; repeated 80 remains 80; invalid -1, fraction, >200 and stale versions rejected without audit/quantity changes; cross-card step IDs forbidden; order quantity 50 rejected after completion80; demand increase changes progress/status without changing80; deletion protection survives resetting quantities to0; process removal confirmation and audit retention; ordinary multi-line groups and matching-product keyword show entire group; changed anchor and customer not trusted; existing permissions/CSRF and filters preserved.

- [x] Run `python -W ignore::DeprecationWarning -m unittest tests.test_order_groups tests.test_order_production -v` with the Python path in Global Constraints; record expected failures before implementation.
- [x] Implement grouping as a presentation layer, not a migration that rewrites orders:

```python
groups = {}
for row in rows:
    key = order_group_key(row)
    groups.setdefault(key, []).append(row)
```

Filter by matched groups, then load all group members. Preserve ordering/sort/filter state and existing order detail actions. Use anchor IDs resolved on server. List ordinary orders by product summary/count, assembly orders by drawing/sets; dates span earliest/latest and all rows must be shipped for completed status. Do not sum mixed units. Keep old list as a reusable detail partial to preserve actions.
- [x] Add idempotent schema and quantity mutation service. Use `BEGIN IMMEDIATE`/existing savepoint conventions with unique non-null order_id. Quantities validate decimal digit integer strings and cap at current demand. Mutation SQL must compare version:

```sql
UPDATE production_followup_process_steps
SET completed_quantity=?, version=version+1, completed_at=?, completed_by=?, updated_at=?
WHERE id=? AND followup_id=? AND version=?
```

Reject zero changed rows as conflict. Audit contains followup/order/step identity, name snapshot, before/after quantity, user/time and event, retained if a confirmed step is deleted. Start uses existing product process snapshot/remark defaults. Do not alter legacy unlinked card statuses or infer old quantities. Linked card complete/revert endpoints must funnel through new quantity mutation; existing step deletion and whole-card deletion endpoints must enforce spec guards, not only the new page. Link identity immutable; customer changes on linked card must not diverge from order.
- [x] Build order-first follow-up pages with real forms for quantities, explicit begin tracking, add/delete/move/remark controls, version and CSRF fields; empty process message. Summary process percent is sum(qty)/(demand*step_count), never inventory. Process card shows order identity, demand, per-process numbers and remarks. Old cards use existing layout through legacy tab. Registered routes must use existing permission names and existing menu links must lead to new default summary.
- [x] Cover every order editing/import/update path that can modify demand or manual_id with production guards; guard order/product deletion paths that could orphan new order links. Enforce new guards transactionally. Preserve all pre-existing finance, inventory and shipping protections.
- [x] Run focused new tests plus production process/customer/order/inventory/shipping regressions; full suite once. Verify browser navigation and progress with temporary fixture DB only. Commit and provide report with migration preservation, RED/GREEN and all commands/results.

### Task 3: Purchase order-style forms and template-based Excel

**Files:** Modify `procurement.py`, `procurement_documents.py`, `app.py` procurement form response/exports, `templates/purchase_order_form.html`, `templates/purchase_order_detail.html`, `static/purchase-orders.js`, `static/purchase-orders.css`, `tests/test_purchase_orders.py`, `tests/test_purchase_order_exports.py`, `tests/js/purchase-orders.test.js`. Create `tests/test_purchase_template_layout.py`. Existing `other` purchasing behavior must remain supported.

**Interfaces:** Keep `build_purchase_order_workbook(order, items, *, include_prices) -> BytesIO` and `purchase_document_view(...)`. Extend display model with structured supplier/header/delivery fields while retaining current PDF fields. Use supplier snapshot from saved order, current supplier data only for newly selected supplier. Page default JSON whitelists supplier id/code/name/contact/phone/email/address (no bank fields or prices).

- [x] Read user template with spreadsheet skill: `/Users/derek/Desktop/原材料采购模板.xlsx`. Do not edit it or import its example data. Add workbook behavior tests using openpyxl:

```python
sheet = load_workbook(build_purchase_order_workbook(order, items, include_prices=False)).active
self.assertEqual(sheet.page_setup.orientation, 'landscape')
self.assertEqual(sheet.page_setup.paperSize, sheet.PAPERSIZE_A4)
values = [cell.value for row in sheet for cell in row]
self.assertIn('物品名称', values)
self.assertIn('单位', values)
self.assertIn('镀锌板', values)
```

Also assert names/units for all categories, print area includes company row and footer, row/header repetition, numeric/date vs text safe values and leading zeros; long remark rows; no price leakage; supplier change populates visible data and switching back/edit error preserves intended snapshot; existing order old supplier snapshot remains unchanged after supplier edits; receipt/item identities unaffected.
- [x] Run focused tests and record RED before implementation.
- [x] Reshape form/detail into template zones: company/title; supplier+code/contact/phone/address/orderNo; items; receiving address/recipient/phone; remarks; purchased date/operator; save/export. Raw columns in exact order: item_name,material,length,width,thickness,surface,quantity,unit,expected_at,remark. Carton retains height/dimension_text/prices; outsourcing retains drawing/dimensions/thickness/surface/prices. Add amount display only with price permission, preserve integer-cent arithmetic and missing-price semantics.
- [x] Feed supplier JSON and JS selection handler without trusting submitted snapshot fields. Existing supplier display must use saved header on edit until user switches supplier. Current backend snapshot refresh behavior must match that contract on save; no automatic historical refresh.
- [x] Build Excel template-shaped header/footer with dynamic item rows, merge cells, black borders, readable fonts, landscape A4 `fitToWidth=1`, `fitToHeight=0`, print area from first company row through last footer, repeated item header, numeric/date cells and text-forced strings. Do not hardcode template customers/products/people; derive company metadata from current profile, recipient from saved order, dates from actual order, operator from created_by. Existing PDF must gain missing raw item_name/unit fields but no wholesale PDF restyling needed.
- [x] Run focused Python and Node tests and full suite once. Render/inspect short and multi-page Excel/PDF outputs in temp directory, verify no cropping/hidden company row, dates or contacts. Commit scoped changes and report artifacts/test paths and concerns.

## Final integration

- [x] Independent task reviews after each deliverable, fix important findings with focused tests.
- [x] Full regression with `/Users/derek/Documents/jiede-web/.venv/bin/python -W ignore::DeprecationWarning -m unittest discover -s tests`, plus targeted Node tests using available Node runtime.
- [x] Browser check temporary-data instance only; do not use customer database for test mutations.
- [x] Whole-change review from starting commit `ba9caf1`; document local usage and test outcome, no push/deployment without new request.
