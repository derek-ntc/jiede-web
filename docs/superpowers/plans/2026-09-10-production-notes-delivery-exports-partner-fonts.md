# Production Notes Delivery Exports and Partner Font Size Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-step notes, price-free PDF/XLSX/DOCX delivery-note exports, and larger business-partner page type.

**Architecture:** Extend persisted process steps with local notes. Normalize every persisted delivery note into one price-free export payload consumed by PDF, XLSX, and DOCX builders. Scope typography through a business-partner wrapper rather than global CSS.

**Tech Stack:** Flask, SQLite, Jinja, unittest, ReportLab, openpyxl, python-docx.

**Spec:** `docs/superpowers/specs/2026-09-10-production-notes-delivery-exports-partner-fonts.md`

## Global Constraints

- `production_followup_process_steps.remark` is `TEXT NOT NULL DEFAULT ''`; trim and cap at 1,000 characters.
- Keep delivery-note exports price-free and snapshot-based.
- Require existing `production_followups_manage` and CSRF protections for note updates.
- Require `shipped_view`; preserve 404/410 delivery-note behavior.
- XLSX and DOCX are editable landscape A4 files.

---

### Task 1: Production step remarks

**Files:**
- Modify: `production_processes.py`, `app.py`, `templates/production_followups.html`, `templates/production_process_card.html`, `static/style.css`
- Test: `tests/test_production_processes.py`, `tests/test_production_followup_customers.py`

**Interfaces:**
- Produce `update_followup_process_step_remark(conn, followup_id, step_id, remark, *, now=None) -> list[dict]`.
- Produce `POST /admin/production-followups/<int:followup_id>/processes/<int:step_id>/remark`.

- [ ] **Step 1: Write the failing model test**

```python
card = production_processes.create_followup_process_snapshot(conn, followup_id)
saved = production_processes.update_followup_process_step_remark(
    conn, followup_id, card[0]['id'], '压铆用4-1或4-2的压铆螺母，注意方向'
)
self.assertEqual(saved[0]['remark'], '压铆用4-1或4-2的压铆螺母，注意方向')
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m unittest tests.test_production_processes -v`

Expected: FAIL because the note operation does not exist.

- [ ] **Step 3: Implement the schema and operation**

Add the idempotent `remark` migration, include it in newly inserted snapshots and manual steps, and update only the matching card step inside a savepoint. Trim values, reject values longer than 1,000 characters, and update the follow-up timestamp.

- [ ] **Step 4: Write route/render tests, implement the authorized route/UI/card column, and run GREEN**

Post a note with the existing CSRF token and assert the success flash plus displayed note. Assert that the print-card HTML contains the `备注` column and note. Run `.venv/bin/python -m unittest tests.test_production_processes tests.test_production_followup_customers -v`; expect PASS and unchanged completion/reorder/deletion assertions.

- [ ] **Step 5: Commit**

Run `git add production_processes.py app.py templates/production_followups.html templates/production_process_card.html static/style.css tests/test_production_processes.py tests/test_production_followup_customers.py` then `git commit -m 'feat: add production process remarks'`.

### Task 2: Shared persisted delivery-note export payload

**Files:**
- Modify: `app.py`
- Test: create `tests/test_delivery_note_exports.py`; modify `tests/test_shipping_workflow_fields.py` only if existing fixtures need shared coverage.

**Interfaces:**
- Produce `delivery_note_export_payload(note: dict) -> dict` containing only document metadata and non-financial saved item fields.

- [ ] **Step 1: Write the failing payload test**

```python
payload = app.delivery_note_export_payload(note)
self.assertEqual(payload['items'][0]['shipped_quantity'], 0)
self.assertEqual(payload['items'][0]['remark'], '暂不发货')
self.assertNotIn('price', repr(payload).lower())
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m unittest tests.test_delivery_note_exports -v`

Expected: FAIL because the projection function does not exist.

- [ ] **Step 3: Implement projection and PDF adapter**

Build the projection exclusively from `load_delivery_note` data. Refactor the existing PDF download route to use an adapter that preserves its URL, download filename, permission, 404 response, and invalidated-note 410 response.

- [ ] **Step 4: Run GREEN and commit**

