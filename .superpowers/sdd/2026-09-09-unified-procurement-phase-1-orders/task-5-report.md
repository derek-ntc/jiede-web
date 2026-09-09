# Task 5 report: purchase order exports

The initial implementation record below is historical. The **Review fix round 1** section at the end supersedes its column, PDF splitting, amount precision, test-count and final-QA claims.

## Result and scope

Implemented and committed as `cfe2c63f502d692adadfc993df1179e5ba96c3bd` (`feat: export purchase orders`). Work was confined to `/Users/derek/Documents/jiede-web/.worktrees/unified-procurement`; QA artifacts are outside Git. No deployment, dependency additions, main-workspace source changes, or subagents.

Changed files:

- `procurement_documents.py`: one category-aware, price-whitelisted view model; openpyxl XLSX and reportlab PDF builders.
- `app.py`: guarded `/admin/purchases/orders/<id>/export.xlsx` and `.pdf` routes, implemented by one format dispatcher.
- `templates/purchase_order_detail.html`: Excel download, existing floating PDF preview integration, and PDF download links, omitted for cancelled orders.
- `tests/test_purchase_order_exports.py`: 14 builder/route tests covering four categories, snapshots, typed cells, print settings, price omission, text escaping, permissions, disposition, pagination and boundary cases.

## Contract decisions

- Followed Controller rulings: existing `openpyxl==3.1.5` and `reportlab==4.2.2`; application/VPS needs no Node, LibreOffice, Poppler, or new Python package for export.
- Both renderers consume the same whitelisted view model. Supplier/delivery fields are saved order snapshots. Only company footer contact data comes from the current `reconciliation_company_profile`; absent profile defaults to 宁波市杰德机械科技有限公司.
- Raw material columns follow the no-price raw-material layout in the ruling even for a price-authorized user. Other categories expose price columns only when the server-derived permission allows them. Optional blank unit/combined detail columns are omitted. Stored line totals are used, and an incomplete set of prices is labelled 未完整录价 rather than treated as zero.
- Hidden-price output contains no price headers, per-line amounts, aggregate total, or price fields stored in hidden workbook parts. No query parameter can enable prices. Responses are private/no-store.
- Routes require `purchase_view`; invalid/missing IDs return 404; cancelled orders return 409 and no artifact. XLSX is always an attachment; PDF is inline unless `download=1`. Filename uses the saved order number and `-purchase-order` suffix.
- XLSX uses numeric/date/currency cells, literal text rather than executable formulas for supplier/item input, landscape A4, one-page width/unlimited height, repeat detail headers, print area, modest margins, CJK text, and continuation rows for unusually long descriptions.
- PDF uses landscape A4, CJK font discovery consistent with the application without importing `app.py`, repeated table headers and in-row splitting for very long text. The fallback is STSong-Light, never Chinese-incapable Helvetica. Contact/footer content flows inside the document frame; only page numbers occupy the fixed footer area.

## TDD evidence

Initial RED command:

```text
.venv/bin/python -m unittest tests.test_purchase_order_exports -v
Ran 21 tests
FAILED (failures=4, errors=7)
```

The 7 new builder tests failed because `procurement_documents` did not exist. The 4 new route/link tests failed because export routes/buttons did not exist (404/missing links). Ten existing route tests were incidentally collected by importing the TestCase symbol; this was corrected to a module import before GREEN, without changing production expectations.

After implementation, two test-fixture issues were corrected: openpyxl rereads A4 as integer `9` whereas its constant is string `"9"`; the hidden-price sentinel initially coincided with the fixture company telephone number. Both were assertion/fixture mistakes, not document leakage.

Additional RED/GREEN boundary checks:

- Maximum allowed quantity `2147483647` exposed `:g` formatting rounding to `2.14748e+09`; fixed by preserving the exact numeric representation and widening the quantity column.
- A 4,000-character detail remark successfully crosses PDF pages and Excel continuation rows while preserving the ending text; no row exceeds Excel's height limit.
- Visual QA exposed a too-narrow carton purchase-date cell (`###`). A failing print-width regression was added, then the date cell was given a three-column presentation span while staying date-typed.
- The PDF delivery/company block is kept together when it fits on one page; the regression checks address, recipient and final email stay together for the representative 32-line case.

