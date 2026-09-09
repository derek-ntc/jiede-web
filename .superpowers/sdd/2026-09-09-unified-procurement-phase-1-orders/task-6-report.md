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

## Review fix round 1 — 2026-09-10

Fix implementation commit: `20202f24082be6db2951e50eb1fa5f6ed74dd125` (`fix: preserve procurement provenance and lock reports`). This round addresses all three Important findings under the controller's updated ledger rulings. The receiving-code-review, test-driven-development, systematic-debugging, and verification-before-completion skills guided evidence-first reproduction, minimal changes, and final verification. No subagents or production resources were used.

### Permanent source rows and editable carton dimensions

- Any existing item whose `legacy_source` or `legacy_id` is non-null is now rejected by the domain layer if omitted from an update. The rejection occurs before any order mutation and returns the explicit business error `历史来源明细不能删除；可以编辑业务字段或新增明细`, not an attachment-trigger `IntegrityError`/HTTP 500.
- The editor disables deletion for these rows and displays `历史来源（不可删除）`. JavaScript preserves the provenance while adding/reordering rows; new rows receive no provenance and remain removable. Error rendering reattaches provenance exclusively from saved database rows and restores an omitted source row so the form remains repairable. Posted metadata is never trusted.
- Carton `dimension_text` is now an allowed, visible text control labeled `尺寸说明/历史尺寸`. Real rendered-form submissions preserve migrated free text, support explicit replacement/clearing, and retain submitted text on validation failure. Numeric length/width/height remain independent; free text is not converted into invented dimensions.
- XLSX/PDF documents show the carton text in the existing `其他要求` cell alongside the original remark. Existing carton columns and widths are preserved, including both price-visible and price-hidden exports.

### Shared-lock read-only CLI

The earlier report section's statement that `mode=ro` plus `BEGIN` alone guarantees a consistent application snapshot is superseded: the application can write using SQLite `nolock=1`, so the CLI must also cooperate with its application lock.

- The CLI does not import `app`. Lock selection is explicit `--lock-path`, then `JIEDE_WRITE_LOCK_PATH`, then the application default `/tmp/jiede-web-write.lock`.
- The selected lock must already exist. It is opened as `rb`, acquired with `fcntl.flock(LOCK_EX)`, and held before opening the `mode=ro` database and throughout `BEGIN`, report reads, and connection close. Only then is the lock released. Lock contents and mtime are not modified.
- A missing/unreadable lock fails clearly and is not created. `--offline` is an explicit mutually exclusive escape hatch only for static copies or a stopped application; both help and stderr warn that concurrent `nolock` writers can make the report inconsistent. Sorted JSON remains on stdout.
- All tests use explicitly selected or environment-selected temporary lock paths. The default `/tmp` application lock was never opened by this work. All CLI database targets are synthetic temporary fixtures.

### Strict RED → GREEN evidence

1. **Source-row protection RED:** two new route tests exposed attachment deletion raising `IntegrityError`, attachment-free deletion incorrectly returning 302, and the enabled source-row delete button. The new Node test also failed (4/5 passed) because the source row could be removed. **GREEN:** both route tests and all five Node tests passed. Tests cover attachment/no-attachment HTTP 400 with complete before/after SQL dumps unchanged, disabled UI, append while retaining the source row, migration rerun creating zero orders, and complete report source accounting.
2. **Carton text RED:** real visible-form and document tests failed because the field was missing and historical text was omitted from exports. **GREEN:** both tests passed after adding the field and export rendering. Export assertions account for existing cell/PDF wrapping without changing layout. The form test covers unchanged, modified, cleared, and validation-error values with absent numeric dimensions remaining null.
3. **CLI lock RED:** three CLI tests failed on the unsupported `--lock-path`, missing-lock success, and lack of a shared-lock acquisition barrier. **GREEN:** all three passed with default/environment/override selection, missing-lock noncreation, explicit offline warning, stable sorted JSON, and real subprocess concurrency.

