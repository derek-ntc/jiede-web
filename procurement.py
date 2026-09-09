"""Schema and value validation shared by the unified procurement subsystem."""

import re
import math
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation

from pricing import line_total_minor, parse_money_minor


PURCHASE_CATEGORIES = {"raw_material", "carton", "outsourcing", "other"}
PURCHASE_STATUSES = {"draft", "ordered", "partially_received", "received", "cancelled"}
_PURCHASE_QUANTITY_MAX = 2_147_483_647
_PURCHASE_ORDER_ITEM_COLUMNS = """
    id, purchase_order_id, sort_order, item_name, drawing_no, material,
    dimension_text, surface, spec, unit, length, width, height, thickness,
    expected_at, remark, ordered_quantity, unit_price_minor, line_total_minor,
    legacy_source, legacy_id, created_at, updated_at
"""


def normalize_supplier_payload(form) -> dict[str, object]:
    """Return trimmed supplier fields while preserving display phone and email text."""
    payload = {
        field: str(form.get(field, "") or "").strip()
        for field in ("code", "name", "contact", "phone", "email", "address", "remark")
    }
    if not payload["name"]:
        raise ValueError("供应商名称为必填项")
    return payload


def supplier_snapshot(row) -> dict[str, str]:
    """Capture supplier details for immutable purchase-order history."""
    return {
        "supplier_code": row["code"],
        "supplier_name": row["name"],
        "supplier_contact": row["contact"],
        "supplier_phone": row["phone"],
        "supplier_email": row["email"],
        "supplier_address": row["address"],
    }


def next_supplier_code(conn) -> str:
    """Find the first unused SUP-00001-style supplier code."""
    existing = {
        int(row[0][4:])
        for row in conn.execute("SELECT code FROM suppliers")
        if re.fullmatch(r"SUP-\d{5}", row[0])
    }
    suffix = 1
    while suffix in existing:
        suffix += 1
    if suffix > 99999:
        raise ValueError("供应商编码已用尽")
    return f"SUP-{suffix:05d}"


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


CATEGORY_VISIBLE_FIELDS = {
    "raw_material": ("item_name", "material", "length", "width", "thickness", "surface"),
    "carton": ("item_name", "material", "length", "width", "height", "unit_price"),
    "outsourcing": ("item_name", "drawing_no", "material", "dimension_text", "thickness", "surface", "unit_price"),
    "other": ("item_name", "spec", "material", "dimension_text", "thickness", "surface", "unit_price"),
}
PURCHASE_CATEGORY_LABELS = {"raw_material": "原材料采购", "carton": "纸箱采购", "outsourcing": "外协加工", "other": "其他采购"}
PURCHASE_STATUS_LABELS = {"draft": "草稿", "ordered": "已下单", "partially_received": "部分到货", "received": "已到齐", "cancelled": "已取消"}
PURCHASE_FIELD_LABELS = {"item_name": "物品名称", "drawing_no": "图号", "material": "材质", "length": "长", "width": "宽", "height": "高", "thickness": "厚度", "surface": "表面", "dimension_text": "尺寸", "spec": "规格", "unit_price": "单价", "quantity": "数量", "unit": "单位", "expected_at": "预计到货日期", "remark": "其他要求"}
_ITEM_TEXT_FIELDS = ("item_name", "drawing_no", "material", "dimension_text", "surface", "spec", "unit", "remark")
_DIMENSION_FIELDS = ("length", "width", "height", "thickness")
_DELIVERY_FIELDS = ("delivery_address", "recipient", "recipient_phone", "remark")


def _purchase_text(value):
    text = str(value if value is not None else "").strip()
    if len(text) > 4000:
        raise ValueError("采购字段内容过长")
    return text


def _purchase_date(value):
    text = _purchase_text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("请填写 YYYY-MM-DD 格式的日期")
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise ValueError("请填写有效日期") from error
    return text


def _purchase_id(value):
    text = str(value or "")
    if not re.fullmatch(r"[1-9]\d{0,18}", text) or int(text) > 2**63 - 1:
        raise ValueError("记录 ID 无效")
    return int(text)


def _purchase_dimension(value):
    text = _purchase_text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
        if not number.is_finite() or number <= 0:
            raise ValueError("尺寸必须为正有限数值或留空")
        result = float(number)
        if not math.isfinite(result) or result <= 0:
            raise ValueError("尺寸超出允许范围")
        return result
    except InvalidOperation as error:
        raise ValueError("尺寸格式无效") from error


