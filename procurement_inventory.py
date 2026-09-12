"""Schema and strict value parsing for independent purchase inventory."""

import hashlib
import json
import re
from contextlib import contextmanager
from uuid import uuid4

from procurement import (
    PURCHASE_CATEGORIES,
    _purchase_date,
    _purchase_id,
    fetch_purchase_orders,
    load_purchase_order,
    normalize_purchase_text,
    parse_optional_positive_decimal,
    recalculate_purchase_order_receipt_status,
)


_PURCHASE_INVENTORY_QUANTITY_MAX = 2_147_483_647
_CATEGORY_SQL = ",".join(f"'{category}'" for category in sorted(PURCHASE_CATEGORIES))

ACTUAL_FIELDS = ("item_name", "drawing_no", "material", "dimension_text",
                 "surface", "spec", "unit", "length", "width", "height", "thickness")
INVOICE_STATUSES = {"not_required", "pending", "invoiced"}


class PurchaseInventoryConflict(ValueError):
    """A validation, stale-state, retry, or explicit-confirmation conflict."""

    def __init__(self, message, *, needs_confirmation=False, current=None):
        super().__init__(message)
        self.needs_confirmation = bool(needs_confirmation)
        self.current = current


@contextmanager
def _inventory_transaction(conn):
    """Reserve the writer before reading; leave successful commit to the caller."""
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT purchase_inventory_write")
    try:
        if not owns_transaction:
            # Also acquire a write reservation in a caller's deferred transaction.
            conn.execute("UPDATE purchase_orders SET id=id WHERE 0")
        yield
    except Exception:
        conn.execute("ROLLBACK TO purchase_inventory_write")
        conn.execute("RELEASE purchase_inventory_write")
        if owns_transaction:
            conn.rollback()
        raise
    else:
        conn.execute("RELEASE purchase_inventory_write")


def _payload_digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _inventory_number(prefix, day):
    return f"{prefix}-{day.replace('-', '')}-{uuid4().hex}"


def _insert_inventory_record(conn, table, values):
    return conn.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                        tuple(values.values())).lastrowid


def validate_actual_fields_for_category(row, category):
    parse_purchase_category_slug(category)
    if category in {"raw_material", "carton"}:
        dimensions = ("length", "width", "thickness" if category == "raw_material" else "height")
        if not row["material"] or any(row[field] is None for field in dimensions):
            raise ValueError("实际材质、长、宽及厚度（原材料）或高度（纸箱）为必填项")
    elif not (row["item_name"] or row["drawing_no"]):
        raise ValueError("实际物品名称或图号为必填项")


def normalize_receipt_row(source, category):
    row = {name: normalize_purchase_text(source.get(name)) for name in ACTUAL_FIELDS[:7]}
    row.update({name: parse_optional_positive_decimal(source.get(name)) for name in ACTUAL_FIELDS[7:]})
    row["actual_quantity"] = parse_purchase_inventory_quantity(source.get("actual_quantity"))
    row["qualified_quantity"] = parse_purchase_inventory_quantity(source.get("qualified_quantity"), allow_zero=True)
    if row["qualified_quantity"] > row["actual_quantity"]:
        raise ValueError("合格入库数量不能大于实际到货数量")
    validate_actual_fields_for_category(row, category)
    row["purchase_order_item_id"] = _purchase_id(source.get("purchase_order_item_id"))
    row["location_id"] = _purchase_id(source.get("location_id"))
    row["invoice_status"] = normalize_purchase_text(source.get("invoice_status"))
    if row["invoice_status"] not in INVOICE_STATUSES:
        raise ValueError("发票状态无效")
    row["remark"] = normalize_purchase_text(source.get("remark"))
    return row


def load_receivable_orders(conn, category: str, filters: dict) -> list[dict]:
    """List matching ordered/partially-received orders, never drafts or closed orders."""
    parse_purchase_category_slug(category)
    return [dict(row) for row in fetch_purchase_orders(conn, category, filters)
            if row["status"] in {"ordered", "partially_received"}]


def load_receipt_preview(conn, order_id: int) -> dict:
    """Read one consistent order/actual/location snapshot for optimistic validation."""
    conn.execute("SAVEPOINT purchase_receipt_preview")
    try:
        order, items = load_purchase_order(conn, order_id)
        totals = {row["purchase_order_item_id"]: row for row in conn.execute(
            "SELECT i.purchase_order_item_id, SUM(i.actual_quantity) AS actual_quantity, "
            "SUM(i.qualified_quantity) AS qualified_quantity FROM purchase_receipt_items i "
            "JOIN purchase_receipts r ON r.id=i.receipt_id "
            "WHERE r.purchase_order_id=? AND r.status='posted' GROUP BY i.purchase_order_item_id", (order_id,))}
        rows = []
        for item in items:
            row = dict(item)
            total = totals.get(item["id"])
            row.update(actual_quantity=total["actual_quantity"] if total else 0,
                       qualified_quantity=total["qualified_quantity"] if total else 0)
            row["remaining_quantity"] = max(0, row["ordered_quantity"] - row["actual_quantity"])
            rows.append(row)
        locations = [dict(row) for row in conn.execute(
            "SELECT id,code,name FROM warehouse_locations WHERE enabled=1 ORDER BY code,id")]
        return dict(order=dict(order), rows=rows, locations=locations)
    finally:
        conn.execute("RELEASE purchase_receipt_preview")


