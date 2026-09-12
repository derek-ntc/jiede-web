"""Schema and strict value parsing for independent purchase inventory."""

import re

from procurement import PURCHASE_CATEGORIES


_PURCHASE_INVENTORY_QUANTITY_MAX = 2_147_483_647
_CATEGORY_SQL = ",".join(f"'{category}'" for category in sorted(PURCHASE_CATEGORIES))


def _ensure_restrictive_reference(
    conn, *, child: str, column: str, parent: str
) -> None:
    """Mirror a restrictive foreign key for FK-disabled production connections."""
    relation = f"{child}_{column}"
    message = f"{child}.{column} references no {parent} row"
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_{relation}_require_insert
        BEFORE INSERT ON {child}
        FOR EACH ROW WHEN NEW.{column} IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM {parent} WHERE id = NEW.{column}
        )
        BEGIN SELECT RAISE(ABORT, '{message}'); END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_{relation}_require_update
        BEFORE UPDATE OF {column} ON {child}
        FOR EACH ROW WHEN NEW.{column} IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM {parent} WHERE id = NEW.{column}
        )
        BEGIN SELECT RAISE(ABORT, '{message}'); END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_{relation}_restrict_parent_delete
        BEFORE DELETE ON {parent}
        FOR EACH ROW WHEN EXISTS (
            SELECT 1 FROM {child} WHERE {column} = OLD.id
        )
        BEGIN SELECT RAISE(ABORT, '{parent} row has dependent {child} records'); END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_{relation}_restrict_parent_id_update
        BEFORE UPDATE OF id ON {parent}
        FOR EACH ROW WHEN NEW.id <> OLD.id AND EXISTS (
            SELECT 1 FROM {child} WHERE {column} = OLD.id
        )
        BEGIN SELECT RAISE(ABORT, '{parent} row has dependent {child} records'); END
        """
    )


def parse_purchase_category_slug(value: str) -> str:
    """Return an exact unified-procurement category slug."""
    if not isinstance(value, str) or value not in PURCHASE_CATEGORIES:
        raise ValueError("采购类别无效")
    return value


def parse_purchase_inventory_quantity(
    value: object, *, allow_zero: bool = False
) -> int:
    """Parse a bounded integer quantity, optionally accepting zero."""
    pattern = r"\d+" if allow_zero else r"[1-9]\d*"
    if isinstance(value, bool) or not re.fullmatch(pattern, str(value or "")):
        raise ValueError("数量必须为非负整数" if allow_zero else "数量必须为正整数")
    quantity = int(value)
    if quantity > _PURCHASE_INVENTORY_QUANTITY_MAX:
        raise ValueError("数量超出允许范围")
    return quantity


def ensure_purchase_inventory_tables(conn) -> None:
    """Create purchase receiving and independent-inventory storage."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no TEXT NOT NULL UNIQUE,
            purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
            received_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('posted','voided')),
            /* CHECK status IN ('posted','voided') documents the public constraint form. */
            remark TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            created_by TEXT NOT NULL,
            posted_by TEXT NOT NULL,
            voided_by TEXT,
            created_at TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            voided_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_receipt_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id) ON DELETE RESTRICT,
            purchase_order_item_id INTEGER NOT NULL REFERENCES purchase_order_items(id) ON DELETE RESTRICT,
            item_name TEXT NOT NULL,
            drawing_no TEXT NOT NULL DEFAULT '',
            material TEXT NOT NULL DEFAULT '',
            dimension_text TEXT NOT NULL DEFAULT '',
            surface TEXT NOT NULL DEFAULT '',
            spec TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            length REAL,
            width REAL,
            height REAL,
            thickness REAL,
            actual_quantity INTEGER NOT NULL CHECK (typeof(actual_quantity) = 'integer' AND actual_quantity > 0),
            qualified_quantity INTEGER NOT NULL CHECK (
                typeof(qualified_quantity) = 'integer'
                AND qualified_quantity >= 0
                AND qualified_quantity <= actual_quantity
            ),
            location_id INTEGER NOT NULL REFERENCES warehouse_locations(id) ON DELETE RESTRICT,
            invoice_status TEXT NOT NULL CHECK (invoice_status IN ('not_required','pending','invoiced')),
            remark TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS purchase_inventory_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_no TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL CHECK (category IN ({_CATEGORY_SQL})),
            origin_receipt_item_id INTEGER NOT NULL REFERENCES purchase_receipt_items(id) ON DELETE RESTRICT,
            source_lot_id INTEGER REFERENCES purchase_inventory_lots(id) ON DELETE RESTRICT,
            source_kind TEXT NOT NULL CHECK (source_kind IN ('receipt','transfer')),
            item_name TEXT NOT NULL,
            drawing_no TEXT NOT NULL DEFAULT '',
            material TEXT NOT NULL DEFAULT '',
            dimension_text TEXT NOT NULL DEFAULT '',
            surface TEXT NOT NULL DEFAULT '',
            spec TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            length REAL,
            width REAL,
            height REAL,
            thickness REAL,
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
            supplier_name TEXT NOT NULL,
            purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE RESTRICT,
            purchase_order_item_id INTEGER NOT NULL REFERENCES purchase_order_items(id) ON DELETE RESTRICT,
            receipt_id INTEGER NOT NULL REFERENCES purchase_receipts(id) ON DELETE RESTRICT,
            location_id INTEGER NOT NULL REFERENCES warehouse_locations(id) ON DELETE RESTRICT,
            opening_quantity INTEGER NOT NULL CHECK (typeof(opening_quantity) = 'integer' AND opening_quantity > 0),
            available_quantity INTEGER NOT NULL CHECK (typeof(available_quantity) = 'integer' AND available_quantity >= 0),
            unit_price_minor INTEGER CHECK (unit_price_minor IS NULL OR (typeof(unit_price_minor) = 'integer' AND unit_price_minor >= 0)),
            currency TEXT NOT NULL,
            invoice_status TEXT NOT NULL CHECK (invoice_status IN ('not_required','pending','invoiced')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (typeof(version) = 'integer' AND version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (source_kind = 'receipt' AND source_lot_id IS NULL)
                OR (source_kind = 'transfer' AND source_lot_id IS NOT NULL)
            ),
            CHECK (source_lot_id IS NULL OR source_lot_id <> id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_inventory_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_no TEXT NOT NULL UNIQUE,
            operation_type TEXT NOT NULL CHECK (operation_type IN ('transfer','adjustment','invoice_status')),
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            operator TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_inventory_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_no TEXT NOT NULL UNIQUE,
            lot_id INTEGER NOT NULL REFERENCES purchase_inventory_lots(id) ON DELETE RESTRICT,
            transaction_type TEXT NOT NULL CHECK (transaction_type IN ('receipt','transfer_out','transfer_in','adjustment','outbound','reversal')),
            quantity_delta INTEGER NOT NULL CHECK (typeof(quantity_delta) = 'integer' AND quantity_delta <> 0),
            related_type TEXT NOT NULL DEFAULT '',
            related_id INTEGER,
            from_location_id INTEGER REFERENCES warehouse_locations(id) ON DELETE RESTRICT,
            to_location_id INTEGER REFERENCES warehouse_locations(id) ON DELETE RESTRICT,
            paired_transaction_id INTEGER REFERENCES purchase_inventory_transactions(id) ON DELETE RESTRICT,
            operator TEXT NOT NULL,
            remark TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_inventory_invoice_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER NOT NULL REFERENCES purchase_inventory_lots(id) ON DELETE RESTRICT,
            old_status TEXT NOT NULL CHECK (old_status IN ('not_required','pending','invoiced')),
            new_status TEXT NOT NULL CHECK (new_status IN ('not_required','pending','invoiced')),
            operator TEXT NOT NULL,
            remark TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_inventory_outbounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outbound_no TEXT NOT NULL UNIQUE,
            outbound_at TEXT NOT NULL,
            used_by TEXT NOT NULL,
            operator TEXT NOT NULL,
            remark TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('posted','voided')),
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            voided_at TEXT,
            voided_by TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS purchase_inventory_outbound_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outbound_id INTEGER NOT NULL REFERENCES purchase_inventory_outbounds(id) ON DELETE RESTRICT,
            lot_id INTEGER NOT NULL REFERENCES purchase_inventory_lots(id) ON DELETE RESTRICT,
            category TEXT NOT NULL CHECK (category IN ({_CATEGORY_SQL})),
            item_name TEXT NOT NULL,
            drawing_no TEXT NOT NULL DEFAULT '',
            material TEXT NOT NULL DEFAULT '',
            dimension_text TEXT NOT NULL DEFAULT '',
            surface TEXT NOT NULL DEFAULT '',
            spec TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            supplier_name TEXT NOT NULL,
            location_id INTEGER NOT NULL REFERENCES warehouse_locations(id) ON DELETE RESTRICT,
            location_code TEXT NOT NULL,
            location_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity > 0),
            remark TEXT NOT NULL DEFAULT ''
        )
        """
    )

    indexes = (
        ("idx_purchase_receipts_order", "purchase_receipts", "purchase_order_id"),
        ("idx_purchase_receipts_status", "purchase_receipts", "status"),
        ("idx_purchase_receipts_received_at", "purchase_receipts", "received_at"),
        ("idx_purchase_receipt_items_receipt", "purchase_receipt_items", "receipt_id"),
        (
            "idx_purchase_receipt_items_order_item",
            "purchase_receipt_items",
            "purchase_order_item_id",
        ),
        ("idx_purchase_receipt_items_location", "purchase_receipt_items", "location_id"),
        ("idx_purchase_receipt_items_item_name", "purchase_receipt_items", "item_name"),
        ("idx_purchase_receipt_items_drawing_no", "purchase_receipt_items", "drawing_no"),
        ("idx_purchase_inventory_lots_category", "purchase_inventory_lots", "category"),
        ("idx_purchase_inventory_lots_supplier", "purchase_inventory_lots", "supplier_id"),
        ("idx_purchase_inventory_lots_order", "purchase_inventory_lots", "purchase_order_id"),
        ("idx_purchase_inventory_lots_receipt", "purchase_inventory_lots", "receipt_id"),
        ("idx_purchase_inventory_lots_location", "purchase_inventory_lots", "location_id"),
        ("idx_purchase_inventory_lots_item_name", "purchase_inventory_lots", "item_name"),
        ("idx_purchase_inventory_lots_drawing_no", "purchase_inventory_lots", "drawing_no"),
        (
            "idx_purchase_inventory_lots_invoice_status",
            "purchase_inventory_lots",
            "invoice_status",
        ),
        ("idx_purchase_inventory_lots_created_at", "purchase_inventory_lots", "created_at"),
        (
            "idx_purchase_inventory_transactions_lot_created_at",
            "purchase_inventory_transactions",
            "lot_id, created_at",
        ),
        (
            "idx_purchase_inventory_transactions_type_created_at",
            "purchase_inventory_transactions",
            "transaction_type, created_at",
        ),
        (
            "idx_purchase_inventory_transactions_related",
            "purchase_inventory_transactions",
            "related_type, related_id",
        ),
        (
            "idx_purchase_inventory_invoice_events_lot_created_at",
            "purchase_inventory_invoice_events",
            "lot_id, created_at",
        ),
        (
            "idx_purchase_inventory_outbounds_status",
            "purchase_inventory_outbounds",
            "status",
        ),
        (
            "idx_purchase_inventory_outbounds_outbound_at",
            "purchase_inventory_outbounds",
            "outbound_at",
        ),
        (
            "idx_purchase_inventory_outbound_items_outbound",
            "purchase_inventory_outbound_items",
            "outbound_id",
        ),
        (
            "idx_purchase_inventory_outbound_items_lot",
            "purchase_inventory_outbound_items",
            "lot_id",
        ),
        (
            "idx_purchase_inventory_outbound_items_category",
            "purchase_inventory_outbound_items",
            "category",
        ),
        (
            "idx_purchase_inventory_outbound_items_location",
            "purchase_inventory_outbound_items",
            "location_id",
        ),
        (
            "idx_purchase_inventory_outbound_items_item_name",
            "purchase_inventory_outbound_items",
            "item_name",
        ),
        (
            "idx_purchase_inventory_outbound_items_drawing_no",
            "purchase_inventory_outbound_items",
            "drawing_no",
        ),
    )
    for name, table, columns in indexes:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")

    references = (
        ("purchase_receipts", "purchase_order_id", "purchase_orders"),
        ("purchase_receipt_items", "receipt_id", "purchase_receipts"),
        (
            "purchase_receipt_items",
            "purchase_order_item_id",
            "purchase_order_items",
        ),
        ("purchase_receipt_items", "location_id", "warehouse_locations"),
        (
            "purchase_inventory_lots",
            "origin_receipt_item_id",
            "purchase_receipt_items",
        ),
        ("purchase_inventory_lots", "source_lot_id", "purchase_inventory_lots"),
        ("purchase_inventory_lots", "supplier_id", "suppliers"),
        ("purchase_inventory_lots", "purchase_order_id", "purchase_orders"),
        (
            "purchase_inventory_lots",
            "purchase_order_item_id",
            "purchase_order_items",
        ),
        ("purchase_inventory_lots", "receipt_id", "purchase_receipts"),
        ("purchase_inventory_lots", "location_id", "warehouse_locations"),
        (
            "purchase_inventory_transactions",
            "lot_id",
            "purchase_inventory_lots",
        ),
        (
            "purchase_inventory_transactions",
            "from_location_id",
            "warehouse_locations",
        ),
        (
            "purchase_inventory_transactions",
            "to_location_id",
            "warehouse_locations",
        ),
        (
            "purchase_inventory_transactions",
            "paired_transaction_id",
            "purchase_inventory_transactions",
        ),
        (
            "purchase_inventory_invoice_events",
            "lot_id",
            "purchase_inventory_lots",
        ),
        (
            "purchase_inventory_outbound_items",
            "outbound_id",
            "purchase_inventory_outbounds",
        ),
        (
            "purchase_inventory_outbound_items",
            "lot_id",
            "purchase_inventory_lots",
        ),
        (
            "purchase_inventory_outbound_items",
            "location_id",
            "warehouse_locations",
        ),
    )
    for child, column, parent in references:
        _ensure_restrictive_reference(
            conn, child=child, column=column, parent=parent
        )


def purchase_inventory_invariant_errors(conn) -> list[str]:
    """Return invariant violations in existing purchase-inventory records."""
    errors = []

    for row in conn.execute(
        """
        SELECT id, lot_no, opening_quantity, available_quantity
        FROM purchase_inventory_lots
        WHERE opening_quantity < 0 OR available_quantity < 0
        ORDER BY id
        """
    ):
        errors.append(
            f"negative lot: {row['lot_no']} (id={row['id']}, "
            f"opening={row['opening_quantity']}, available={row['available_quantity']})"
        )

    for row in conn.execute(
        """
        SELECT lots.id, lots.lot_no, lots.opening_quantity, items.qualified_quantity
        FROM purchase_inventory_lots AS lots
        JOIN purchase_receipt_items AS items
          ON items.id = lots.origin_receipt_item_id
        WHERE lots.source_kind = 'receipt'
          AND lots.opening_quantity <> items.qualified_quantity
        ORDER BY lots.id
        """
    ):
        errors.append(
            f"receipt opening quantity mismatch: {row['lot_no']} (id={row['id']}, "
            f"opening={row['opening_quantity']}, qualified={row['qualified_quantity']})"
        )

    for row in conn.execute(
        """
        SELECT first.id AS first_id, first.transaction_no AS first_no,
               first.quantity_delta AS first_delta,
               paired.id AS paired_id, paired.transaction_no AS paired_no,
               paired.quantity_delta AS paired_delta
        FROM purchase_inventory_transactions AS first
        JOIN purchase_inventory_transactions AS paired
          ON paired.id = first.paired_transaction_id
        WHERE first.transaction_type IN ('transfer_out','transfer_in')
          AND paired.transaction_type IN ('transfer_out','transfer_in')
          AND first.id < paired.id
          AND abs(first.quantity_delta) <> abs(paired.quantity_delta)
        ORDER BY first.id
        """
    ):
        errors.append(
            "transfer pair quantity mismatch: "
            f"{row['first_no']} ({row['first_delta']}) / "
            f"{row['paired_no']} ({row['paired_delta']})"
        )

    for row in conn.execute(
        """
        SELECT items.id, items.outbound_id, items.lot_id, items.quantity
        FROM purchase_inventory_outbound_items AS items
        WHERE NOT EXISTS (
            SELECT 1
            FROM purchase_inventory_transactions AS transactions
            WHERE transactions.transaction_type = 'outbound'
              AND transactions.lot_id = items.lot_id
              AND transactions.quantity_delta = -items.quantity
              AND (
                    (transactions.related_type = 'outbound'
                     AND transactions.related_id = items.outbound_id)
                 OR (transactions.related_type = 'outbound_item'
                     AND transactions.related_id = items.id)
              )
        )
        ORDER BY items.id
        """
    ):
        errors.append(
            "outbound item missing transaction: "
            f"item={row['id']}, outbound={row['outbound_id']}, lot={row['lot_id']}"
        )

    orphan_queries = (
        (
            "receipt lot origin",
            """
            SELECT lots.id
            FROM purchase_inventory_lots AS lots
            LEFT JOIN purchase_receipt_items AS items
              ON items.id = lots.origin_receipt_item_id
            WHERE lots.source_kind = 'receipt'
              AND (lots.origin_receipt_item_id IS NULL OR items.id IS NULL)
            """,
        ),
        (
            "transfer lot source",
            """
            SELECT lots.id
            FROM purchase_inventory_lots AS lots
            LEFT JOIN purchase_inventory_lots AS source
              ON source.id = lots.source_lot_id
            WHERE lots.source_kind = 'transfer'
              AND (lots.source_lot_id IS NULL OR source.id IS NULL
                   OR lots.source_lot_id = lots.id)
            """,
        ),
        (
            "receipt purchase order",
            """
            SELECT receipts.id
            FROM purchase_receipts AS receipts
            LEFT JOIN purchase_orders AS orders
              ON orders.id = receipts.purchase_order_id
            WHERE orders.id IS NULL
            """,
        ),
        (
            "receipt item parent",
            """
            SELECT items.id
            FROM purchase_receipt_items AS items
            LEFT JOIN purchase_receipts AS receipts ON receipts.id = items.receipt_id
            LEFT JOIN purchase_order_items AS order_items
              ON order_items.id = items.purchase_order_item_id
            LEFT JOIN warehouse_locations AS locations
              ON locations.id = items.location_id
            WHERE receipts.id IS NULL OR order_items.id IS NULL OR locations.id IS NULL
            """,
        ),
        (
            "lot parent",
            """
            SELECT lots.id
            FROM purchase_inventory_lots AS lots
            LEFT JOIN suppliers ON suppliers.id = lots.supplier_id
            LEFT JOIN purchase_orders AS orders ON orders.id = lots.purchase_order_id
            LEFT JOIN purchase_order_items AS order_items
              ON order_items.id = lots.purchase_order_item_id
            LEFT JOIN purchase_receipts AS receipts ON receipts.id = lots.receipt_id
            LEFT JOIN warehouse_locations AS locations ON locations.id = lots.location_id
            WHERE suppliers.id IS NULL OR orders.id IS NULL OR order_items.id IS NULL
               OR receipts.id IS NULL OR locations.id IS NULL
            """,
        ),
        (
            "inventory transaction parent",
            """
            SELECT transactions.id
            FROM purchase_inventory_transactions AS transactions
            LEFT JOIN purchase_inventory_lots AS lots ON lots.id = transactions.lot_id
            LEFT JOIN warehouse_locations AS source_location
              ON source_location.id = transactions.from_location_id
            LEFT JOIN warehouse_locations AS target_location
              ON target_location.id = transactions.to_location_id
            LEFT JOIN purchase_inventory_transactions AS paired
              ON paired.id = transactions.paired_transaction_id
            WHERE lots.id IS NULL
               OR (transactions.from_location_id IS NOT NULL AND source_location.id IS NULL)
               OR (transactions.to_location_id IS NOT NULL AND target_location.id IS NULL)
               OR (transactions.paired_transaction_id IS NOT NULL AND paired.id IS NULL)
            """,
        ),
        (
            "invoice event lot",
            """
            SELECT events.id
            FROM purchase_inventory_invoice_events AS events
            LEFT JOIN purchase_inventory_lots AS lots ON lots.id = events.lot_id
            WHERE lots.id IS NULL
            """,
        ),
        (
            "outbound item parent",
            """
            SELECT items.id
            FROM purchase_inventory_outbound_items AS items
            LEFT JOIN purchase_inventory_outbounds AS outbounds
              ON outbounds.id = items.outbound_id
            LEFT JOIN purchase_inventory_lots AS lots ON lots.id = items.lot_id
            LEFT JOIN warehouse_locations AS locations ON locations.id = items.location_id
            WHERE outbounds.id IS NULL OR lots.id IS NULL OR locations.id IS NULL
            """,
        ),
    )
    for source, query in orphan_queries:
        for row in conn.execute(query):
            errors.append(f"orphaned source record: {source} id={row[0]}")

    return errors