def normalize_purchase_order_payload(payload, *, can_view_prices: bool) -> dict:
    """Validate raw dictionaries or indexed HTML form rows; ignore all posted totals."""
    category = parse_purchase_category(payload.get("category"))
    status = _purchase_text(payload.get("status", "draft"))
    if status not in {"draft", "ordered"}:
        raise ValueError("订单只能保存为草稿或已下单")
    result = dict(category=category, status=status, supplier_id=_purchase_id(payload.get("supplier_id")),
                  purchased_at=_purchase_date(payload.get("purchased_at")), can_view_prices=bool(can_view_prices))
    if payload.get("delivery_profile_id"):
        result["delivery_profile_id"] = _purchase_id(payload["delivery_profile_id"])
    for field in _DELIVERY_FIELDS:
        if field in payload:
            result[field] = _purchase_text(payload[field])
    raw_rows = payload.get("rows")
    if raw_rows is None:
        indexed = {}
        for key in payload:
            match = re.fullmatch(r"items\[(\d{1,4})\]\[([a-z_]+)\]", key)
            if match:
                indexed.setdefault(int(match[1]), {})[match[2]] = payload[key]
        raw_rows = [indexed[index] for index in sorted(indexed)]
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > 500:
        raise ValueError("请填写 1 至 500 条采购明细")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError("采购明细无效")
        row = {field: _purchase_text(raw.get(field)) for field in _ITEM_TEXT_FIELDS}
        row.update({field: _purchase_dimension(raw.get(field)) for field in _DIMENSION_FIELDS})
        row["id"] = _purchase_id(raw["id"]) if raw.get("id") else None
        row["ordered_quantity"] = parse_purchase_quantity(raw.get("quantity"))
        row["expected_at"] = _purchase_date(raw.get("expected_at"))
        if category in {"raw_material", "carton"} and not row["material"]:
            raise ValueError("材质为必填项")
        if category == "outsourcing" and not (row["item_name"] or row["drawing_no"]):
            raise ValueError("外协加工必须填写物品名称或图号")
        if category == "other" and not row["item_name"]:
            raise ValueError("物品名称为必填项")
        row["unit_price_minor"] = parse_optional_money_minor(raw.get("unit_price")) if can_view_prices else None
        row["line_total_minor"] = line_total_minor(row["unit_price_minor"], row["ordered_quantity"])
        rows.append(row)
    result["rows"] = rows
    return result


@contextmanager
def _purchase_transaction(conn):
    # Reserve the SQLite writer before reading order/receipt balances and numbering.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT purchase_order_write")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO purchase_order_write")
        conn.execute("RELEASE purchase_order_write")
        raise
    conn.execute("RELEASE purchase_order_write")


def _purchase_header(conn, payload, old=None):
    supplier = conn.execute("SELECT * FROM suppliers WHERE id=? AND active=1", (payload["supplier_id"],)).fetchone()
    if supplier is None:
        raise ValueError("请选择启用的供应商")
    # Unchanged supplier retains its historical snapshot on subsequent edits.
    snapshot = supplier_snapshot(supplier)
    if old is not None and old["supplier_id"] == payload["supplier_id"]:
        snapshot = {field: old[field] for field in snapshot}
    delivery = {field: old[field] if old is not None else "" for field in _DELIVERY_FIELDS}
    if payload.get("delivery_profile_id"):
        profile = conn.execute("SELECT * FROM purchase_delivery_profiles WHERE id=? AND active=1", (payload["delivery_profile_id"],)).fetchone()
        if profile is None:
            raise ValueError("请选择启用的公司收货模板")
        delivery = dict(delivery_address=profile["delivery_address"], recipient=profile["recipient"], recipient_phone=profile["phone"], remark=profile["default_remark"])
    elif old is None and not all(field in payload for field in _DELIVERY_FIELDS[:3]):
        raise ValueError("请选择收货模板或填写完整收货信息")
    delivery.update({field: payload[field] for field in _DELIVERY_FIELDS if field in payload})
    if not all(delivery[field] for field in _DELIVERY_FIELDS[:3]):
        raise ValueError("收货地址、收件人和联系电话为必填项")
    return dict(snapshot, **delivery, supplier_id=payload["supplier_id"], purchased_at=payload["purchased_at"], status=payload["status"])