def receipt_preview_token(preview: dict) -> str:
    """Hash server state deterministically; clients treat the result as opaque."""
    return _payload_digest(preview)


def _normalize_receipt_payload(conn, payload):
    if not isinstance(payload, dict):
        raise ValueError("到货内容无效")
    sources = payload.get("rows")
    if not isinstance(sources, list) or not sources or len(sources) > 500 or any(not isinstance(row, dict) for row in sources):
        raise ValueError("请填写 1 至 500 条到货明细")
    ids = [_purchase_id(row.get("purchase_order_item_id")) for row in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("到货明细不能重复")
    first = conn.execute("SELECT purchase_order_id FROM purchase_order_items WHERE id=?", (ids[0],)).fetchone()
    if first is None:
        raise ValueError("采购明细不存在")
    order, items = load_purchase_order(conn, first[0])
    if not set(ids) <= {row["id"] for row in items}:
        raise ValueError("到货明细不能跨采购订单或类别")
    if "purchase_order_id" in payload and _purchase_id(payload["purchase_order_id"]) != order["id"]:
        raise ValueError("到货明细不属于此采购订单")
    if "category" in payload and parse_purchase_category_slug(payload["category"]) != order["category"]:
        raise ValueError("到货明细不属于此采购类别")
    for source in sources:
        if "category" in source and parse_purchase_category_slug(source["category"]) != order["category"]:
            raise ValueError("到货明细不属于此采购类别")
    key = normalize_purchase_text(payload.get("idempotency_key"))
    if not key:
        raise ValueError("缺少防重复提交标识")
    normalized = dict(purchase_order_id=order["id"], received_at=_purchase_date(payload.get("received_at")),
                      remark=normalize_purchase_text(payload.get("remark")), idempotency_key=key,
                      rows=[normalize_receipt_row(row, order["category"]) for row in sources])
    return normalized, order, {row["id"]: row for row in items}


def _load_receipt(conn, receipt_id):
    row = conn.execute("SELECT * FROM purchase_receipts WHERE id=?", (_purchase_id(receipt_id),)).fetchone()
    if row is None:
        raise PurchaseInventoryConflict("到货单不存在")
    return dict(row)


def post_purchase_receipt(conn, payload: dict, actor: str, now: str) -> dict:
    """Atomically snapshot actuals and open independent stock; caller commits."""
    with _inventory_transaction(conn):
        try:
            normalized, order, items = _normalize_receipt_payload(conn, payload)
        except ValueError as error:
            raise PurchaseInventoryConflict(str(error)) from error
        digest = _payload_digest(normalized)
        existing = conn.execute("SELECT * FROM purchase_receipts WHERE idempotency_key=?",
                                (normalized["idempotency_key"],)).fetchone()
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise PurchaseInventoryConflict("重复提交标识已用于不同的到货内容")
            return dict(existing)
        current = load_receipt_preview(conn, order["id"])
        if order["status"] not in {"ordered", "partially_received", "received"}:
            raise PurchaseInventoryConflict("草稿或已取消的采购订单不能到货", current=current)
        if payload.get("preview_token") != receipt_preview_token(current):
            raise PurchaseInventoryConflict("采购订单、累计到货或仓位已变更，请重新预览", current=current)
        locations = {row["id"] for row in current["locations"]}
        if any(row["location_id"] not in locations for row in normalized["rows"]):
            raise PurchaseInventoryConflict("请选择启用的仓位", current=current)
        remaining = {row["id"]: row["remaining_quantity"] for row in current["rows"]}
        if (any(row["actual_quantity"] > remaining[row["purchase_order_item_id"]] for row in normalized["rows"])
                and payload.get("confirm_over_receipt") is not True):
            raise PurchaseInventoryConflict("实际到货超过剩余订购数量，请确认超收", needs_confirmation=True, current=current)
        header = {field: normalized[field] for field in ("purchase_order_id", "received_at", "remark", "idempotency_key")}
        header.update(receipt_no=_inventory_number("PR", normalized["received_at"]), status="posted", payload_hash=digest,
                      created_by=actor, posted_by=actor, created_at=now, posted_at=now)
        receipt_id = _insert_inventory_record(conn, "purchase_receipts", header)
        for row in normalized["rows"]:
            receipt_item_id = _insert_inventory_record(conn, "purchase_receipt_items", dict(row, receipt_id=receipt_id))
            if row["qualified_quantity"] == 0:
                continue
            lot = {field: row[field] for field in ACTUAL_FIELDS}
            lot.update(lot_no=_inventory_number("PIL", normalized["received_at"]), category=order["category"],
                       origin_receipt_item_id=receipt_item_id, source_lot_id=None, source_kind="receipt",
                       supplier_id=order["supplier_id"], supplier_name=order["supplier_name"],
                       purchase_order_id=order["id"], purchase_order_item_id=row["purchase_order_item_id"],
                       receipt_id=receipt_id, location_id=row["location_id"], opening_quantity=row["qualified_quantity"],
                       available_quantity=row["qualified_quantity"], unit_price_minor=items[row["purchase_order_item_id"]]["unit_price_minor"],
                       currency=order["currency"], invoice_status=row["invoice_status"], created_at=now, updated_at=now)
            lot_id = _insert_inventory_record(conn, "purchase_inventory_lots", lot)
            _insert_inventory_record(conn, "purchase_inventory_transactions", dict(
                transaction_no=_inventory_number("PIT", normalized["received_at"]), lot_id=lot_id,
                transaction_type="receipt", quantity_delta=row["qualified_quantity"], related_type="receipt",
                related_id=receipt_id, to_location_id=row["location_id"], operator=actor, remark=row["remark"], created_at=now))
        recalculate_purchase_order_receipt_status(conn, order["id"], actor, now)
        return _load_receipt(conn, receipt_id)


def receipt_voidability(conn, receipt_id: int) -> tuple[bool, str]:
    """Conservatively reject reversals after any downstream quantity activity."""
    receipt = _load_receipt(conn, receipt_id)
    if receipt["status"] != "posted":
        return False, "到货单已作废"
    lots = conn.execute("""
        WITH RECURSIVE affected(id) AS (
            SELECT id FROM purchase_inventory_lots WHERE receipt_id=?
            UNION
            SELECT child.id FROM purchase_inventory_lots child JOIN affected ON child.source_lot_id=affected.id
        ) SELECT lots.* FROM purchase_inventory_lots lots JOIN affected ON lots.id=affected.id
        """, (receipt_id,)).fetchall()
    for lot in lots:
        if lot["source_kind"] != "receipt" or lot["available_quantity"] != lot["opening_quantity"]:
            return False, "到货库存已移仓或数量已变化，不能作废"
        if conn.execute("SELECT 1 FROM purchase_inventory_transactions WHERE lot_id=? AND transaction_type<>'receipt' LIMIT 1",
                        (lot["id"],)).fetchone():
            return False, "到货库存有移仓、数量调整或出库历史，不能作废"
        if conn.execute("SELECT 1 FROM purchase_inventory_outbound_items WHERE lot_id=? LIMIT 1", (lot["id"],)).fetchone():
            return False, "到货库存有出库历史，不能作废"
    return True, ""


def void_purchase_receipt(conn, receipt_id: int, actor: str, now: str) -> dict:
    """Reverse untouched receipt lots once, retaining all historical snapshots."""
    with _inventory_transaction(conn):
        receipt = _load_receipt(conn, receipt_id)
        if receipt["status"] == "voided":
            return receipt
        allowed, reason = receipt_voidability(conn, receipt_id)
        if not allowed:
            raise PurchaseInventoryConflict(reason)
        lots = conn.execute("SELECT * FROM purchase_inventory_lots WHERE receipt_id=?", (receipt_id,)).fetchall()
        for lot in lots:
            _insert_inventory_record(conn, "purchase_inventory_transactions", dict(
                transaction_no=_inventory_number("PIT", receipt["received_at"]), lot_id=lot["id"],
                transaction_type="reversal", quantity_delta=-lot["opening_quantity"], related_type="receipt",
                related_id=receipt_id, from_location_id=lot["location_id"], operator=actor, remark="到货单作废", created_at=now))
            conn.execute("UPDATE purchase_inventory_lots SET available_quantity=0, version=version+1, updated_at=? WHERE id=?", (now, lot["id"]))
        conn.execute("UPDATE purchase_receipts SET status='voided', voided_by=?, voided_at=? WHERE id=?", (actor, now, receipt_id))
        recalculate_purchase_order_receipt_status(conn, receipt["purchase_order_id"], actor, now)
        return _load_receipt(conn, receipt_id)


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
    text = "" if value is None else str(value)
    if isinstance(value, bool) or not re.fullmatch(pattern, text):
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

    reported_transfer_pairs = set()
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
          AND abs(first.quantity_delta) <> abs(paired.quantity_delta)
        ORDER BY first.id
        """
    ):
        pair = tuple(sorted((row["first_id"], row["paired_id"])))
        if pair in reported_transfer_pairs:
            continue
        reported_transfer_pairs.add(pair)
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
            "lot origin",
            """
            SELECT lots.id
            FROM purchase_inventory_lots AS lots
            LEFT JOIN purchase_receipt_items AS items
              ON items.id = lots.origin_receipt_item_id
            WHERE lots.origin_receipt_item_id IS NULL OR items.id IS NULL
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
