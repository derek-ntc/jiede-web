# Task 7 report: integration migration QA and final verification

## Scope and result

- Accepted base: `f8f9bc91908ab4b97dbb342cf5d549808d82ada7`.
- Worktree: `/Users/derek/Documents/jiede-web/.worktrees/product-process-delivery-lines`.
- Changes are tests and this report only. No application behavior, schema design, or user-visible decision changed; the approved specification remains unchanged.
- Both previously recorded full-suite failures are resolved. Final complete suite, including the opt-in real-browser test: **557 passed, no failures, no skips**, in 53.802 seconds.
- No deployment, push, subagent dispatch, production database access, or production data mutation.

## Legacy database integration fixture

`LegacyWorkflowStartupTests` extends the existing real SQLite finance fixture and reuses its genuine old-source-constraint builder. It creates a representative pre-upgrade database by removing all six new workflow tables, the production snapshot marker, and the assembly line remark column, and by restoring the old source-type CHECK constraints on delivery, invoice, and reconciliation item tables.

The fixture includes old imported product data (drawing/SKU/unit with an empty specification), an existing BOM relationship, two fixed-column production cards (partially completed and untouched), ordinary and assembly shipments with allocation and combined order totals, issued legacy delivery documents with no snapshot items, an invoiced finance record, and a confirmed reconciliation statement with active claims. Source IDs, custom indexes/triggers, and autoincrement high-water marks come from the existing legacy source fixture.

The combined startup test verifies:

- Every old row and old field value survives the upgrade, including document/source/claim IDs, imported master data, historical prices, order totals, invoice metadata, and reconciliation data.
- First and second startup produce identical complete table contents and SQLite schema objects.
- Exactly six production steps are backfilled in the expected order with literal historical timestamps; no supplemental sources or delivery snapshot items are fabricated for legacy records.
- `PRAGMA foreign_key_check` is empty and `PRAGMA integrity_check` returns `ok`.
- Both finance and reconciliation claims still refuse mutations, and a real batch-delete request containing a locked product rejects the entire batch.
- Legacy product, technical, production-card, shipped-list, finance, and reconciliation HTML routes remain readable, and both old delivery PDFs remain downloadable.
- Ordinary and assembly source update/delete triggers still update/invalidate their corresponding delivery notes. Trigger probes use savepoints and roll back so the locked fixture is preserved.

The test complements, rather than replaces, the existing detailed source-table rebuild, checksum/type integrity, rollback/retry, sequence, and supplemental-trigger tests exercised by the complete suite.

## End-to-end workflow coverage

Two new real Flask/SQLite integration tests in `test_delivery_note_workflow.py` connect previously separated contracts:

1. Excel preview/confirmation updates an existing product and creates a standalone product; the specification filter finds the imported value, while price, prior remark, and BOM relationship survive. Saving a default process template, creating/completing a card, changing the template, creating another card, and restarting twice preserves the entire historical step snapshot and completion actor. A real shipment creates invoice/reconciliation locks; mixed batch deletion is atomic, while deleting only the unlocked imported product succeeds and preserves the product-list return query.
2. Both assembly and ordinary modes ship ordered rows with quantities **2, 0, 3**, including an added product with insufficient stock. The document keeps all three ordered remarks; the zero row changes neither stock nor order totals and creates no outbound transaction. Positive source types are exactly assembly items or ordinary plus supplemental sources. Both accounting documents contain only quantities 2 and 3 and total the hand-calculated **996 minor units**. Same-token retry is read-only, active claims lock both positive sources, and product edits plus two restarts do not change document specifications or line snapshots. HTML escaping and actual PDF generation are verified.

Initial integration runs exposed only fixture-authoring mistakes (duplicate batch label, wrong existing return-field name, and assuming finance rows expose a manual ID); these were corrected to the application's established interfaces. No production fix was needed.

## Baseline failure diagnosis and fixes

### Import help

The failing test required the old literal `旧模板`; the current page already implements the approved dual mapping: Excel specification is both the product-matching drawing number and the saved product specification. The obsolete assertion was replaced with the current dual-mapping explanation. Real import/list behavior is separately covered by the new end-to-end test, so this is not being used as a substitute for functionality coverage.

### Multiprocessing shipment test

The original failure was reproduced before changes. A temporary faulthandler probe showed the child in `multiprocessing.queues._finalize_join`, waiting on its QueueFeederThread, which was blocked in `multiprocessing.connection._send`. The parent joined children before draining the result queue; the expanded HTTP 409 preview could fill the OS pipe, preventing clean child exit. This was a test-harness deadlock, not application lock or idempotency failure.

The fix drains bounded queue results before joining workers and always terminates/joins stragglers and closes the queue. Both workers explicitly report readiness before one start event releases their HTTP requests. The old save-function barrier was removed because the application write lock prevents a second worker from reaching that barrier concurrently; its timeout was not meaningful synchronization.

