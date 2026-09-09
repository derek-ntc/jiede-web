# Task 6 — legacy procurement migration

## Outcome and scope

Implementation commit: `6618198106274e9ddcb6faa4d331004f9418b6c3` (`feat: migrate legacy purchase orders`).

Implemented deterministic supplier migration, Phase 1 carton/followup order backfill, read-only reconciliation CLI, startup integration, unified navigation, and legacy HTTP write denial. All implementation and tests were performed in `/Users/derek/Documents/jiede-web/.worktrees/unified-procurement`. All database migration runs used memory databases or synthetic temporary fixtures. There was no worktree `data/manuals.db`; no main-workspace, NAS, VPS, or user database was migrated or altered. No deployment, file copying of legacy attachments, or subagent dispatch occurred.

The implementation is ready for the controller's independent spec/code review, not a deployment or release approval.

## Controller rulings implemented

- Supplier sources use the fixed precedence: existing unified values, carton/arrival/powder master tables, then the five product/record free-text sources. All names are compared exactly after trimming; case is preserved. Every source row is linked, including the supplier-less `purchase_followups` source. Business-record remarks are not misclassified as supplier remarks. Nonempty conflicting master fields are retained as idempotent conflict records; existing suppliers retain their code, existing field values, and active state.
- `suppliers.system_kind` distinguishes the inactive system unknown supplier from a real supplier with a matching code/name. A real-row collision blocks the affected blank-source links/orders and is reported; it is never hijacked. The system row cannot be renamed, activated, or deleted through supplier routes or direct identity-changing SQL. Normal supplier management remains unchanged.
- `procurement_migration_conflicts` has an exact uniqueness key over source, entity/name, field, kept/incoming values, and reason. Invalid order rows store their original values as deterministic textual JSON for diagnostics. Reruns preserve conflict timestamps and do not duplicate conflicts.
- Only `carton_purchases` and `purchase_followups` become orders, one header/item each. Stable order numbers are `LEGACY-CARTON-{id}` and `LEGACY-OTHER-{id}`, with unique legacy source/ID pairs on both tables. Supplier snapshots are saved; historical company-delivery snapshots remain blank.
- Carton material, print mark, dimensions/free-text size, quantity, minor-unit price, dates, author, and remarks are preserved. `purchase_orders.legacy_metadata` preserves completion/purchase flags and historical date provenance without claiming receipt or inventory. Explicit carton received dates become item expected dates; otherwise expected dates derive from the deterministic header date and are counted.
- Missing followup purchase date produces draft. Fallback is missing/blank recorded date → missing/blank created date → supplied migration date. Per controller clarification, *any explicit malformed purchased/recorded/created date is a row conflict*, even when a later candidate is valid. Invalid quantities, negative/nonfinite/sub-cent/overflowing money, malformed calendar dates/timestamps, and invalid dimensions do not get fabricated values.
- Historical image filenames are stored in `purchase_order_legacy_attachments`; no attachment file is read, copied, renamed, or deleted. Source/order/item relationships are protected by foreign keys and trigger checks for the production connection's FK-disabled mode. The old strict-item schema rebuild temporarily drops/recreates cross-table attachment triggers within its existing savepoint.
- Arrival order/receipt/inventory migration remains deferred to Phase 2. Arrival CRUD and supplier maintenance remain operational; powder-coating workflow behavior is unchanged. No procurement inventory or receipt table is created by Phase 1 migration.
- `init_db()` calls migration after all unified/legacy tables and indexes exist, inside the application write lock and startup transaction. Validation conflicts do not abort startup; SQL/schema/programming failures propagate and roll back.
- A centralized compatibility guard makes all historical carton/followup POST/subroutes return 409, including supplier/product mutation and indirect `/admin/orders/carton-purchases`. Historical entry GETs redirect to unified carton/other, whose own permissions apply. Historical carton statement and single-row PDF GETs stay readable. Arrival URLs do not redirect.
- Top navigation now has one procurement entry and the existing unified business-partner entry. All unified purchase paths, detail/edit/export paths, and legacy arrival paths activate procurement. Four category tabs and permission-aware historical arrival tab are present. Dashboard/admin counts use unified orders/drafts while retaining legacy arrival counts. The old production-order carton creation button is replaced with a permission-aware unified carton link.

