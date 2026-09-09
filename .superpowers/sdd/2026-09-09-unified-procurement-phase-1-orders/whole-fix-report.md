# Phase 1 whole-branch review fix round 1

## Scope and findings

Reviewed the Phase 1 design, implementation plan, SDD ledger, and immutable `review-8a523f3..7e29593.diff` package before changing code. This round addresses the two Important findings and one Minor finding recorded for whole-branch review:

- Historical carton statement and single-record purchase-order PDF GET routes now project price visibility from `user_can_view_purchase_prices()` on the server. Price-blind documents remain readable but contain no unit-price column, line amount, amount summary, total row, price header, or price value. Price-authorized output retains its existing columns and totals.
- A supplier with any `supplier_legacy_links` row is now a historically referenced supplier. The supplier delete route deactivates it while preserving its ID and links, even when it has no unified purchase order. A subsequent `init_db()` migration therefore cannot recreate it as a new active supplier.
- `purchase_view` and `purchase_manage` count as backend-module access. Manage implies view for the unified purchase pages, so a dedicated purchase manager sees a working procurement card and the same backend entry as a dedicated purchase viewer.

No arrival receipt/inventory model, arrival migration, procurement inventory, deployment, production database, or legacy-source mutation was added. Existing Phase 1 arrival behavior remains unchanged.

## Root causes

1. Both historical GET routes called legacy ReportLab builders that always rendered price columns and totals; the new server-side procurement price projection was used only by unified exports.
2. `delete_supplier` checked only `purchase_orders`. When no order referenced the supplier, it issued `DELETE`; the existing supplier-delete trigger then removed legacy links, and startup migration treated the source row as unseen and created a new active supplier.
3. `user_can_access_admin_modules()` omitted the two unified procurement permissions. The template context also treated only the view bit as readable, so a manage-only account could not follow the procurement card contract.

## RED evidence

- Real legacy PDF route tests first exercised the HTTP GET responses and extracted text from the returned PDF bytes. With no price permission, both documents exposed `单价`, `金额`/`总价`, `合计`, `1234.56`, and `8641.92`: **2 tests, 10 failing subtest assertions**.
- Supplier delete/startup regression: **1 test errored** because the original supplier ID no longer existed after POST delete followed by `init_db()`.
- Procurement-only backend navigation: **1 test with 2 failing subtests**; both view-only and manage-only accounts received `302` from `/admin` instead of `200`.

The PDF tests use real PDF text extraction: Poppler `pdftotext` when present, with macOS PDFKit as a local fallback. They assert retained non-price content as well as absence of price headers, totals, and literal values.

## GREEN evidence

- Price-blind legacy PDF GET regressions: **2/2 passed** after adding the server-derived projection.
- Historical supplier regression plus existing deletion contracts: **3/3 passed**. This covers link-only deactivation across restart, physical deletion for a supplier with no order/history, and deactivation/reactivation for an order-referenced supplier.
- Procurement-only backend/navigation regression: **1/1 passed**, including both view-only and manage-only cases, the top backend entry, the procurement card, and a successful GET to its target.
- Price-authorized route characterization passed for both legacy PDFs and retained unit price, line amount, and total values.

Final Phase 1 focused command (bundled Poppler on `PATH`):

```sh
PATH="/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin:$PATH" .venv/bin/python -m unittest tests.test_procurement_schema tests.test_procurement_permissions tests.test_suppliers tests.test_purchase_orders tests.test_purchase_order_exports tests.test_procurement_migration -v
```

Result: **105 tests in 7.612 seconds, all passed, no skips**.

JavaScript procurement regression:

```sh
/Users/derek/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --test tests/js/purchase-orders.test.js
```

Result: **5/5 passed, no skips**.

## PDF visual QA

Generated four one-page synthetic PDFs (statement/order × price-hidden/price-visible), rendered each at 120 DPI with bundled Poppler, and inspected all four PNG pages. The hidden variants retain titles, supplier metadata, dates, quantities, item details, remarks, and signature areas. Removed columns leave aligned tables with expanded remark space; there is no clipping, overlap, broken glyph, or orphaned total styling. Visible variants preserve their prior price columns, total rows, and overall geometry. All temporary PDFs and PNGs were removed after inspection.

## Changed files and boundary checks

- `app.py`: legacy carton PDF projection, historical-reference supplier deletion, procurement backend access/view implication.
- `tests/test_procurement_permissions.py`: two price-blind real-PDF GET tests, price-authorized compatibility coverage, and dedicated purchase-role navigation coverage.
- `tests/test_suppliers.py`: POST delete → `init_db()` historical-link regression.
- This report records the whole-fix RED/GREEN and scope evidence.

No full suite was run, per controller instruction. No production database, deployment target, network service, or remote branch was touched.