Final focused GREEN command (Poppler binary directory prepended to PATH):

```text
PATH="/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin:$PATH" .venv/bin/python -m unittest tests.test_purchase_order_exports tests.test_procurement_permissions -v
Ran 20 tests in 1.691s
OK
```

This is 14 export tests plus 6 procurement permission tests, with no skips.

## Full verification

Ran the full Python suite exactly once after focused testing and final production changes:

```text
PATH="/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin:$PATH" .venv/bin/python -m unittest discover -s tests -v
Ran 630 tests in 61.815s
OK (skipped=1)
```

629 passed; the one skip is the existing `test_shipping_high_contrast_colors_in_real_browser`, which requires `JIEDE_BROWSER_TESTS=1` and Node/Playwright. No export test was skipped. Existing `datetime.utcnow()` deprecation warnings remain unchanged.

Full log: `/private/tmp/jiede-procurement-task5-qa/full-suite.log`.

`git diff --check` and staged `git diff --cached --check` passed. Reviewed all four changed files for snapshot/permission boundaries, safe text handling, page setup, amount formatting, and scope. No unrelated changes were included. Git index/commit operations required the normal sandbox escalation for this worktree's metadata and then succeeded.

## Artifact and visual QA

All files below are under `/private/tmp/jiede-procurement-task5-qa/` and are not committed. The temporary `generate_samples.py` builds five representative sample pairs using the committed builders. Samples include supplier code with leading zeros, Chinese contacts and remarks, date cells, dimensions, 32-line orders with multi-line detail remarks, and priced/hidden variants.

| Sample basename | Lines | XLSX | Direct PDF pages viewed | XLSX-to-PDF pages viewed |
| --- | ---: | --- | ---: | ---: |
| `raw_material-hidden` | 2 | `raw_material-hidden.xlsx` | 1 | 1 |
| `carton-priced` | 2 | `carton-priced.xlsx` | 1 | 1 |
| `outsourcing-priced` | 2 | `outsourcing-priced.xlsx` | 1 | 1 |
| `other-priced` | 32 | `other-priced.xlsx` | 1–4 | 1–5 |
| `other-hidden` | 32 | `other-hidden.xlsx` | 1–4 | 1–4 |

Direct PDFs are `<basename>.pdf`. LibreOffice-converted PDFs are `xlsx-rendered/<basename>.pdf`. Rendered images are `<basename>-pdf-<page>.png` and `<basename>-xlsx-<page>.png`. Every final image was opened using `view_image`: 11 direct-PDF pages plus 12 XLSX-rendered pages, 23 pages total.

Reopened every XLSX with openpyxl and checked:

- Stable sheet `采购订单`; `A1` = 采购订单; `A2` = `PO-20260909-0042` as text; `B4` rereads as `2026-09-09 00:00:00` and remains a typed date.
- Landscape A4, `fitToWidth=1`, `fitToHeight=0`, print area and repeating header rows.
- Raw material print area `A1:I18`, header row 8; carton `A1:J20`, row 9; outsourcing `A1:I20`, row 9; priced other `A1:H50`, row 9; hidden other `A1:F48`, row 8.
- Category-specific columns and typed quantity/money/date content; priced 32-line sample total is ¥13,339.20. Hidden samples have no price/total cells or currency amounts.

Conversion/render commands used the already-present bundled tools:

```text
soffice -env:UserInstallation=file:///private/tmp/jiede-procurement-task5-qa/lo-profile --headless --convert-to pdf --outdir /private/tmp/jiede-procurement-task5-qa/xlsx-rendered <sample.xlsx>
pdftoppm -scale-to 1400 -png <sample.pdf> <output-prefix>
```

Initial LibreOffice conversion had missing Chinese glyphs because the bundled headless renderer's fontconfig did not discover the macOS fonts and used unwritable default caches. Root cause was isolated from the direct PDF, whose Chinese rendering was correct. A QA-only `fonts.conf` points to `/System/Library/Fonts`, `/System/Library/Fonts/Supplemental`, and a temporary font cache; rerunning with `FONTCONFIG_FILE=/private/tmp/jiede-procurement-task5-qa/fonts.conf` fixed the problem. No system or dependency files were modified. PDF selected `PurchaseCJK2` (Arial Unicode.ttf).