The concurrency test starts a writer against a temporary database with `mode=rw&nolock=1` while holding a pre-created temporary application lock. The writer commits only the legacy half, signals a barrier, and stays locked. The actual report CLI signals its lock attempt and cannot finish during a 0.2-second bounded assertion. The writer then commits the unified half and signals a second barrier while still holding the lock. After release, the CLI reports only the completed state: carton quantities **15 → 15**, amounts **4,245 → 4,245 minor units**. Database SHA-256 and nanosecond mtime captured at the completed-but-still-locked barrier are unchanged after the CLI exits. This exercises actual processes and the application's `nolock` model, not a mocked lock or a normal SQLite writer.

### Final verification and persisted QA

- Phase 1 focused suite: **100 tests in 6.625 seconds, all passed, no skips** (`tests.test_procurement_schema`, `tests.test_procurement_permissions`, `tests.test_suppliers`, `tests.test_purchase_orders`, `tests.test_purchase_order_exports`, `tests.test_procurement_migration`).
- Final complete Python suite, run once in this fix round on the final production/test changes with bundled Poppler explicitly on PATH: **664 tests in 63.951 seconds, OK, skipped=1 (663 passed)**. The sole skip remains the existing opt-in real-browser shipping-color check; no export tests skipped.
- `node --test tests/js/purchase-orders.test.js`: **5/5 passed**, no skips.
- `git diff --check` and staged diff check passed. Self-reviewed all nine changed files, including provenance reconstruction, normalization allowlists, transaction ordering, document escaping, lock lifetime/error paths, old-schema compatibility, and temporary-only test targets. No new dynamic SQL, table migration, permission expansion, legacy source mutation, or unrelated changes were introduced.

Logs are under `/private/tmp/jiede-procurement-task6-qa/`: `fix1-focused.log`, `fix1-full-suite.log`, and `fix1-node.log`.

The actual CLI was also rerun against the existing synthetic `valid.db`, explicitly using the pre-created `fix1-report.lock`. Its output is saved as `fix1-locked-report.json`, with lock acquisition diagnostics in `fix1-locked-report.stderr`. `cmp` against `valid-report.json` passed: all source counts, totals, and canonical JSON bytes are unchanged (4 migrated orders, 14 supplier links, 8 supplier conflicts, 1 attachment, 1 deferred arrival row).

| File | Unchanged SHA-256 before/after locked CLI | Unchanged mtime_ns |
| --- | --- | --- |
| Synthetic `valid.db` | `974dc3b5a39a0fbb470d4767be5e7099e74505411af8d6593dd747ae1168db8d` | `1788968068698495263` |
| Pre-created `fix1-report.lock` | `14e98e71f404e5c646ccde557961d69c8aebe2062f0ff37b05790f6e29c9671a` | `1788969602228878356` |

Changed files in this round: `app.py`, `procurement.py`, `procurement_documents.py`, `scripts/report_procurement_migration.py`, `static/purchase-orders.js`, `templates/purchase_order_form.html`, `tests/js/purchase-orders.test.js`, `tests/test_procurement_migration.py`, and `tests/test_purchase_order_exports.py`.

Residual boundaries: direct out-of-band SQL is not an authorized editor and must preserve legacy provenance; all application updates use the protected domain path. Consistent online reporting depends on every writer using the same stable lock inode/path, as the application already does. `--offline` deliberately disables that protection and must not be used against a live writable database. These are documented operational constraints, not permission to touch a live database or deploy this branch.

## Review fix round 2 — shared dotenv lock configuration

Fix implementation commit: `e6ee8725079746393e7ae87ceee1904f90793235` (`fix: share application write lock configuration`). Only the remaining Important configuration mismatch was addressed. All changes stayed in the designated worktree, and all new configuration/database/lock fixtures were temporary. No real `.env` was read or changed by the configuration QA; no default application lock, real database, deployment, or subagent was used.

### Root cause and minimal correction

The application originally loaded the project `.env` with `override=True` before reading `JIEDE_WRITE_LOCK_PATH`, whereas the report CLI read only its initial process environment. Different `.env` and shell values therefore selected different locks even though both processes used `flock` correctly.