The existing legacy-preview case still requires **201 and 409**, a fresh conflicting preview digest, exactly one batch/allocation/operation/note, exactly one inventory deduction, and the exact order shipped total. A separate real multiprocessing case now requires identical **201/201** receipts when both workers submit the same explicit operation token, with the same exactly-once database assertions. Both cases passed **20 consecutive repetitions each (40 tests)** in 3.214 seconds. Existing cross-writer lock tests remain unchanged and pass.

Evidence: `/private/tmp/ppdl-task7-baseline.log` and `/private/tmp/ppdl-task7-concurrency-diagnose.log`.

## Automated verification

Runtime: `/Users/derek/Documents/jiede-web/.venv/bin/python`. Existing deprecation/resource-warning noise was suppressed with `PYTHONWARNINGS=ignore`; intentional fault-injection application logs remain in the logs.

Each required command `python -m unittest discover -s tests -p '<filename>' -v` passed independently:

| Test file | Passed |
| --- | ---: |
| `test_startup_shipment_backfill.py` | 7 |
| `test_product_bom_import.py` | 37 |
| `test_product_batch_assembly.py` | 17 |
| `test_production_processes.py` | 17 |
| `test_assembly_shipping.py` | 93 |
| `test_flexible_assembly_shipping.py` | 20 |
| `test_delivery_note_workflow.py` | 39 |
| `test_finance.py` | 33 |
| `test_reconciliation.py` | 74 |
| Total | 337 |

Logs: `/private/tmp/ppdl-task7-test_<module>.log`.

- Default full run: 557 tests, 556 passed and 1 existing opt-in skip, 61.261 seconds. `/private/tmp/ppdl-task7-full.log`.
- The optional browser check initially could not resolve Playwright; the already-bundled module was provided via `NODE_PATH`. Its next run hit the sandbox's macOS Chromium MachPort restriction. With approved escalation, the existing test passed without code changes or installation. `/private/tmp/ppdl-task7-optional-browser-final.log`.
- Final full run with `JIEDE_BROWSER_TESTS=1` and bundled `NODE_PATH`: **557 passed, no skips**, 53.802 seconds. `/private/tmp/ppdl-task7-full-final.log`. The environment limitation was therefore resolved for final verification and is not an outstanding application failure.
- `node tests/order_shipping_ui.cjs` and its `stale-spec` scenario passed.
- `node tests/assembly_shipping_ui.cjs deletion`, `focus`, and `composition` passed. An initial invocation omitted the required scenario argument; rerunning with all documented scenarios passed.
- `git diff --check` passed.

## Local browser and PDF QA

A temporary script at `/private/tmp/ppdl-task7-qa.py` used only the integration fixture's disposable database/files and bound Flask to `127.0.0.1`. In the actual Codex in-app browser:

- Product list showed the imported specification and retained remark. Selecting P1 enabled bulk controls; the delete modal displayed drawing, name, customer, and specification. Cancel made no deletion.
- Technical page showed the current template. Added `去毛刺`, moved it above `包装`, and saved; the saved order was visible.
- Historical process card still displayed `下料` and literal `检验 <终检>` in both print copies, with completion time and operator `admin`, despite the new product template.
- Ordinary mode rejected an all-zero selection, preserved the entered zero-row remark, searched/added the same customer's product, showed unmatched/shortage warnings, and successfully saved. Operation 4's document displayed quantity 0 plus quantity 1 with both original remarks.
- Assembly mode expanded the BOM, accepted a zero actual quantity with its remark, searched/added a temporary product, showed no-order/shortage warnings, and after confirming those warnings saved successfully. Operation 5's document retained the zero BOM row and the added product's remark.
- Both delivery PDF downloads returned real PDF bytes. Their A4 print layouts were rendered with bundled Poppler and visually reviewed: visible zero quantities, final remark column, literal angle-bracket text, legible Chinese text, no overlap/clipping, and no product prices.

PDF QA artifacts are `/private/tmp/ppdl-task7-browser-qa.pdf` and `/private/tmp/ppdl-task7-assembly-qa.pdf`, with matching PNG renders. The delivery-result link intentionally downloads an attachment rather than opening the floating PDF modal; this existing behavior was not changed. No physical print job was sent.

The in-app browser's tiny native screenshot viewport did not provide a reliable desktop-size capture even after a temporary override. Thus no desktop screenshot/layout certification is claimed from that surface. Functional checks used current accessibility/DOM state; the successful existing real Chromium test separately covered 1600px and 390px contrast, overflow, and style isolation. The viewport override was reset, the created tab closed, the QA server exited cleanly, its temporary database/files were cleaned, and the temporary synthetic-session cookie file was removed. Only synthetic PDF/PNG artifacts and diagnostic scripts/logs remain in `/private/tmp` for review.

Systematic-debugging, test-driven-development test-design guidance, verification-before-completion, and PDF skills guided this task. Controller review is the next step; this task does not authorize deployment.