Final visual conclusion: centered title/order number, complete Chinese text, clear dark body text, wrapped descriptions, visible dates/currency, restrained horizontal rules, repeated detail headings on continuation pages, and readable margins/page numbers. No broken Chinese glyphs, clipped rows, overlapping cells, or body/footer collisions remain. Excel uses its normal print pagination, so the delivery/contact block can span pages; PDF keeps the ordinary-size block together. Sheet repeat headers also appear on an Excel footer-only final page, as expected from the worksheet's print-title setting.

## Risks / deviations

- Runtime uses only the already-pinned Python packages as directed. Artifact-tool/Node were not added; the controller had already run both skill markers, and they were not repeated. Spreadsheet/PDF skills governed layout and reopen/render/visual verification.
- Poppler `pdftotext` is a test/QA-only executable. PDF text tests explicitly skip if absent on another developer machine; here PATH included the existing bundled executable, and all export tests ran. No runtime dependency is introduced.
- XLSX page count can vary with the recipient's installed CJK font and spreadsheet application's font substitution. Verified locally with Arial Unicode MS and bundled LibreOffice. PDF font discovery retains a registered STSong-Light CJK fallback on hosts without a loadable TTF; target VPS/browser-specific rendering remains part of eventual deployment acceptance, not performed here.
- Company footer is intentionally current profile data, not a historical snapshot, per Controller ruling. Supplier and delivery remain immutable saved snapshots.
- No application schema, inventory behavior, migration, deployment, or financial workflow was changed.

## Review fix round 1

Addressed all four Important findings and the three requested Minor improvements. Scope remained the isolated worktree; no dependencies, deployment, subagents or repeated skill markers. Receiving-code-review, TDD, systematic-debugging, verification-before-completion, Spreadsheet and PDF skills guided regression-first changes and the reopened/rendered visual checks.

### Changes and RED evidence

- Raw-material and carton column definitions now omit unit even when a saved item supplies one. Exact-column tests failed first on the extra 单位 column. Outsourcing/other retain the optional unit contract.
- Reproduced the 4,000-character remark failure: page 7 had nine table headers and later pages could contain only a header. PDF cells are now pre-split into bounded continuation paragraphs; the table uses row-only splitting (`splitInRow=0`) and `repeatRows=1`. The regression asserts at most one header per page, actual remark content on every detail page, all 999 repetitions plus the ending marker, and the complete delivery/company block at the end.
- Excel precision RED used a legal item: unit minor amount `4294967298`, quantity `2147483647`, line minor amount `9223372036854775806`. The old workbook reread the line as `9.223372036854776e16`, and a 500-line total as `4.611686018427388e19`, losing minor units. Now amounts that survive a 15-significant-digit numeric round-trip remain numeric; all others become exact formatted RMB text. Tests require line/total `¥92,233,720,368,547,758.06` and 500-line total `¥46,116,860,184,273,879,030.00`. The safe unit price remains numeric `42949672.98`.
- Shared text sanitization normalizes CR/LF, turns tabs into readable spaces, and replaces control/surrogate/noncharacter code points with spaces before either renderer. Builder RED reproduced openpyxl `IllegalCharacterError`; route regression persists controls in SQLite and requires both formats to return 200 with readable Chinese words preserved. User text still cannot become Excel formulas or ReportLab markup.
- Added worksheet vertical separators and joined delivery address/recipient into one printable row, backed by a failing border/delivery-pair test. Scoped export responses now receive `private, no-store` even for 404 (including non-integer IDs) and 409; error-cache tests failed first.
- Visual QA found a second amount-layout issue after precision was fixed: the legal safe unit price displayed `###`, and the exact total wrapped its final digit onto a separate line. A width-contract test failed (`14.0606 < 17`), then money columns were sized for formatted values and the total received a wider merged amount span. Final rerender shows the full single-line values without hashes or missing digits.

Initial focused revision RED: 21 tests, 12 failed subtest assertions and 2 errors, captured in `/private/tmp/jiede-procurement-task5-qa/fix1-red.log`. Subsequent legal-boundary and visual-width RED checks were run before their respective fixes. Final focused GREEN: 29 tests in 2.232 seconds, no skips (`tests.test_purchase_order_exports` plus `tests.test_procurement_permissions`); log `fix1-focused.log`.

### Revised verification and commit

