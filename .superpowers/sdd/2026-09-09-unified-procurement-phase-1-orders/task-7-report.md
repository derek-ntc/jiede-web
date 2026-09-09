# Task 7 — Phase 1 full verification

## Outcome and scope

Phase 1 verification passed on commit `cbfb55f` before this documentation-only
change. Work stayed in the designated worktree plus synthetic files under
`/private/tmp`. No deployment, push, NAS/VPS access, production database access,
or read of `data/manuals.db` was performed. The initial acceptance server was a
temporary Flask development server bound to `127.0.0.1:5077` and pointed at the
isolated rehearsal database; it was stopped after browser acceptance.

No production-code defect was found. Temporary QA helpers were removed before
the original verification commit; commit `c24095b` changed only the plan
checkboxes and this report.

## Complete automated regression

Fresh final-code Python command:

```sh
PATH="/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin:$PATH" \
  .venv/bin/python -m unittest discover -s tests -v
```

Result: **672 tests in 64.972 seconds; OK, skipped=1** — 671 passed and
zero failed/errors. The sole skip remains the pre-existing opt-in
`test_shipping_high_contrast_colors_in_real_browser`, which requires
`JIEDE_BROWSER_TESTS=1`. Procurement export tests ran. Existing UTC deprecation
and unrelated resource warnings remain baseline output. Full log:
`/private/tmp/jiede-procurement-task7-full-python.log`.

Fresh JavaScript command used the real repository glob (one test file):

```sh
node --test tests/js/*.test.js
```

Result: **5 tests, 5 passed, 0 failed/skipped/todo** in 70.975 ms. Log:
`/private/tmp/jiede-procurement-task7-node.log`.

## Isolated startup migration and read-only reconciliation

The final fixture directory is
`/private/tmp/jiede-procurement-task7-qa.RsDTdJ/`. It was constructed from the
repository's synthetic migration fixture, not copied from any user database.
Its lock file was explicitly created before database initialization. Both real
`app.init_db()` calls used that exact lock inode.

- Startup 1 returned `orders_created=4`; startup 2 returned
  `orders_created=0`. Unified order count stayed 4 after the second startup.
- The two in-process reports were identical. The actual CLI was then run twice
  with explicit `--database` and `--lock-path`; both canonical JSON outputs were
  byte-identical.