Both consumers now call the side-effect-free `runtime_config.resolve_write_lock_path`. Its precedence is explicit CLI `--lock-path` → project-root `.env` lock value → shell environment → `/tmp/jiede-web-write.lock`. Dotenv interpolation uses the existing python-dotenv dependency and its override semantics. App resolution happens immediately before the unchanged `load_dotenv(BASE_DIR / ".env", override=True)` so both consumers resolve from the same initial inputs; the application still loads all of its other configuration exactly as before. The CLI neither imports `app` nor loads unrelated dotenv values into its environment or output.

`--env-file` is a documented CLI option selecting a different dotenv file solely for lock selection. Its help states override precedence, missing-file fallback, and non-loading of other values. An explicit lock bypasses dotenv parsing entirely. Empty/blank/control-character lock paths and paths that do not name a file raise clear `JIEDE_WRITE_LOCK_PATH` errors rather than silently selecting a fallback. A present but unreadable configuration file also fails clearly. Offline reporting retains its prior explicit warning and skips lock configuration entirely.

### RED → GREEN and concurrency evidence

- Added five tests before production changes. **RED: 5 failures.** The direct reproduction copied `app.py` and the actual CLI into a temporary project root with its own `.env`, while using the worktree's existing dependency modules. The isolated application selected temporary `dotenv.lock`; the unmodified CLI's diagnostic showed temporary `shell.lock`. Other failures covered the missing shared resolver/`--env-file` support.
- **GREEN: all 5 passed.** The isolated app preserves its other dotenv configuration. While the test process holds `dotenv.lock`, automatic CLI selection signals that exact lock and blocks until release; explicit `--lock-path` selects the shell lock and completes while the dotenv lock remains held. No `.env` uses shell then default correctly; the default is compared as a value only and never opened. Resolver calls do not mutate process environment. Empty and malformed lock values fail without fallback, and synthetic unrelated config values never appear in CLI stdout/stderr.
- The first post-implementation test run exposed a fixture-only issue: operating systems reject NUL while constructing an environment variable, before the resolver runs. The test now supplies NUL directly to the resolver's explicit-path input while keeping representable invalid values in environment tests. Production behavior was not weakened.
- Existing CLI tests now pass temporary `--env-file` paths so future real project configuration cannot affect them. The previous real subprocess `nolock` writer test also passed in both focused and final full runs, still reconciling only the completed **15/15 quantity and 4,245/4,245 minor-unit** snapshot with unchanged database hash/mtime.

### Final verification

- Focused Phase 1 + shared-configuration regression: **105 tests in 7.383 seconds, all passed, no skips**.
- Final complete Python suite, once on final production/test code with the bundled Poppler PATH: **669 tests in 64.285 seconds, OK, skipped=1 (668 passed)**. Only the existing opt-in real-browser shipping-color check skipped; PDF/export tests ran.
- Node purchase-order tests: **5/5 passed**, no skips.
- `git diff --check` and staged diff check passed. Reviewed the complete five-file change, including configuration precedence/interpolation, no app import in CLI, unchanged application dotenv loading, explicit-lock handling, error messages, and temporary fixture boundaries.
- Changed files: `runtime_config.py`, `app.py`, `scripts/report_procurement_migration.py`, `tests/test_runtime_config.py`, and `tests/test_procurement_migration.py`.

Evidence under `/private/tmp/jiede-procurement-task6-qa/`: `fix2-red.log`, `fix2-green.log`, `fix2-focused.log`, `fix2-full-suite.log`, and `fix2-node.log`.

Persisted CLI QA used only synthetic `valid.db` and temporary `fix2.env`, whose lock setting pointed to pre-created `fix1-report.lock` while the shell pointed to missing `fix2-shell-missing.lock`. The CLI selected the dotenv lock, succeeded, and left the incorrect shell lock nonexistent. `fix2-locked-report.json` is byte-identical to `valid-report.json`; `fix2-locked-report.stderr` contains only lock acquisition diagnostics, with no unrelated synthetic dotenv value. Database and lock SHA-256/mtime remain exactly the values in the round-1 table above (`valid.db`: `974dc3b5…`, `1788968068698495263`; lock: `14e98e71…`, `1788969602228878356`). Existing totals remain 4 migrated orders, 14 supplier links, 8 supplier conflicts, 1 attachment, and 1 deferred arrival row.