Run `.venv/bin/python -m unittest tests.test_shipping_workflow_fields tests.test_delivery_note_exports -v` expecting PASS. Then run `git add app.py tests/test_shipping_workflow_fields.py tests/test_delivery_note_exports.py` and `git commit -m 'refactor: share delivery note export payload'`.

### Task 3: Editable XLSX and DOCX delivery-note exports

**Files:**
- Modify: `requirements.txt`, `app.py`, `templates/delivery_note_result.html`
- Test: `tests/test_delivery_note_exports.py`

**Interfaces:**
- Produce `build_delivery_note_xlsx(payload: dict) -> BytesIO` and `build_delivery_note_docx(payload: dict) -> BytesIO`.
- Produce `GET /admin/delivery-notes/<int:note_id>.xlsx` and `.docx`.

- [ ] **Step 1: Write failing format tests**

```python
xlsx = client.get(f'/admin/delivery-notes/{note_id}.xlsx')
self.assertEqual(xlsx.status_code, 200)
workbook = load_workbook(BytesIO(xlsx.data))
self.assertEqual(workbook.active.page_setup.orientation, 'landscape')
docx = client.get(f'/admin/delivery-notes/{note_id}.docx')
self.assertEqual(docx.status_code, 200)
```

Also open the DOCX ZIP and assert its `word/document.xml` contains a zero-quantity row remark and has no `单价` value.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m unittest tests.test_delivery_note_exports -v`

Expected: FAIL with missing XLSX and DOCX routes.

- [ ] **Step 3: Implement builders, protected routes, and controls**

Pin `python-docx` in `requirements.txt`. Create an `openpyxl` A4-landscape worksheet and a python-docx A4-landscape document with title, recipient metadata, and identical price-free columns: 序号, 产品图号, 产品名称, 规格型号, 单位, 数量, 订单号, 发货时间, 备注. Reuse the PDF validity guard for both routes and render three export buttons per valid result-row.

- [ ] **Step 4: Run GREEN and inspect the DOCX**

Run `.venv/bin/python -m unittest tests.test_shipping_workflow_fields tests.test_delivery_note_exports -v`; expect PASS, including guest redirect and invalidated 410 tests for all formats. Render a generated DOCX using bundled `render_docx.py` and inspect every PNG at 100% zoom for readable landscape A4 tables.

- [ ] **Step 5: Commit**

Run `git add requirements.txt app.py templates/delivery_note_result.html tests/test_delivery_note_exports.py` then `git commit -m 'feat: export delivery notes as excel and word'`.

### Task 4: Scoped business-partner readability

**Files:**
- Modify: `templates/customers.html`, `templates/suppliers.html`, `templates/common_info.html`, `static/style.css`
- Test: create `tests/test_business_partner_readability.py`

**Interfaces:**
- Produce a `business-partner-page` wrapper and only `.business-partner-page` typography rules.

- [ ] **Step 1: Write the failing scope test**

```python
for endpoint in ('/admin/customers', '/admin/business-partners/suppliers', '/admin/common-info'):
    self.assertIn('business-partner-page', client.get(endpoint).get_data(as_text=True))
self.assertIn('.business-partner-page table', Path('static/style.css').read_text())
```

- [ ] **Step 2: Run RED, implement, and run GREEN**

Run `.venv/bin/python -m unittest tests.test_business_partner_readability -v` expecting failure. Add template wrappers and scoped CSS for 16px controls/cells/buttons, 17px headers, and a 48px minimum cell height. Rerun the same test and `tests.test_suppliers`; expect PASS.

- [ ] **Step 3: Commit**

Run `git add templates/customers.html templates/suppliers.html templates/common_info.html static/style.css tests/test_business_partner_readability.py` then `git commit -m 'style: enlarge business partner information pages'`.

### Task 5: Release verification

**Files:** none unless a focused regression failure requires a minimal fix.

- [ ] **Step 1: Run full automated verification**

Run: `.venv/bin/python -m unittest discover -s tests -q` and `npm test`.

Expected: all configured tests pass; record only explicitly skipped optional tests.

- [ ] **Step 2: Manually verify the isolated local UI**

Save a process note, print-preview its card, export one ordinary and one assembly note in every format, and confirm the three partner pages enlarge while purchasing pages retain their existing size.

- [ ] **Step 3: Commit only a verified final fix, if any**

Run `git status --short`. If it is empty, do not create an empty commit. Otherwise, stage only the verified source/test files and commit with a precise message.