## TDD and verification

Used the test-driven-development, systematic-debugging, verification-before-completion, and requesting-code-review skills. The last skill's independent review is handed to the controller because this implementer was explicitly prohibited from creating subagents.

1. Wrote migration fixtures and assertions before production changes. Initial `python` was unavailable and system `python3` lacked Pillow, so the existing worktree `.venv/bin/python` was used; no dependency installation was performed.
2. Genuine RED: `.venv/bin/python -m unittest tests.test_procurement_migration -v` ran **14 tests with 23 failed assertions**. Missing migration/report APIs, absent startup backfill, old GET behavior, and non-unified navigation were the causes.
3. First GREEN: all **14 migration tests passed** after implementation and fixing a missing `timezone` import exposed by startup integration tests.
4. Additional RED/GREEN edge checks covered malformed fallback timestamps, whitespace in existing unified supplier names, system supplier reactivation, attachment relationship protection, and removal of the obsolete order-page carton button. Optional absent-table/old-column fixtures also pass. The existing strict-item schema regression caught a cross-table trigger reference during DROP/RENAME; the trigger lifecycle was corrected, then verified.
5. Final focused regression (six Phase 1 modules plus the two intentionally changed legacy inspection-route tests): **96 tests, all passed, no skips**. Existing bundled Poppler was explicitly added to PATH, so PDF tests ran.
6. First complete suite: **658 tests, 1 failure, 1 pre-existing skip**. The sole failure was `test_business_list_layout.py` requiring the obsolete carton `formaction` button. Updated only that assertion to require the new carton link and absence of the old action; retained all shipment-plan form, selection, sort, filter, and overdue checks. The corrected test passed independently.
7. The controller explicitly required a second complete run after that failure. Final command:

   ```sh
   PATH="/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin:$PATH" .venv/bin/python -m unittest discover -s tests -v
   ```

   **658 tests in 63.003 seconds: OK, skipped=1 (657 passed).** The sole skip remains the pre-existing real-browser shipping color test requiring `JIEDE_BROWSER_TESTS=1`. PDF/export tests did not skip. Existing UTC deprecation warnings and intentionally injected failure-path logs remain unchanged.
8. `git diff --check` and `git diff --cached --check` passed before committing. Reviewed all changed production, template, script, and test diffs; no unrelated edits were included.

Logs:

- `/private/tmp/jiede-procurement-task6-qa/full-suite.log` — first complete run and obsolete assertion failure.
- `/private/tmp/jiede-procurement-task6-qa/final-full-suite.log` — final complete passing run.

## Temporary report CLI reconciliation

QA directory: `/private/tmp/jiede-procurement-task6-qa/`.

Three synthetic fixtures were migrated twice, then inspected with the actual CLI using `--database` and only `mode=ro` SQLite connections. CLI never imports the application or invokes ensure/migrate functions; it opens a consistent read transaction and prints sorted JSON. Missing file/schema fails clearly without creating a database.

| Fixture | First/second created | Order-source accounting | Supplier coverage | Conflicts |
| --- | --- | --- | --- | --- |
| `valid.db` | 4 / 0 | carton 2/2, followup 2/2 migrated; arrival 1 deferred | 14/14 links; 4 unknown | 8 supplier field conflicts |
| `invalid.db` | 4 / 0 | carton 4 examined: 2 migrated, 2 conflicted; followup 3 examined: 2 migrated, 1 conflicted; arrival 1 deferred | 17/17 links; 7 unknown | 11 total: 8 supplier fields + 3 invalid order rows |
| `blocked.db` | 0 / 0 | followup 1 examined/conflicted; no order created | 0/1 links; real colliding supplier untouched | 1 blocking unknown collision |

Normal fixture reconciles carton quantity **14 → 14**, amount **3,920 → 3,920 minor units**; followup quantity **7 → 7**, both prices absent rather than invented. Arrival quantity **5** and amount **1,005 minor units** remain explicitly deferred, with zero unified orders/items. Attachment coverage **1/1**, derived expected dates **3**, duplicate legacy keys **0**, foreign-key violations **0**.