def _write_purchase_item(conn, order_id, row, sort_order, now):
    values = {field: row[field] for field in (*_ITEM_TEXT_FIELDS, *_DIMENSION_FIELDS, "ordered_quantity", "unit_price_minor", "line_total_minor", "expected_at")}
    values.update(sort_order=sort_order, updated_at=now)
    if row.get("id"):
        conn.execute("UPDATE purchase_order_items SET " + ", ".join(f"{field}=?" for field in values) + " WHERE id=? AND purchase_order_id=?", (*values.values(), row["id"], order_id))
    else:
        values.update(purchase_order_id=order_id, created_at=now)
        conn.execute(f"INSERT INTO purchase_order_items ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))


def create_purchase_order(conn, payload, actor, now) -> int:
    with _purchase_transaction(conn):
        if any(row.get("id") for row in payload["rows"]):
            raise ValueError("新采购单不能引用已有明细")
        header = _purchase_header(conn, payload)
        header.update(category=payload["category"], currency="CNY", order_no=next_purchase_order_no(conn, payload["purchased_at"]), created_by=actor, updated_by=actor, created_at=now, updated_at=now)
        cursor = conn.execute(f"INSERT INTO purchase_orders ({', '.join(header)}) VALUES ({', '.join('?' for _ in header)})", tuple(header.values()))
        order_id = cursor.lastrowid
        for index, source in enumerate(payload["rows"]):
            row = dict(source)
            if not payload["can_view_prices"]:
                row["unit_price_minor"] = None
            row["line_total_minor"] = line_total_minor(row["unit_price_minor"], row["ordered_quantity"])
            _write_purchase_item(conn, order_id, row, index, now)
    return order_id


def load_purchase_order(conn, order_id):
    order = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (_purchase_id(order_id),)).fetchone()
    if order is None:
        raise LookupError("采购订单不存在")
    rows = conn.execute("SELECT * FROM purchase_order_items WHERE purchase_order_id=? ORDER BY sort_order,id", (order_id,)).fetchall()
    return order, rows


def _purchase_receipt_history(conn, order_id):
    # Phase 1 creates no receipt tables; use the downstream schema when installed.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchase_receipt_items'").fetchone() is None:
        return {}
    return {row["purchase_order_item_id"]: row["actual"] for row in conn.execute(
        "SELECT ri.purchase_order_item_id, SUM(ri.actual_quantity) AS actual FROM purchase_receipt_items ri "
        "JOIN purchase_receipts r ON r.id=ri.receipt_id "
        "JOIN purchase_order_items i ON i.id=ri.purchase_order_item_id "
        "WHERE i.purchase_order_id=? AND r.status <> 'voided' GROUP BY ri.purchase_order_item_id", (order_id,))}


def update_purchase_order(conn, order_id, payload, actor, now) -> None:
    with _purchase_transaction(conn):
        order, existing = load_purchase_order(conn, order_id)
        if order["status"] in {"received", "cancelled"}:
            raise ValueError("已到齐或已取消的采购订单不能修改")
        if payload["category"] != order["category"]:
            raise ValueError("不能更改采购类别")
        if order["status"] != "draft" and payload["status"] == "draft":
            raise ValueError("已下单订单不能退回草稿")
        old_rows = {row["id"]: row for row in existing}
        ids = [row["id"] for row in payload["rows"] if row.get("id")]
        if len(set(ids)) != len(ids) or any(item_id not in old_rows for item_id in ids):
            raise ValueError("采购明细 ID 重复或不属于此订单")
        history = _purchase_receipt_history(conn, order_id)
        if any(item_id not in ids for item_id in history):
            raise ValueError("有到货历史的明细不能删除")
        for row in payload["rows"]:
            if row["ordered_quantity"] < history.get(row.get("id"), 0):
                raise ValueError("订购数量不能低于累计实际到货数量")
        header = _purchase_header(conn, payload, order)
        if order["status"] == "partially_received":
            header["status"] = "partially_received"
        header.update(updated_by=actor, updated_at=now)
        conn.execute("UPDATE purchase_orders SET " + ", ".join(f"{field}=?" for field in header) + " WHERE id=?", (*header.values(), order_id))
        for item_id in old_rows.keys() - set(ids):
            conn.execute("DELETE FROM purchase_order_items WHERE id=? AND purchase_order_id=?", (item_id, order_id))
        for index, source in enumerate(payload["rows"]):
            row = dict(source)
            if not payload["can_view_prices"]:
                row["unit_price_minor"] = old_rows[row["id"]]["unit_price_minor"] if row.get("id") else None
            row["line_total_minor"] = line_total_minor(row["unit_price_minor"], row["ordered_quantity"])
            _write_purchase_item(conn, order_id, row, index, now)