Fix commit: `2bddfb4` (`fix: harden purchase order document exports`). Changed production/test files: `procurement_documents.py`, `app.py`, `tests/test_purchase_order_exports.py`. No template or dependency changes in this fix round.

After final visual QA, ran the full Python suite **once in this review round**:

```text
PATH="/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin:$PATH" .venv/bin/python -m unittest discover -s tests -v
Ran 639 tests in 60.764s
OK (skipped=1)
```

638 passed. The sole skip is the same pre-existing real-browser shipping color test requiring `JIEDE_BROWSER_TESTS=1`; all export tests executed. Full log: `/private/tmp/jiede-procurement-task5-qa/fix1-full-suite.log`. Existing datetime deprecation warnings are unchanged. Reviewed the complete diff; `git diff --check` and `git diff --cached --check` passed before the fix commit. Only this report remains for the separate documentation commit.

### Revised artifact and visual QA

Temporary generator `generate_fix1.py` produced the following final files under `/private/tmp/jiede-procurement-task5-qa/`. Every listed PDF page was rendered using `pdftoppm`; every PNG was actually opened with `view_image`. XLSX files were first reopened with openpyxl, then converted with the existing headless `soffice` and the QA-only fontconfig described above.

| Final sample basename | Direct PDF pages viewed | XLSX-rendered pages viewed |
| --- | ---: | ---: |
| `fix1-raw_material-hidden` | 1 | 1 |
| `fix1-carton-priced` | 1 | 1 |
| `fix1-outsourcing-priced` | 1 | 1 |
| `fix1-other-priced` | 1–4 | 1–5 |
| `fix1-other-hidden` | 1–4 | 1–4 |
| `fix1-huge-money` | 1 | 1 |
| `fix1-controls` | 1 | 1 |
| `fix1-long-remark-hidden` | 01–10 | not generated (PDF pagination regression) |
| `fix1-long-remark-priced` | 01–12 | not generated (PDF pagination regression) |

There are 35 direct-PDF PNGs and 14 XLSX-rendered PNGs, **49 final pages viewed**. Files use `<basename>.pdf` / `.xlsx`, `xlsx-rendered/<basename>.pdf`, and `<basename>-pdf-<page>.png` / `-xlsx-<page>.png`; the two extreme PDF filenames use zero-padded page numbers. QA generation/reopen/header-count evidence is in `fix1-qa.log`.

Reopened XLSX checks confirm typed purchase date `2026-09-09`, numeric ordinary money, exact-text unsafe money, leading-zero snapshot codes, landscape A4, one-page width/unlimited height and repeated headings. Updated print areas/header rows: raw `A1:H17`/8, carton `A1:I19`/9, outsourcing `A1:I19`/9, priced other `A1:H49`/9, hidden other `A1:F47`/8, huge money `A1:H18`/9, controls `A1:F16`/8.

Final visual conclusion: Chinese and the sanitized readable separators render correctly; raw/carton have no unit; amount digits are complete; no clipping, overlap, duplicate headers within a page, header-only direct-PDF pages, or page-number/footer collisions. Excel's delivery address and recipient now stay together in the representative long order, and vertical rules visibly distinguish neighboring columns. The original 4,000-character test checked only overall text retention and missed per-page header defects; this round explicitly tests and visually checks those page-level conditions.

### Remaining presentation tradeoffs

- Conservative continuation rows preserve all 4,000 remark characters but produce 10 hidden-price pages or 12 priced pages in this deliberately extreme sample, with unused cells/whitespace to the left of continued remarks. The hidden-price PDF's last page contains the complete delivery/company block and no table header; it is not an empty/header-only page.
- Ordinary PDF delivery/company blocks stay together, so a final contact-only page can occur. XLSX print titles still repeat on a final page containing totals/contact information; the workbook-wide repeating-header contract was retained. Extremely long delivery fields can still continue across rows/pages, rather than being clipped.
- Unsafe large monetary cells intentionally become exact RMB text, so spreadsheet consumers must not assume every monetary cell is arithmetic-ready numeric data. This is the Controller's precision ruling, not an accidental type conversion.
- QA uses local CJK fonts and existing LibreOffice/Poppler only; no Node or renderer executable is required by the VPS application. The prior font-substitution/deployment limitations still apply.