Operational boundary: use the same deployed project dotenv file for online CLI and application, or deliberately select their exact existing lock with `--lock-path`. No deployment/configuration-file edits were performed by this task.

## Review fix round 3 — cwd-independent configured lock identity

Fix implementation commit: `a324822367904b63580ba28510653bda1a0dc9e7` (`fix: anchor configured write locks to project root`). Read the task brief, this report, the `review-630413d..9f66839.diff` package, and the controller-relayed Important finding before changing code. The receiving-code-review, TDD, and verification-before-completion skills guided reproduction and the minimal correction. Scope remained the designated worktree and synthetic temporary fixtures; no real `.env`, production database, default lock contents, deployment, push, or subagent was used.

### Exact relative-path contract

- Precedence remains explicit `--lock-path` → dotenv lock value → shell lock value → default.
- **Non-explicit relative lock values** from dotenv, shell, or a future relative default are anchored to the application project root. An alternate `--env-file` does not change that anchor; its own relative filename still follows ordinary CLI current-directory semantics.
- **Explicit `--lock-path` relative values** are anchored to the CLI caller's current working directory, matching an operator's normal interpretation of a supplied filename. They still override all dotenv/shell configuration.
- **All returned paths** are absolute and canonicalized with `Path.resolve()`, resolving `..` and existing symlinks. Already-absolute values keep their target. The current absolute default `/tmp/jiede-web-write.lock` resolves to the same target (for example `/private/tmp/...` on macOS), not a different lock inode.
- The only executable production change is the resolver's final relative-path anchoring and canonicalization. Dotenv precedence/interpolation, other application configuration values, environment non-mutation, CLI no-app-import behavior, and lock/database open modes are unchanged. CLI help and the resolver docstring state the contract. Resolution and reporting do not create or modify lock files.

### TDD and verification

Added three regression tests before changing production behavior and updated existing absolute-path expectations to the canonical-path contract. **RED: 8 configuration tests ran with 5 failed assertions** (including two cwd cases in one test). Most importantly, with the project's `write.lock` held, the old CLI launched by absolute script path from a second cwd opened that directory's same-named decoy lock and completed immediately: the expected blocking timeout did not occur. Shell paths remained unanchored, explicit paths remained noncanonical, and the default retained its symlink spelling.

**GREEN: 8/8 configuration tests in 1.429 seconds.** A temporary copied app starts from the temporary project root and a copied actual CLI starts from a different cwd. Both now select the project's same absolute lock, and the CLI waits until that lock is released despite a valid decoy lock in its cwd. Database, dotenv, project lock, and decoy lock bytes, nanosecond mtimes, and inodes are unchanged. The shell-path test exercises two cwd values plus a symlinked project root and `state/../write.lock`; both return the same canonical path without importing `app`, mutating the environment, or creating files. Explicit `sub/../local.lock` uses the caller's directory, completes while the dotenv lock is held, preserves file bytes/mtime, and an explicit missing lock fails without creation.

Final requested focused verification:

- Phase 1 plus configuration suite: **108 tests in 8.293 seconds, all passed, no skips**, using the bundled Poppler PATH. This includes the existing real subprocess `nolock` writer/report snapshot regression, permissions, orders, exports, schema, suppliers, migration, and startup tests.
- Node purchase-order tests: **5/5 passed**, no skips.
- `git diff --check` and staged diff check passed; all three changed code/test files were self-reviewed. No unrelated changes were included.
- This narrow round requested focused verification only; the full-suite result in round 2 belongs to that earlier revision and is not claimed as a fresh full run of round 3.

Logs: `/private/tmp/jiede-procurement-task6-qa/fix3-red.log`, `fix3-green.log`, `fix3-focused.log`, and `fix3-node.log`. Changed files: `runtime_config.py`, `scripts/report_procurement_migration.py`, and `tests/test_runtime_config.py`.