def cancel_purchase_order(conn, order_id, actor, now):
    with _purchase_transaction(conn):
        order, _ = load_purchase_order(conn, order_id)
        if order["status"] in {"received", "cancelled"}:
            raise ValueError("已到齐或已取消的订单不能取消")
        conn.execute("UPDATE purchase_orders SET status='cancelled', updated_by=?, updated_at=? WHERE id=?", (actor, now, order_id))


def fetch_purchase_orders(conn, category: str, filters: dict):
    conditions, params = ["o.category=?"], [parse_purchase_category(category)]
    for field in ("supplier_id", "status"):
        if filters.get(field):
            conditions.append(f"o.{field}=?")
            params.append(filters[field])
    if filters.get("order_no"):
        conditions.append("o.order_no LIKE ?")
        params.append(f"%{filters['order_no']}%")
    for key, operator in (("purchased_from", ">="), ("purchased_to", "<=")):
        if filters.get(key):
            conditions.append(f"o.purchased_at {operator} ?")
            params.append(filters[key])
    item_conditions = ["i.purchase_order_id=o.id"]
    if filters.get("q"):
        item_conditions.append("(i.item_name LIKE ? OR i.drawing_no LIKE ? OR i.spec LIKE ? OR i.material LIKE ?)")
        params.extend([f"%{filters['q']}%"] * 4)
    for key, operator in (("expected_from", ">="), ("expected_to", "<=")):
        if filters.get(key):
            item_conditions.append(f"i.expected_at {operator} ?")
            params.append(filters[key])
    conditions.append("EXISTS (SELECT 1 FROM purchase_order_items i WHERE " + " AND ".join(item_conditions) + ")")
    return conn.execute("SELECT o.* FROM purchase_orders o WHERE " + " AND ".join(conditions) + " ORDER BY o.purchased_at DESC,o.id DESC", params).fetchall()