- Before and after both CLI reads, the database SHA-256 remained
  `1bdd5dc94fe8e1477c5fbc51defbde048f63d50c001d74c29f3d5905db09adf7`,
  inode `192668400`, mtime `1788972454`, size `724992`. The pre-created lock
  remained the same inode `192668399`, empty-file SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`,
  mtime `1788972454`, size 0. This is direct read-only evidence.
- `PRAGMA foreign_key_check`: 0 rows. Duplicate legacy source keys: 0.
- Carton sources: 2/2 migrated; quantity 14 -> 14; amount 3,920 -> 3,920
  minor units; no unmigrated/conflicted rows.
- Followup sources: 2/2 migrated; quantity 7 -> 7; both price values remain
  absent; no unmigrated/conflicted rows.
- Arrival source: 1 row, quantity 5 and amount 1,005 minor units, explicitly
  deferred to Phase 2 with no fabricated Phase 1 order/inventory row.
- Supplier legacy coverage: 14/14 links across all nine source tables; 0
  unlinked. Four links use the inactive unknown supplier. Eight expected
  supplier-field conflicts are reported; there are no blocking conflicts.
- Legacy attachment coverage: 1/1 linked. Product inventory balances and
  transactions remained empty during migration.

Evidence files: `startup-rehearsal.json`, `report-1.json`, `report-2.json`, and
their lock diagnostics in the fixture directory.

## Local HTTP and real-browser acceptance

The HTTP acceptance used the same isolated database and the real Flask routes.
All mutations were synthetic test records.

- Supplier CRUD: created and edited a supplier, physically deleted an unused
  supplier, deactivated a referenced supplier, and restored it. Final referenced
  supplier state is `T7-SUP / Task7供应商已编辑 / 王五 / active`.
- Company delivery data: created, edited, deactivated, and restored a profile;
  the final recipient/default remark are `赵六已编辑 / 上午送达`.
- Four category routes created real orders. Raw material, carton, and
  outsourcing each retained two items; other procurement retained 70 items.
  Each detail route returned 200 and rendered its category-specific content.
- Combined supplier/order/item/date/status-capable list filters were exercised
  with a positive exact match (`PO-20260910-0004`) and a negative item search.
- Permission acceptance proved a price-blind purchase manager sees no price in
  HTML or XLSX, and an unrelated operator is redirected from purchase and
  supplier pages.
- The fresh XLSX response reopened as sheet `采购订单` with landscape
  orientation, A4 (`paperSize=9`), `fitToWidth=1`, `fitToHeight=0`, print area
  `'采购订单'!$A$1:$F$83`, and repeated row `$9:$9`.
- The fresh PDF response was `application/pdf` and `pdfinfo` reported 5 pages
  for the 70-line order, proving continuation pagination on the final code.
- `/admin/purchase-followups` redirects to unified other procurement and
  `/admin/carton-purchases` redirects to unified carton procurement. The
  historical arrival page remains available with HTTP 200 as required by the
  Phase 1 ruling.
- The canonical customer page remained HTTP 200 and its business-partner alias
  redirected to it. A full-row customer snapshot was byte-for-value identical
  before and after procurement acceptance.
- A product-inventory sentinel balance of 37 units remained exactly unchanged,
  and inventory transactions remained empty after all four purchase orders.

The real in-app browser then logged in through `/admin/login` and visibly
inspected: the dashboard unified procurement/business-partner navigation;
supplier management; company delivery profiles; the other-procurement list with
all four category tabs, historical-arrival tab, and filters; the 70-row order
detail with supplier/delivery snapshots and Excel/PDF actions; and the unchanged
customer page. Server logs show 200 responses for those pages and the expected
302 login transition. The temporary browser tab and server were closed.

Fresh HTTP evidence is in `http-acceptance.json`; generated artifacts are
`acceptance-order.xlsx` and `acceptance-order.pdf` in the fixture directory.

## Honest visual boundary

The fresh browser run used the in-app browser's compact viewport, so it is a
visible routing/content check rather than a desktop print-preview review. This
Task 7 run did not reopen the generated workbook in Microsoft Excel or manually
inspect every page of the fresh five-page PDF. For those visual details it uses
the accepted Task 5 evidence already recorded in this ledger: 49 final pages
viewed by the implementer plus 32 pages independently reviewed, covering all
four categories, price-visible/hidden variants, long pagination, CJK rendering,
large exact money, and sanitized control characters. Per the Task 7 brief, the
Spreadsheet/PDF one-time markers were not repeated. Target-VPS/browser-specific
font substitution and physical printer output remain deployment acceptance,
which was explicitly out of scope.

## Review evidence correction round 1 — 2026-09-10

This documentation-only correction addresses the two Important evidence
findings from the independent review of `cbfb55f..c24095b`. The paths below are
the direct audit evidence for CLI read-only behavior and the missing browser
category/filter/role matrix; the earlier fixture remains the evidence for the
broader CRUD, export, redirect, customer, and inventory checks. No production
code changed, and no full-suite rerun was needed because the correction only
captures evidence and documents it.

### Direct CLI proof on a sealed database

A new database and lock were created under the dedicated `cli/` evidence
directory. The lock existed before either real `app.init_db()` startup and kept
the same inode. The first startup reported `orders_created=4`, the second
reported `orders_created=0`, and the final order count was 4. After setup, the
database was used only by the read-only report CLI and filesystem digest/stat
reads. All later HTTP and browser work used the separate `browser/browser.db`.

Both report invocations were executed from the worktree with this exact command:

```sh
.venv/bin/python scripts/report_procurement_migration.py \
  --database /private/tmp/jiede-procurement-task7-fix1-evidence/cli/rehearsal.db \
  --lock-path /private/tmp/jiede-procurement-task7-fix1-evidence/cli/write.lock