Invalid fixture reports quantity/amount exclusions separately: carton old valid quantity 16 versus migrated 14 reflects the two-unit row with nonfinite money; the zero-quantity row is flagged, not added to a plausible quantity. Followup old quantity 8 versus migrated 7 reflects the malformed-date row. Every order source is accounted as migrated/conflicted/deferred, with no unexplained source gap.

JSON artifacts: `valid-report.json`, `invalid-report.json`, `blocked-report.json`.

## Strict read-only evidence

Fresh read-only report connections each returned `total_changes == 0`. Before/after CLI hashes and nanosecond mtimes were identical:

| Fixture | Unchanged SHA-256 | Unchanged mtime_ns |
| --- | --- | --- |
| valid | `974dc3b5a39a0fbb470d4767be5e7099e74505411af8d6593dd747ae1168db8d` | `1788968068698495263` |
| invalid | `af0eacbc77298944a6e3ba179cce4acf29f9783d79a98cac3e508e6ee32581c0` | `1788968068713887002` |
| blocked | `75fc6ad24876a0f4f08f6be260f3b3f32aca1778c76477e8062b82c09e11f6bd` | `1788968068727007117` |

`readonly-evidence.md` in the QA directory records these checks. Automated tests also compare repeated CLI output, canonical sorted JSON, attachment bytes/mtime, missing-file behavior, missing-schema error, and no report writes. The route fixture compares complete SQL dumps before/after every denied legacy mutation.

## Changed files and self-review

- `procurement.py`: migration schema, system/attachment constraints, deterministic merge/backfill, strict validation, read-only report, compatible strict-item trigger rebuild.
- `app.py`: startup migration, HTTP compatibility guard, unified statistics, system supplier write denial.
- `scripts/report_procurement_migration.py`: read-only CLI.
- `templates/base.html`, `purchase_tabs.html`, `admin.html`, `dashboard.html`, `arrival_records.html`: unified/permission-aware navigation and counts.
- `templates/orders.html`, `suppliers.html`: remove obsolete action and expose read-only system status.
- `tests/test_procurement_migration.py`: 19 domain, migration, report, CLI, startup, and route tests.
- `tests/test_inspection_reports.py`, `tests/test_business_list_layout.py`: only assertions directly superseded by the new navigation/read-only contract.

Dynamic SQL identifiers come exclusively from fixed internal allowlists or internally constructed header/item dictionaries; legacy values are parameter-bound. Migration uses the existing writer reservation/savepoint and never commits the caller's transaction. Legacy tables/files are never deleted or rewritten. Unexpected errors are not swallowed. Navigation grants no new permissions: arrival remains gated by the existing carton permission, and unified pages enforce their independent purchase permissions.

## Risks, deviations, and follow-up boundaries

- Added explicit `system_kind` and `legacy_metadata` columns to support safe identity and structured provenance. Added small defensive system/attachment constraints and corresponding UI guards to keep migration invariants intact after startup.
- Added historical arrival tabs/styles and replaced a dead carton action on the production-orders page; these are directly required to make the temporary compatibility state usable. No new automated production-order-to-unified-purchase conversion was introduced.
- Historical attachment references are retained in schema, not copied into a new attachment storage system. No attachment existence probe occurs during report generation, preserving strict DB-only reads.
- Arrival/powder workflows can create new supplier source rows while running; those rows are linked on subsequent initialization, and the report exposes any current source gaps. Their business record migration remains Phase 2 scope.
- Historical rows with invalid data intentionally remain unmigrated and require later operator correction. Supplier conflicts need human review; no automated overwrite or conflict resolution was attempted.
- Complete suite was run twice only because the first revealed an obsolete assertion and the controller explicitly required final full-suite evidence after correction. Final production code was unchanged between those two runs; only that test assertion changed.
- No fresh browser visual QA was requested for this migration task; permission-aware navigation was verified using real Flask responses. The existing opt-in browser regression remains the only skip.
