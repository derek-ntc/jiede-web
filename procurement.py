"""Schema and value validation shared by the unified procurement subsystem."""

import re
from datetime import date

from pricing import parse_money_minor


PURCHASE_CATEGORIES = {"raw_material", "carton", "outsourcing", "other"}
PURCHASE_STATUSES = {"draft", "ordered", "partially_received", "received", "cancelled"}
_PURCHASE_QUANTITY_MAX = 2_147_483_647


def parse_purchase_category(value: str) -> str:
    """Return a supported purchase category without silently normalizing input."""
    if not isinstance(value, str) or value not in PURCHASE_CATEGORIES:
        raise ValueError("采购类别无效")
    return value


def parse_purchase_quantity(value: object) -> int:
    """Parse a positive 32-bit integer quantity from a form value."""
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value or "")):
        raise ValueError("数量必须为正整数")
    quantity = int(value)
    if quantity > _PURCHASE_QUANTITY_MAX:
        raise ValueError("数量超出允许范围")
    return quantity


def parse_optional_money_minor(value: str, currency: str = "CNY") -> int | None:
    """Parse an optional nonnegative money value into its currency minor units."""
    text = str(value or "").strip()
    return None if not text else parse_money_minor(text, currency)


def next_purchase_order_no(conn, purchased_at: str) -> str:
    """Find the first currently unused order number for an ISO calendar date."""
    if not isinstance(purchased_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", purchased_at):
        raise ValueError("采购日期必须为 ISO 日期")
    try:
        parsed_date = date.fromisoformat(purchased_at)
    except ValueError as error:
        raise ValueError("采购日期必须为有效日期") from error

    day = parsed_date.strftime("%Y%m%d")
    prefix = f"PO-{day}-"
    existing = {
        int(row[0][len(prefix) :])
        for row in conn.execute(
            "SELECT order_no FROM purchase_orders WHERE order_no LIKE ?", (f"{prefix}%",)
        )
        if re.fullmatch(rf"{re.escape(prefix)}\d{{4}}", row[0])
    }
    suffix = 1
    while suffix in existing:
        suffix += 1
    if suffix > 9999:
        raise ValueError("当天采购单号已用尽")
    return f"{prefix}{suffix:04d}"


def ensure_procurement_tables(conn) -> None:
    """Create the isolated unified-procurement tables and their query indexes."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE CHECK (trim(code) <> ''),
            name TEXT NOT NULL UNIQUE CHECK (trim(name) <> ''),
            contact TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            remark TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS supplier_legacy_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
            legacy_source TEXT NOT NULL,
            legacy_id INTEGER NOT NULL,
            UNIQUE (legacy_source, legacy_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_delivery_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            delivery_address TEXT NOT NULL,
            recipient TEXT NOT NULL,
            phone TEXT NOT NULL,
            default_remark TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL CHECK (category IN ('raw_material', 'carton', 'outsourcing', 'other')),
            supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
            supplier_code TEXT NOT NULL,
            supplier_name TEXT NOT NULL,
            supplier_contact TEXT NOT NULL,
            supplier_phone TEXT NOT NULL,
            supplier_email TEXT NOT NULL,
            supplier_address TEXT NOT NULL,
            purchased_at TEXT NOT NULL,
            delivery_address TEXT NOT NULL,
            recipient TEXT NOT NULL,
            recipient_phone TEXT NOT NULL,
            remark TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ordered', 'partially_received', 'received', 'cancelled')),
            legacy_source TEXT,
            legacy_id INTEGER,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            drawing_no TEXT NOT NULL,
            material TEXT NOT NULL,
            dimension_text TEXT NOT NULL,
            surface TEXT NOT NULL,
            spec TEXT NOT NULL,
            unit TEXT NOT NULL,
            length REAL,
            width REAL,
            height REAL,
            thickness REAL,
            expected_at TEXT NOT NULL,
            remark TEXT NOT NULL,
            ordered_quantity INTEGER NOT NULL CHECK (ordered_quantity > 0),
            unit_price_minor INTEGER CHECK (unit_price_minor IS NULL OR unit_price_minor >= 0),
            line_total_minor INTEGER CHECK (line_total_minor IS NULL OR line_total_minor >= 0),
            legacy_source TEXT,
            legacy_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_suppliers_active_name ON suppliers (active, name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_orders_category ON purchase_orders (category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_orders_status ON purchase_orders (status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_orders_purchased_at ON purchase_orders (purchased_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier_id ON purchase_orders (supplier_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_order_items_order_sort ON purchase_order_items (purchase_order_id, sort_order)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_order_items_item_name ON purchase_order_items (item_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_order_items_drawing_no ON purchase_order_items (drawing_no)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_delivery_profiles_one_active_default "
        "ON purchase_delivery_profiles (is_default) WHERE is_default = 1 AND active = 1"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_orders_legacy_source_id "
        "ON purchase_orders (legacy_source, legacy_id) "
        "WHERE legacy_source IS NOT NULL AND legacy_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_order_items_legacy_source_id "
        "ON purchase_order_items (legacy_source, legacy_id) "
        "WHERE legacy_source IS NOT NULL AND legacy_id IS NOT NULL"
    )
