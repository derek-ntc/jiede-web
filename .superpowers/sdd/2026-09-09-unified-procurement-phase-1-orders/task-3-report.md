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

## Fix round 1

### RED evidence

- Command: `.venv/bin/python -m unittest tests.test_suppliers.SupplierManagementTests.test_inactive_profile_cannot_become_default_or_conflict_on_reactivation tests.test_suppliers.SupplierManagementTests.test_supplier_only_user_can_discover_delivery_profile_entry_but_unrelated_user_cannot -v`
- Result: expected RED — 2 failures. A stopped profile could be edited with `is_default=1`, and a supplier-only account could not find the delivery-profile URL from the supplier page.

### GREEN evidence

- Command: `.venv/bin/python -m unittest tests.test_suppliers -v`
- Result: PASS — 11 supplier-management tests passed.

### Full verification

- Command: `.venv/bin/python -m unittest discover -s tests -v`
- Result: PASS — 592 discovered tests completed successfully. The suite continued to emit existing `datetime.utcnow()` deprecation warnings and one existing resource warning, with no test failures or errors.

### Fix details

- Editing an inactive delivery profile now always writes `is_default=0`; a submitted default selection is rejected with a business message. Reactivation also explicitly sets `is_default=0`, and expected SQLite integrity errors are converted to user-facing retry messages.
- The delivery-profile default checkbox is hidden for inactive rows.
- Supplier-only users now see a secondary 公司收货模板 link on the supplier page; the three primary 客商管理 tabs remain unchanged.

### Commit

Fix implementation commit: `541a9f4` (`fix: protect inactive delivery profiles`).
