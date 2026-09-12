# Shared Products, Supplier Bank Details, and Delivery Note Format

## Goal

Make a product a single shared record when its product drawing number, name, and specification are the same, even when it is supplied to more than one customer. Its inventory and warehouse locations must remain one combined balance. Add supplier payment-bank fields and refine the Excel/PDF delivery-note layout.

## Scope and decisions

### Product identity and customer applicability

`manuals` remains the canonical product table. A product is eligible for automatic reuse only when the normalized drawing number, product name, and specification (the existing `supplier` column, labelled “规格型号” in the UI) are all equal. Customer is no longer part of product identity.

Create a `product_customers` relation with `manual_id`, `customer_id`, timestamps, and a unique `(manual_id, customer_id)` constraint. It records every customer that may use a product. `manuals.customer` remains temporarily as a legacy display/default value during migration, but all customer filtering and customer-specific assembly/production/dispatch validation must use `product_customers` or the order's explicit customer.

An imported BOM must find a product by the three-part identity across all customers. It must create the missing customer relation, then create or update that customer's assembly-component relationship. The relation therefore allows one product to have different assembly drawing numbers and per-set quantities for different customers.

### Existing duplicate migration

At startup, transactionally group product rows by the normalized three-part identity. Keep the oldest product ID as the canonical record. For each duplicate:

- move or merge inventory balances by `(canonical manual_id, location_id)` and add quantities;
- move inventory transactions, orders, production follow-ups, assembly shipment items/allocations, delivery/reconciliation references, product files, materials, process/inspection records and other `manual_id` references to the canonical ID;
- merge assembly-component rows without losing customer-specific definitions; if a duplicate relation conflicts with an existing canonical relation for the same customer, assembly drawing number and per-set quantity, preserve one row and report a deterministic migration warning rather than silently overwriting a different quantity;
- union both products' customer relations;
- retain the canonical technical fields and files, add non-duplicate technical files, then remove the duplicate product only after references have been moved.

The migration is idempotent and runs in the existing initialization transaction. Existing historical order, shipment and delivery snapshots keep their recorded customer values; only their product reference changes to the canonical record.

### Customer-facing workflows

Product list and inventory screens display a product once. Their customer filters use the relation table, so selecting either linked customer finds the same product. Inventory remains shared and shows its one total and locations.

Assembly selection and production follow-up resolve a selected customer through `product_customers`; they only show that customer's BOM/order rows. Product orders retain their own `customer` snapshot, so orders, shipment allocation and documents remain customer-specific.

### Supplier payment details

Add nullable/empty-safe `payment_bank_name`, `payment_bank_branch_no`, and `payment_account_no` fields to the unified `suppliers` table. The supplier create/edit page and search expose them. Existing purchase snapshots are left unchanged; future documents may use these fields when a supplier payment block is required.

### Delivery-note exports

Excel and PDF remain portrait A4 with the same metadata and columns. Increase the left label column and the principal drawing/name/specification columns while keeping the total usable width within A4 margins and one-page width.

Keep the dark blue-grey title/header styling. Remove all green fills from the company strip, assembly metadata labels, quantity headers, and signature footer; those cells become white while retaining black borders and readable text. PDF follows the same styling and column proportions as Excel.

## Error handling and data safety

The duplicate migration must be atomic: an error rolls back all data moves. It must not merge products that only share a drawing number but differ in name or specification. Import validation reports a single identity conflict when one spreadsheet contains inconsistent values for a product.

Customer-specific operations reject a product that has not been linked to the selected customer. Supplier bank fields accept normal account characters after trimming, with a 200-character maximum for each field.

## Verification

- Product-import tests: same three-part product imported for two customers creates one `manuals` row, two customer relations, separate customer BOM links, and one combined inventory balance.
- Migration test: seeded duplicate product rows, inventory in the same/different locations, orders and BOM rows are merged to one canonical product without lost total stock or customer associations.
- Inventory/product-list tests: each linked customer filter returns the same canonical product once.
- Supplier tests: create/edit/search preserve bank name, branch number and account number.
- Delivery export tests: Excel print area stays within A4, widened columns are present, and no delivery-note green fill is emitted; rendered PDF matches the white-body/dark-header palette.
