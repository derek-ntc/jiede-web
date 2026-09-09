# Product Process and Delivery Lines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make product Excel imports populate the visible specification field, support safe all-or-nothing batch deletion, add configurable product process cards, and make shipment lines (including zero-quantity and added products) persist accurately through delivery notes, finance, and reconciliation.

**Architecture:** Keep the current Flask/SQLite application architecture. Add normalized process-template and process-card tables; retain legacy production columns as compatibility projections. Persist delivery-note line snapshots instead of reconstructing new notes only from shipment sources. Extend the shipment source model with supplemental lines, while retaining all historical ordinary and assembly records.

**Tech Stack:** Python/Flask, SQLite, Jinja templates, vanilla JavaScript, CSS, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-09-product-process-delivery-lines-design.md`

## Global Constraints

- Preserve all existing business data and existing URLs.
- Do not deploy to VPS or NAS as part of this implementation plan.
- Use migrations that are idempotent and transactional; validate SQLite table rebuild copies before swapping tables.
- Apply the existing application write lock and re-check mutable financial/reconciliation state inside the final write transaction.
- Keep legacy follow-up views working while the new process-step table becomes the source of truth for new UI behavior.
- Keep the current customer boundary for shipment product search and never create an unscoped extra product line.
- A shipment operation must contain at least one positive quantity. Zero lines are documentation-only and must not affect inventory, order allocation, finance, or reconciliation.

---

## Task 1: Fix product import specifications and implement safe batch delete

**Files:**
- Modify: `app.py`
- Modify: `templates/product_bom_import.html`
- Modify: `templates/index.html`
- Modify: `static/product_list.js`
- Modify: `static/product-list.css`
- Modify: `tests/test_product_bom_import.py`
- Modify: `tests/test_product_batch_assembly.py`

- [ ] **Step 1: Add failing import regression tests.**
  - Cover insert and re-import update where Excel `规格型号` writes both the drawing-number matching field and `manuals.supplier`.
  - Cover blank specification on an update deliberately clearing the imported specification, while unrelated product files, price, remarks, materials, and BOM links remain untouched.
  - Run: `python -m unittest discover -s tests -p 'test_product_bom_import.py'`
  - Expected: tests fail because `apply_product_bom_import` does not write `supplier`.

- [ ] **Step 2: Update the importer and preview contract.**
  - In `parse_product_bom_rows`, retain the current `规格型号 -> drawing_no` key for matching.
  - In `build_product_bom_import_plan`, expose an explicit preview column/value for the product specification.
  - In `apply_product_bom_import`, include `supplier` in both insert and matched-product update paths, without changing the existing preservation behavior for unrelated fields.
  - Update import help text to state that the column supplies both matching drawing number and the product-list specification display.
  - Run the Task 1 import test command; expected pass.

- [ ] **Step 3: Add failing all-or-nothing deletion tests.**
  - Add tests for a batch containing normal products, duplicated IDs, an unknown ID, and a finance/reconciliation-locked product.
  - Assert any invalid/locked selection leaves every selected product intact; assert valid selection removes every selected product and associated files only after DB commit.
  - Assert single delete and batch delete redirect to the product list with `q`, `supplier`, `customer`, `sort`, and `direction` preserved.
  - Run: `python -m unittest discover -s tests -p 'test_product_batch_assembly.py'`
  - Expected: tests fail before the route exists.

- [ ] **Step 4: Implement the guarded delete service and route.**
  - Extract/reuse the existing mutable-finance/reconciliation checks so the complete selected set is verified before any deletion.
  - Add a POST batch-delete endpoint under the product-edit permission boundary; parse, deduplicate, and load all IDs before acting.
  - Move product uploads to an operation-specific quarantine before the transaction; restore them on failure; delete quarantine only after commit and log cleanup failures.
  - Make `delete_manual` use the same service for a one-ID selection rather than retaining a separate redirect/delete path.
  - Run the Task 1 batch test command; expected pass.

- [ ] **Step 5: Add the list interaction and confirmation UI.**
  - Reuse existing selection checkboxes, add a destructive batch-delete toolbar action, and show a native dialog/accessible modal containing drawing number, name, customer, and specification for every selected row.
  - Keep product search/filter/sort query parameters in both the form action and redirect.
  - Disable submit when selection is empty and leave the existing assembly batch action unaffected.
  - Run: `python -m unittest discover -s tests -p 'test_product_list_layout.py'`
  - Expected: existing layout checks and new semantic hooks pass.

- [ ] **Step 6: Commit the focused change.**
  - Run: `git diff --check`
  - Run: `python -m unittest discover -s tests -p 'test_product_bom_import.py'`
  - Run: `python -m unittest discover -s tests -p 'test_product_batch_assembly.py'`
  - Commit: `feat: improve product import and batch delete`

## Task 2: Introduce configurable process templates and migrate follow-up cards

**Files:**
- Create: `production_processes.py`
- Modify: `app.py`
- Modify: `tests/test_production_followup_customers.py`
- Create: `tests/test_production_processes.py`

- [ ] **Step 1: Write failing schema/migration tests.**
  - Cover idempotent creation of `manual_process_configs`, `manual_process_steps`, and `production_followup_process_steps`.
  - Seed legacy follow-ups using the three timestamp columns and assert migration creates three ordered rows exactly once, with matching completion timestamps/operators where available.
  - Cover product behavior: no config means legacy default processes; saved empty config means no processes; explicit config is copied into new follow-up snapshot.
  - Run: `python -m unittest discover -s tests -p 'test_production_processes.py'`
  - Expected: import/module/schema failures before implementation.

- [ ] **Step 2: Implement process persistence helpers.**
  - Put table creation, legacy backfill, template load/save, and follow-up snapshot creation in `production_processes.py`.
  - Use case-insensitive per-product uniqueness, stable `sort_order`, a 100-character process-name limit, and an explicit config row to distinguish empty from legacy defaults.
  - Call idempotent setup from startup with the existing database initialization order.
  - Run the Task 2 test command; expected pass.

- [ ] **Step 3: Add failing state-transition tests.**
  - Test completion only for the first uncompleted step and revert only for the final completed step.
  - Test add-at-end, deletion of incomplete steps only, and reorder only within the uncompleted tail.
  - Test compatibility synchronization for named `激光`, `折弯`, and `焊接` steps, including clear-on-revert.
  - Run the Task 2 test command; expected failure for transition helpers.

- [ ] **Step 4: Implement process-card transition service.**
  - Add transaction-protected helpers returning the full ordered card after every action.
  - Preserve the legacy `production_followups` status/timestamp behavior for existing routes; project the three recognized names to/from legacy columns.
  - Ensure changing a product template cannot mutate any existing follow-up snapshot.
  - Run the Task 2 test command; expected pass.

- [ ] **Step 5: Commit the data layer change.**
  - Run: `git diff --check`
  - Run: `python -m unittest discover -s tests -p 'test_production_processes.py'`
  - Commit: `feat: add configurable production process data`

## Task 3: Expose product process templates and configurable follow-up cards

**Files:**
- Modify: `app.py`
- Modify: `templates/detail_technical.html`
- Modify: `templates/production_followups.html`
- Modify: `templates/production_process_card.html`
- Modify: `static/style.css`
- Modify: `tests/test_production_followup_customers.py`
- Modify: `tests/test_production_processes.py`

- [ ] **Step 1: Add failing route/template tests.**
  - Assert product technical information can save ordered process templates including a deliberate empty template.
  - Assert the production list supports customer filtering plus keyword search over drawing number/name and renders process actions from stored rows.
  - Assert the printable card has two copies and displays every process with operator/time, not a fixed three-stage loop.
  - Run: `python -m unittest discover -s tests -p 'test_production_followup_customers.py'`
  - Run: `python -m unittest discover -s tests -p 'test_production_processes.py'`

- [ ] **Step 2: Implement product technical-information editing.**
  - Add an editable process-template block in `detail_technical.html` that supports append, remove, reordering, validation feedback, and explicit save.
  - Route saves through the process-template service under existing product-edit permissions.

- [ ] **Step 3: Implement follow-up UI actions and print rendering.**
  - Add process-card endpoints for add/remove/reorder/complete/revert that enforce service rules server-side.
  - Replace fixed laser/bending/welding presentation with ordered process rows, while rendering legacy cards correctly after backfill.
  - Retain current production customer filter and add keyword query parameter preservation.
  - Run both Task 3 test commands; expected pass.

- [ ] **Step 4: Commit the process UI change.**
  - Run: `git diff --check`
  - Commit: `feat: manage product processes and follow-up cards`

## Task 4: Persist delivery-note line snapshots and add supplemental source support

**Files:**
- Modify: `shipping_workflow.py`
- Modify: `app.py`
- Modify: `reconciliation.py`
- Modify: `pricing.py`
- Create: `tests/test_delivery_note_items.py`
- Create: `tests/test_supplemental_shipments.py`
- Modify: `tests/test_delivery_note_workflow.py`
- Modify: `tests/test_finance.py`
- Modify: `tests/test_reconciliation.py`

- [ ] **Step 1: Write failing snapshot and source-type migration tests.**
  - Seed ordinary and assembly delivery notes, run startup migration, and assert their old source rows survive unchanged.
  - Assert `delivery_note_items` supports item order, line snapshots, quantity zero, source links optional for documentation-only lines, and 500-character remarks.
  - Assert source-type constrained tables (`delivery_note_sources`, finance items, reconciliation items) accept `supplemental` without dropping rows, indexes, or triggers.
  - Run: `python -m unittest discover -s tests -p 'test_delivery_note_items.py'`
  - Expected: failures because the normalized line table/migration does not exist.

- [ ] **Step 2: Implement idempotent schema migration and verified SQLite rebuild.**
  - Add `delivery_note_items` and `supplemental_shipments` in `shipping_workflow.py`.
  - Rebuild CHECK-constrained source tables transactionally: create compatible replacement, copy, compare counts and critical-field digest, swap, and recreate indexes/triggers before commit.
  - Preserve source IDs for ordinary/assembly rows. Make startup safe to rerun after an interrupted prior startup.
  - Run the Task 4 migration test command; expected pass.

- [ ] **Step 3: Update delivery-note creation/loading APIs.**
  - Extend `create_delivery_notes` to receive ordered display-line snapshots after source grouping succeeds.
  - Persist all rows, including zero quantities, and have `load_delivery_note` prefer snapshots for new notes while retaining dynamic compatibility for older notes.
  - Add supplemental shipment loading that supplies price, currency, and product snapshot data to finance/reconciliation paths.
  - Run: `python -m unittest discover -s tests -p 'test_delivery_note_workflow.py'`
  - Run: `python -m unittest discover -s tests -p 'test_finance.py'`
  - Run: `python -m unittest discover -s tests -p 'test_reconciliation.py'`

- [ ] **Step 4: Commit the delivery persistence foundation.**
  - Run: `git diff --check`
  - Run all four Task 4 test commands.
  - Commit: `feat: persist delivery note lines and supplemental sources`

## Task 5: Make assembly shipment rows editable, zero-preserving, and remark-aware

**Files:**
- Modify: `app.py`
- Modify: `templates/assembly_shipping_form.html`
- Modify: `static/assembly_shipping.js`
- Modify: `shipping_workflow.py`
- Modify: `tests/test_assembly_shipping.py`
- Modify: `tests/test_flexible_assembly_shipping.py`
- Modify: `tests/test_delivery_note_workflow.py`

- [ ] **Step 1: Add failing assembly workflow tests.**
  - Cover an assembly preview/save with a zero-quantity BOM line: it stays in the delivery-note snapshot, receives a remark, but creates no allocation, stock deduction, shortage, finance, or reconciliation quantity.
  - Cover deleting a BOM line, adding a current-customer product, merging a duplicate product, stock shortage warning, and rejection of an all-zero operation.
  - Cover explicit line remark storage in `assembly_shipment_items`.
  - Run: `python -m unittest discover -s tests -p 'test_assembly_shipping.py'`
  - Run: `python -m unittest discover -s tests -p 'test_flexible_assembly_shipping.py'`
  - Expected: failures for zero lines/remarks/snapshot semantics.

- [ ] **Step 2: Migrate and implement assembly persistence changes.**
  - Add an idempotent `remark` column to `assembly_shipment_items`.
  - Change item parsing/storage to accept nonnegative quantities, reserve allocations only for positive quantities, and preserve display order/remarks for snapshot creation.
  - Recheck stock at save; deduct only available inventory and report any shortage without blocking authorized shipment.
  - Keep existing flexible extra-item source behavior for positive additions.

- [ ] **Step 3: Update the assembly form.**
  - Render per-row editable actual quantity, remark, removal control, stock/status warning, and current-customer product-search footer.
  - Ensure client-side behavior is convenience only; submit complete ordered line data and let server enforce customer/product/quantity rules.
  - Build delivery-note snapshots after positive sources exist so zero/document-only rows remain on the note.
  - Run both Task 5 test commands plus `test_delivery_note_workflow.py`; expected pass.

- [ ] **Step 4: Commit the assembly workflow change.**
  - Run: `git diff --check`
  - Commit: `feat: support editable assembly shipment lines`

## Task 6: Extend order shipment rows with additions, zero lines, remarks, and supplemental allocation

**Files:**
- Modify: `app.py`
- Modify: `templates/shipment_operations.html`
- Modify: `shipping_workflow.py`
- Modify: `reconciliation.py`
- Modify: `pricing.py`
- Modify: `tests/test_shipment_price_bulk.py`
- Modify: `tests/test_delivery_note_workflow.py`
- Modify: `tests/test_finance.py`
- Modify: `tests/test_reconciliation.py`
- Modify: `tests/test_supplemental_shipments.py`

- [ ] **Step 1: Add failing ordinary-shipment tests.**
  - Cover current-customer product search/add, per-row removal, zero quantity/remark, and mixed positive+zero delivery-note output.
  - Cover matching a product to open customer order quantities in delivery-date/order-ID order, then writing unmatched/excess remainder as `supplemental_shipments` with warning.
  - Cover supplemental price snapshots, limited inventory deduction, finance/reconciliation inclusion only for positive quantity, and lock-aware edit/delete restoration behavior.
  - Run: `python -m unittest discover -s tests -p 'test_supplemental_shipments.py'`
  - Run: `python -m unittest discover -s tests -p 'test_shipment_price_bulk.py'`
  - Expected: failures before new allocation/persistence rules.

- [ ] **Step 2: Implement a unified server-side shipment-line parser/allocation service.**
  - Normalize order, assembly, and added lines into explicit row records with product/customer/source/quantity/remark fields.
  - In order mode, match positive quantities to existing open orders first and create supplemental records only for unmatched remainder; reject an added line without a concrete current customer.
  - Use nonnegative validation, enforce one positive line minimum, and guarantee zero rows cannot reach inventory/order/finance/reconciliation mutation code.
  - Preserve operation-level logistics note separately from per-line remark.

- [ ] **Step 3: Update ordinary shipment operation UI and delivery presentation.**
  - Let existing order-product rows be changed to zero instead of being silently dropped.
  - Add customer-scoped product search at the footer and visible warning states for unmatched order quantities and stock shortage.
  - Render specification, quantity, and final-column remark consistently in shipment and delivery-note screens/PDF/print output.
  - Run the Task 6 test commands plus finance/reconciliation/delivery-note tests; expected pass.

- [ ] **Step 4: Commit the order shipment change.**
  - Run: `git diff --check`
  - Commit: `feat: support supplemental and zero order shipment lines`

## Task 7: Perform integration migration QA and document verification

**Files:**
- Modify: `tests/test_startup_shipment_backfill.py`
- Modify: `tests/test_delivery_note_workflow.py`
- Modify: `docs/superpowers/specs/2026-09-09-product-process-delivery-lines-design.md` (only if implementation discovers a necessary, user-visible decision change)

- [ ] **Step 1: Create a representative legacy database fixture.**
  - Include products with old import data, fixed three-step production follow-ups, ordinary/assembly shipments, issued delivery notes, finance invoices, reconciliation statements, and locked records.
  - Run startup migrations twice and assert stable table schema, row counts, source IDs, trigger behavior, and legacy display routes.
  - Run: `python -m unittest discover -s tests -p 'test_startup_shipment_backfill.py'`

- [ ] **Step 2: Run end-to-end workflow tests.**
  - Verify product import → product list specification → batch delete protection.
  - Verify product process template → new production card snapshot → historical card stability.
  - Verify assembly/order shipment positive+zero+added lines → delivery note → finance/reconciliation behavior.
  - Run: `python -m unittest discover -s tests -p 'test_product_bom_import.py'`
  - Run: `python -m unittest discover -s tests -p 'test_product_batch_assembly.py'`
  - Run: `python -m unittest discover -s tests -p 'test_production_processes.py'`
  - Run: `python -m unittest discover -s tests -p 'test_assembly_shipping.py'`
  - Run: `python -m unittest discover -s tests -p 'test_flexible_assembly_shipping.py'`
  - Run: `python -m unittest discover -s tests -p 'test_delivery_note_workflow.py'`
  - Run: `python -m unittest discover -s tests -p 'test_finance.py'`
  - Run: `python -m unittest discover -s tests -p 'test_reconciliation.py'`

- [ ] **Step 3: Perform final verification.**
  - Run: `git diff --check`
  - Run: `python -m unittest discover -s tests`
  - Manually verify the product list, technical product page, production process card, assembly shipment, order shipment, and delivery-note print preview in the local browser.
  - Record any pre-existing environment-only test limitation separately from application failures.

- [ ] **Step 4: Commit integration tests and documentation.**
  - Commit: `test: cover configurable process and delivery workflows`
