# Production Notes Delivery Exports and Partner Font Size

## Goal

Add per-step production instructions, editable delivery-note exports, and larger text on the customer, supplier, and common-information pages without altering historical business data or disclosing shipment prices.

## Production process notes

- Add `remark TEXT NOT NULL DEFAULT ''` to `production_followup_process_steps` through the existing idempotent startup migration.
- Each note belongs to one step on one production follow-up card, not to a global process template. It is editable by a user with `production_followups_manage` and the existing CSRF token.
- Trim surrounding whitespace and reject notes longer than 1,000 characters. A note does not change completion, reversion, reordering, addition, or deletion rules.
- Show the note on the production-followup page and in a dedicated `备注` column on the printable production card. Existing cards receive blank notes.

## Delivery-note exports

- Keep the existing PDF route intact and add XLSX and DOCX routes for every valid persisted delivery note, for both ordinary and assembly shipments.
- Produce all formats from the same normalized saved-note projection: document number, customer, delivery/recipient metadata, drawing number, product name, specification, unit, quantity including zero, order number, ship date, and remark.
- No format may include unit price, amount, tax, invoice, or a different financial field.
- New routes require `shipped_view`; missing notes return 404 and invalidated notes return the existing 410 response before a file is generated.
- XLSX and DOCX are landscape A4 and editable. The result page presents PDF, Excel, and Word export buttons.

## Partner-page readability

- Scope larger type only to the customer, supplier, and common-information screens.
- Enlarge table text, labels, inputs, textareas, search fields, and action buttons; add modest row height. Do not change global typography or procurement pages.

## Constraints and acceptance

- Preserve all delivery-note/source/item values, delivery-note numbers, stock behavior, and current PDF compatibility.
- Use `openpyxl` for XLSX and add pinned `python-docx` to application dependencies for DOCX.
- Verify database migration, authorization, zero-quantity/remark retention, price-free artifact XML, A4 landscape setup, and visually inspect generated DOCX pages.

