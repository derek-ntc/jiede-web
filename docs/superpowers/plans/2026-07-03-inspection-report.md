# Inspection Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add product-level inspection requirements and shipment-plan inspection reports.

**Architecture:** Keep the Flask single-file pattern used by the app. Add two SQLite tables, helper functions in `app.py`, template sections for product pages and shipment plans, and one print-focused report preview page.

**Tech Stack:** Flask, SQLite, Jinja templates, plain JavaScript, pytest-style tests using temporary SQLite databases.

---

### Task 1: Data And Helpers

**Files:**
- Modify: `app.py`
- Create: `tests/test_inspection_reports.py`

- [ ] Add `ensure_inspection_tables(conn)` and call it from `init_db()`.
- [ ] Add helpers to fetch, save, and group product inspection requirements.
- [ ] Add a helper that builds inspection report rows for a shipment plan.
- [ ] Add tests for saving requirements and grouping plan report rows.

### Task 2: Product Pages

**Files:**
- Modify: `app.py`
- Modify: `templates/detail.html`
- Modify: `templates/edit.html`
- Modify: `templates/admin.html`
- Modify: `static/style.css`

- [ ] Load inspection requirements for product detail and edit pages.
- [ ] Save posted inspection requirement rows on product create/edit.
- [ ] Show requirements on product detail.
- [ ] Add editable rows and add/delete row behavior to product create/edit.

### Task 3: Shipment Plan Report

**Files:**
- Modify: `app.py`
- Create: `templates/inspection_report_preview.html`
- Modify: `templates/shipped_orders.html`
- Modify: `static/style.css`

- [ ] Add inspection report preview route for shipment plans.
- [ ] Render a print-friendly report grouped by product and requirement rows.
- [ ] Add inspection report button to each shipment plan card.

### Task 4: Completed Report Upload

**Files:**
- Modify: `app.py`
- Modify: `templates/shipped_orders.html`
- Modify: `static/style.css`

- [ ] Add completed report file storage under `uploads/inspection-reports`.
- [ ] Add upload route for a shipment plan.
- [ ] Show uploaded report files on the shipment plan card.
- [ ] Delete uploaded report files when deleting a shipment plan.

### Task 5: Verification

**Files:**
- Modify as needed from previous tasks.

- [ ] Run focused tests for inspection helpers.
- [ ] Run Python syntax check.
- [ ] Manually inspect the changed pages in the browser if a local app server is available.
