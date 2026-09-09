# Phase 1 Task 3 implementation report

## RED evidence

- Command: `.venv/bin/python -m unittest tests.test_suppliers -v`
- Result: expected RED. The initial suite ran 8 tests and failed 9 assertions because each new compatibility, supplier, and delivery-profile endpoint returned `404` instead of the expected redirect or CRUD response.
- Additional focused RED command: `.venv/bin/python -m unittest tests.test_suppliers.SupplierManagementTests.test_supplier_edit_rejects_blank_code_without_changing_existing_supplier -v`
- Result: expected RED. The existing supplier remained unchanged, but the route returned the generic SQLite uniqueness message rather than rejecting an empty edit code explicitly.

## GREEN evidence

- Command: `.venv/bin/python -m unittest tests.test_suppliers tests.test_customer_billing -v`
- Result: PASS — 15 tests passed (9 supplier-management tests and 6 existing customer-billing tests).

## Full verification

- Command: `.venv/bin/python -m unittest discover -s tests -v`
- Result: PASS — 590 discovered tests completed successfully. The output included pre-existing `datetime.utcnow()` deprecation warnings and one existing resource warning, with no test failures or errors.

## Changes

- `procurement.py`: supplier payload normalization, supplier snapshots, and gap-filling `SUP-00001` code generation.
- `app.py`: unified business-partner redirects, supplier and delivery-profile CRUD/status routes, permission enforcement, and first-accessible-tab navigation.
- `templates/business_partner_tabs.html`: three permission-filtered 客商管理 tabs.
- `templates/suppliers.html`: supplier management UI with search, edit, deactivate/delete, and restore actions.
- `templates/purchase_delivery_profiles.html`: company purchase delivery-profile UI.
- `templates/customers.html`, `templates/common_info.html`, `templates/base.html`: unified tab/navigation presentation while retaining old customer/common-info CRUD routes.
- `tests/test_suppliers.py`: integration coverage for aliases, permissions, normalization/snapshots, duplicate trimming, edit validation, used-vs-unused deletion, reactivation, and one-default-profile behavior.

## Commit

Implementation commit: `632e1ca` (`feat: unify customer and supplier management`).

## Risks / deviations

- No deployment performed.
- Phone and email are preserved as trimmed display text only; they are not reformatted or silently normalized.
- The existing schema's partial unique index remains the database-level backstop for active default delivery profiles; routes clear the old active default and write the new default within one database transaction.