def _create_purchase_order_items_table(conn, table_name, if_not_exists=False):
    if_not_exists_sql = " IF NOT EXISTS" if if_not_exists else ""
    conn.execute(
        f"""
        CREATE TABLE{if_not_exists_sql} {table_name} (
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
            ordered_quantity INTEGER NOT NULL CHECK (typeof(ordered_quantity) = 'integer' AND ordered_quantity > 0),
            unit_price_minor INTEGER CHECK (unit_price_minor IS NULL OR (typeof(unit_price_minor) = 'integer' AND unit_price_minor >= 0)),
            line_total_minor INTEGER CHECK (line_total_minor IS NULL OR (typeof(line_total_minor) = 'integer' AND line_total_minor >= 0)),
            legacy_source TEXT,
            legacy_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _purchase_order_items_requires_integer_upgrade(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'purchase_order_items'"
    ).fetchone()
    if row is None:
        return False
    schema = re.sub(r"\s+", "", row[0].lower())
    return not all(
        check in schema
        for check in (
            "typeof(ordered_quantity)='integer'",
            "typeof(unit_price_minor)='integer'",
            "typeof(line_total_minor)='integer'",
        )
    )


def _upgrade_purchase_order_items_integer_storage(conn):
    """Atomically rebuild pre-strict item tables while preserving valid rows."""
    conn.execute("SAVEPOINT purchase_order_items_integer_upgrade")
    try:
        old_sequence_row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'purchase_order_items'"
        ).fetchone()
        old_sequence = old_sequence_row[0] if old_sequence_row is not None else 0
        old_max_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM purchase_order_items"
        ).fetchone()[0]
        _create_purchase_order_items_table(conn, "purchase_order_items_rebuild")
        conn.execute(
            f"""
            INSERT INTO purchase_order_items_rebuild ({_PURCHASE_ORDER_ITEM_COLUMNS})
            SELECT {_PURCHASE_ORDER_ITEM_COLUMNS} FROM purchase_order_items
            """
        )
        conn.execute("DROP TRIGGER IF EXISTS trg_purchase_orders_restrict_item_id_update")
        conn.execute("DROP TRIGGER IF EXISTS trg_purchase_orders_cascade_items_delete")
        conn.execute("DROP TABLE purchase_order_items")
        conn.execute(
            "ALTER TABLE purchase_order_items_rebuild RENAME TO purchase_order_items"
        )
        restored_sequence = max(old_sequence, old_max_id)
        updated_sequence = conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'purchase_order_items'",
            (restored_sequence,),
        )
        if updated_sequence.rowcount == 0:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('purchase_order_items', ?)",
                (restored_sequence,),
            )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT purchase_order_items_integer_upgrade")
        conn.execute("RELEASE SAVEPOINT purchase_order_items_integer_upgrade")
        raise
    conn.execute("RELEASE SAVEPOINT purchase_order_items_integer_upgrade")


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
    _create_purchase_order_items_table(conn, "purchase_order_items", if_not_exists=True)
    if _purchase_order_items_requires_integer_upgrade(conn):
        _upgrade_purchase_order_items_integer_storage(conn)

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
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_supplier_legacy_links_require_supplier_insert
        BEFORE INSERT ON supplier_legacy_links
        FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM suppliers WHERE id = NEW.supplier_id)
        BEGIN
            SELECT RAISE(ABORT, 'supplier_legacy_links.supplier_id references no supplier');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_supplier_legacy_links_require_supplier_update
        BEFORE UPDATE OF supplier_id ON supplier_legacy_links
        FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM suppliers WHERE id = NEW.supplier_id)
        BEGIN
            SELECT RAISE(ABORT, 'supplier_legacy_links.supplier_id references no supplier');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_suppliers_restrict_purchase_order_delete
        BEFORE DELETE ON suppliers
        FOR EACH ROW WHEN EXISTS (SELECT 1 FROM purchase_orders WHERE supplier_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'supplier has purchase orders');
        END
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS trg_suppliers_restrict_child_id_update")
    conn.execute(
        """
        CREATE TRIGGER trg_suppliers_restrict_child_id_update
        BEFORE UPDATE ON suppliers
        FOR EACH ROW WHEN NEW.id <> OLD.id AND (
            EXISTS (SELECT 1 FROM supplier_legacy_links WHERE supplier_id = OLD.id)
            OR EXISTS (SELECT 1 FROM purchase_orders WHERE supplier_id = OLD.id)
        )
        BEGIN
            SELECT RAISE(ABORT, 'supplier id has dependent procurement records');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_suppliers_cascade_legacy_links_delete
        AFTER DELETE ON suppliers
        FOR EACH ROW
        BEGIN
            DELETE FROM supplier_legacy_links WHERE supplier_id = OLD.id;
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_orders_require_supplier_insert
        BEFORE INSERT ON purchase_orders
        FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM suppliers WHERE id = NEW.supplier_id)
        BEGIN
            SELECT RAISE(ABORT, 'purchase_orders.supplier_id references no supplier');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_orders_require_supplier_update
        BEFORE UPDATE OF supplier_id ON purchase_orders
        FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM suppliers WHERE id = NEW.supplier_id)
        BEGIN
            SELECT RAISE(ABORT, 'purchase_orders.supplier_id references no supplier');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_order_items_require_order_insert
        BEFORE INSERT ON purchase_order_items
        FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM purchase_orders WHERE id = NEW.purchase_order_id)
        BEGIN
            SELECT RAISE(ABORT, 'purchase_order_items.purchase_order_id references no purchase order');
        END
        """
    )
    conn.execute("DROP TRIGGER IF EXISTS trg_purchase_orders_restrict_item_id_update")
    conn.execute(
        """
        CREATE TRIGGER trg_purchase_orders_restrict_item_id_update
        BEFORE UPDATE ON purchase_orders
        FOR EACH ROW WHEN NEW.id <> OLD.id AND EXISTS (
            SELECT 1 FROM purchase_order_items WHERE purchase_order_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'purchase order id has dependent items');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_order_items_require_order_update
        BEFORE UPDATE OF purchase_order_id ON purchase_order_items
        FOR EACH ROW WHEN NOT EXISTS (SELECT 1 FROM purchase_orders WHERE id = NEW.purchase_order_id)
        BEGIN
            SELECT RAISE(ABORT, 'purchase_order_items.purchase_order_id references no purchase order');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchase_orders_cascade_items_delete
        AFTER DELETE ON purchase_orders
        FOR EACH ROW
        BEGIN
            DELETE FROM purchase_order_items WHERE purchase_order_id = OLD.id;
        END
        """
    )