```

The command transcript is preserved at
`/private/tmp/jiede-procurement-task7-fix1-evidence/cli/commands.txt`. Both JSON
stdout streams, both stderr lock diagnostics, and the pre/post filesystem
measurements are preserved beside it. Direct results:

- Database pre/post SHA-256:
  `17ef9d80701432ce5f28e1f3d07e6923954fe67223e7bf50aeae454518699bc8`.
- Database pre/post metadata: inode `192671758`, mtime `1788973902`,
  size `724992`.
- Lock pre/post SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Lock pre/post metadata: inode `192671756`, mtime `1788973902`, size `0`.
- `pre.sha256` and `post.sha256` have the same SHA-256
  `9a4a7ba232f437bc5cc69fb165f8f0481149dcb237782f631863698347de83e0`;
  `pre.stat` and `post.stat` have the same SHA-256
  `d7b6d569cc3ef8fcc25c49cf2e9fb7829f3b78fd12ed73228db540bd4cd4a65c`.
- Both report stdout files have the same SHA-256
  `9adb335cb6db8eac93ad22120cd27dc56fa878ff81d1dbd84b1704be91637d91`.
  Both stderr files contain acquisition of the exact pre-created lock path.

The identical database and lock digests, inode, mtime, and size before and after
the two captured invocations are direct evidence that those CLI invocations did
not change either file. The JSON stdout directly supports the prior
reconciliation results (zero foreign-key
violations and duplicate keys; carton 14/14 quantity and 3,920/3,920 minor-unit
amount; followup 7/7 quantity; 14/14 supplier links; 1/1 attachment; one arrival
row deferred) without relying on an asserted boolean or a database later changed
by acceptance actions. Task 7 Step 2 therefore remains checked.

### Auditable browser and permission matrix

Browser acceptance used a second isolated database and lock on localhost port
5078. The structured CUA observation record is preserved at
`/private/tmp/jiede-procurement-task7-fix1-evidence/browser/cua-browser-trace.json`.
It records the observed URLs, headings, visible orders/suppliers/totals,
filtered row count, permission redirects, and flash text:

- Administrator login reached `首页总览` with `采购` and `客商管理` visible.
  The real browser visited all four lists: raw material showed
  `PO-20260910-0001`; carton showed `PO-20260910-0002` and total 35.50;
  outsourcing showed `PO-20260910-0003` and total 111.00; other showed
  `PO-20260910-0004` and total 30.90. Every page showed the synthetic supplier.
- The browser submitted the other-procurement keyword filter
  `q=T7-OTHER-FILTER`; the resulting URL retained the query and displayed one
  matching order row. The parallel HTTP evidence in
  `browser/http-trace.log`, `browser/admin-filter.headers.txt`, and
  `browser/admin-filter.body.html` additionally preserves the combined
  supplier/order/item/status/purchase-date/expected-date query, its 200 response,
  and its selected form values. The negative-filter response is preserved in
  `browser/admin-filter-negative.body.html`; it omits the synthetic order, and
  `browser/admin-filter-negative.forbidden.txt` is zero bytes.
- The price-blind buyer saw the other list and order 4 detail with both item
  names but no unit-price or amount fields. The corresponding HTTP bodies are
  `browser/blind-other.body.html` and `browser/blind-detail.body.html`; the
  saved `browser/blind-forbidden.txt` is zero bytes. Supplier management
  redirected to `/admin` and showed the no-permission flash.
- The outsider requested raw-material, carton, outsourcing, filtered other, and
  supplier management. Every request redirected to `/admin`, rendered
  `后台管理`, and showed `当前账号没有权限访问该模块`.

`/private/tmp/jiede-procurement-task7-fix1-evidence/browser/server.log`
corroborates the corresponding 200/302 request sequence for all four
administrator pages, the browser filter, the price-blind list/detail and
supplier denial, and all five outsider denials. The structured CUA record is an
observation log, not a screenshot or video replay; the persisted HTTP bodies and
headers provide separately inspectable rendered-content/status evidence for the
four-category administrator matrix, the combined positive and negative filters,
the price-blind list/detail, and one outsider purchase denial. The server log and
CUA record cover the remaining supplier and outsider route denials.

The server log also records an incidental exploratory `/admin/suppliers` 404
before the correct supplier-management route was tested; that nonexistent URL
is not treated as product evidence. The logged favicon 404 is likewise
unrelated. Together with the earlier synthetic CRUD, multi-row, export,
customer-preservation, redirect, and inventory-sentinel acceptance, this fills
the missing four-category/filter/role matrix. Task 7 Step 3 therefore remains
checked.

### Persisted evidence paths and existence verification

Evidence root:
`/private/tmp/jiede-procurement-task7-fix1-evidence/`.

The path-and-SHA inventory for all 62 payload files is
`/private/tmp/jiede-procurement-task7-fix1-evidence/evidence-manifest.sha256`.
Running `shasum -a 256 -c` against it checked every enumerated path as `OK`; the
full check output is
`/private/tmp/jiede-procurement-task7-fix1-evidence/evidence-manifest-check.log`.
The persistent files are grouped as follows, with every individual body/header/
visible-extract path enumerated in the manifest:

- CLI commands and startup:
  `cli/commands.txt`, `cli/setup.log`, `cli/startup-results.json`.
- Sealed CLI inputs: `cli/rehearsal.db`, `cli/write.lock`.
- Direct read-only measurements: `cli/pre.sha256`, `cli/post.sha256`,
  `cli/pre.stat`, `cli/post.stat`, `cli/evidence-file.sha256`.
- CLI invocation evidence: `cli/report-1.stdout.json`,
  `cli/report-1.stderr.log`, `cli/report-2.stdout.json`,
  `cli/report-2.stderr.log`, `cli/report-stdout.sha256`.
- Browser fixture and process evidence: `browser/setup.json`,
  `browser/setup.log`, `browser/browser.db`, `browser/write.lock`,
  `browser/server.log`, `browser/http-trace.log`,
  `browser/cua-browser-trace.json`.
- Administrator HTTP evidence: `browser/admin-login.*`,
  `browser/admin-raw.*`, `browser/admin-carton.*`,
  `browser/admin-outsourcing.*`, `browser/admin-other.*`,
  `browser/admin-filter.*`, and `browser/admin-filter-negative.*`.
- Price-blind evidence: `browser/blind-login.*`, `browser/blind-other.*`,
  `browser/blind-detail.*`, `browser/blind-visible.txt`,
  `browser/blind-forbidden.txt`, and
  `browser/blind-forbidden.byte-count.txt`.
- Outsider evidence: `browser/outsider-login.*`, `browser/outsider-other.*`,
  `browser/outsider-block.visible.txt`.
- Initial health response and the three synthetic session-cookie jars are also
  enumerated in the manifest. They contain only temporary localhost fixture
  state, no real credentials or production data.

No repository-local file matching `scripts/.task7_fix1_*_tmp.py` remains. No
file under `/private/tmp/jiede-procurement-task7-fix1-evidence/` was deleted.
Repository changes in this correction remain documentation-only.
