"""Schema and field helpers shared by the shipping workflow."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid


def _table_columns(conn, table_name):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def ensure_shipping_workflow_tables(conn):
    """Add shipping/customer fields without rewriting existing business data."""
    migrations = {
        "customers": {
            "recipient_name": (
                "ALTER TABLE customers ADD COLUMN recipient_name "
                "TEXT NOT NULL DEFAULT ''"
            ),
            "recipient_phone": (
                "ALTER TABLE customers ADD COLUMN recipient_phone "
                "TEXT NOT NULL DEFAULT ''"
            ),
        },
        "product_order_shipments": {
            "specification_snapshot": (
                "ALTER TABLE product_order_shipments "
                "ADD COLUMN specification_snapshot TEXT"
            ),
        },
        "assembly_shipment_items": {
            "source_kind": (
                "ALTER TABLE assembly_shipment_items "
                "ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'bom'"
            ),
            "specification_snapshot": (
                "ALTER TABLE assembly_shipment_items "
                "ADD COLUMN specification_snapshot TEXT"
            ),
        },
        "production_followups": {
            "customer": (
                "ALTER TABLE production_followups ADD COLUMN customer "
                "TEXT NOT NULL DEFAULT ''"
            ),
            "manual_id": (
                "ALTER TABLE production_followups ADD COLUMN manual_id INTEGER"
            ),
        },
    }
    for table_name, table_migrations in migrations.items():
        existing = _table_columns(conn, table_name)
        for column_name, statement in table_migrations.items():
            if column_name not in existing:
                conn.execute(statement)

    conn.execute("""CREATE TABLE IF NOT EXISTS shipment_price_backfill_runs (
        digest TEXT PRIMARY KEY, operator TEXT NOT NULL, recorded_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS shipment_price_backfill_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        digest TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK (source_type IN ('ordinary', 'assembly_item')),
        source_id INTEGER NOT NULL,
        manual_id INTEGER NOT NULL,
        unit_price_minor INTEGER NOT NULL,
        currency TEXT NOT NULL,
        operator TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        reason TEXT NOT NULL,
        UNIQUE (digest, source_type, source_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL UNIQUE, request_digest TEXT NOT NULL,
        operator TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, document_no TEXT NOT NULL UNIQUE,
        operation_id INTEGER NOT NULL REFERENCES delivery_operations(id),
        customer TEXT NOT NULL, recipient_name TEXT NOT NULL,
        recipient_phone TEXT NOT NULL, address TEXT NOT NULL,
        operator TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        invalidated INTEGER NOT NULL DEFAULT 0,
        UNIQUE(operation_id, customer)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_note_sources (
        note_id INTEGER NOT NULL REFERENCES delivery_notes(id),
        source_type TEXT NOT NULL CHECK(source_type IN ('ordinary', 'assembly')),
        source_id INTEGER NOT NULL,
        PRIMARY KEY(note_id, source_type, source_id),
        UNIQUE(source_type, source_id)
    )""")
    # Batch references survive replacement of assembly item IDs. Changes and
    # invalidation participate in the source transaction (including rollbacks).
    for source_type, table in [('ordinary', 'product_order_shipments'),
                               ('assembly', 'assembly_shipment_batches')]:
        for action in ('UPDATE', 'DELETE'):
            invalidation = ', invalidated = 1' if action == 'DELETE' else ''
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS delivery_{source_type}_{action.lower()}
                AFTER {action} ON {table} BEGIN
                UPDATE delivery_notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    {invalidation}
                WHERE id IN (SELECT note_id FROM delivery_note_sources
                    WHERE source_type = '{source_type}' AND source_id = OLD.id);
                END""")


class DeliveryOperationConflict(ValueError):
    pass


def delivery_request_identity(form, files, source_type):
    """Hash submitted intent only; never consult mutable product/stock defaults."""
    token = str(form.get('operation_token') or uuid.uuid4().hex).strip()
    if not token or len(token) > 200:
        raise ValueError('发货操作标识无效')
    fields = {key: form.getlist(key) for key in sorted(form)
              if key != 'operation_token'}
    attachments = []
    for file in files:
        position = file.stream.tell()
        digest = hashlib.sha256()
        try:
            file.stream.seek(0)
            for block in iter(lambda: file.stream.read(65536), b''):
                digest.update(block)
        finally:
            file.stream.seek(position)
        attachments.append([file.filename, file.mimetype, digest.hexdigest()])
    payload = json.dumps([source_type, fields, attachments], ensure_ascii=False,
                         sort_keys=True, separators=(',', ':'))
    return token, hashlib.sha256(payload.encode()).hexdigest()


def find_delivery_operation(conn, token, digest, operator):
    row = conn.execute('SELECT * FROM delivery_operations WHERE token=?', (token,)).fetchone()
    if row and (row['operator'] != operator or row['request_digest'] != digest):
        raise DeliveryOperationConflict('此发货操作标识已用于其他内容或用户，请刷新后重新提交')
    return dict(row) if row else None


def start_delivery_operation(conn, token, digest, operator):
    return conn.execute(
        'INSERT INTO delivery_operations (token, request_digest, operator, created_at) VALUES (?, ?, ?, ?)',
        (token, digest, operator, datetime.now(timezone.utc).isoformat()),
    ).lastrowid


def parse_recipient_overrides(form):
    try:
        overrides = json.loads(form.get('recipient_overrides') or '{}')
    except (ValueError, TypeError) as error:
        raise ValueError('收货资料格式无效') from error
    if not isinstance(overrides, dict):
        raise ValueError('收货资料格式无效')
    # Single-customer assembly form uses ordinary named inputs.
    if 'recipient_name' in form:
        overrides[str(form.get('customer') or '').strip()] = {
            field: form.get(field, '') for field in ('recipient_name', 'recipient_phone', 'address')
        }
    normalized = {}
    for customer, values in overrides.items():
        if not isinstance(values, dict):
            raise ValueError('收货资料格式无效')
        result = {}
        for field in ('recipient_name', 'recipient_phone', 'address'):
            if field in values:
                result[field] = str(values[field] or '').strip()
                limit = 1000 if field == 'address' else 100
                if len(result[field]) > limit:
                    raise ValueError(f'收货资料超过长度限制（{limit} 字符）')
        normalized[customer] = result
    return normalized


def delivery_source_items(conn, source_type, source_id):
    if source_type == 'ordinary':
        rows = conn.execute("""SELECT s.id AS source_id, s.shipped_at,
            s.shipped_quantity, m.drawing_no, m.product_name, m.unit,
            COALESCE(NULLIF(o.customer, ''), m.customer, '') AS customer,
            COALESCE(s.specification_snapshot, m.supplier, '') AS specification,
            o.order_no, s.signature_status, s.signature_image, s.signed_at
            FROM product_order_shipments s JOIN product_orders o ON o.id=s.order_id
            JOIN manuals m ON m.id=o.manual_id WHERE s.id=?""", (source_id,)).fetchall()
    elif source_type == 'assembly':
        rows = conn.execute("""SELECT b.id AS source_id, b.customer, b.shipped_at,
            i.shipped_quantity, i.drawing_no, i.product_name, COALESCE(m.unit, '') AS unit,
            COALESCE(i.specification_snapshot, m.supplier, '') AS specification,
            b.assembly_drawing_no,
            COALESCE((SELECT group_concat(DISTINCT o.order_no)
                FROM assembly_shipment_allocations a JOIN product_orders o ON o.id=a.order_id
                WHERE a.item_id=i.id), '未关联订单') AS order_no,
            '' AS signature_status,
            '' AS signature_image, '' AS signed_at
            FROM assembly_shipment_batches b JOIN assembly_shipment_items i ON i.batch_id=b.id
            LEFT JOIN manuals m ON m.id=i.manual_id WHERE b.id=? ORDER BY i.id""", (source_id,)).fetchall()
    else:
        raise ValueError('送货单来源类型无效')
    return [dict(row, source_type=source_type, order_customer=row['customer']) for row in rows]


def format_delivery_timestamp(value):
    if not value:
        return '-'
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S（北京时间）')


def create_delivery_notes(conn, operation_id, source_groups, recipient_overrides, operator):
    """Group authoritative sources by customer; caller owns commit/rollback.

    source_groups is an iterable of (source_type, source_id) references.
    """
    groups = {}
    for source_type, source_id in source_groups:
        items = delivery_source_items(conn, source_type, source_id)
        if not items:
            raise ValueError('送货单来源不存在')
        groups.setdefault(items[0]['customer'], []).append((source_type, source_id))
    if not groups:
        raise ValueError('送货单必须包含发货来源')
    now = datetime.now(timezone.utc).isoformat()
    note_ids = []
    for customer, refs in sorted(groups.items()):
        defaults = conn.execute('SELECT recipient_name, recipient_phone, address FROM customers WHERE name=?', (customer,)).fetchone()
        recipient = dict(defaults) if defaults else dict(recipient_name='', recipient_phone='', address='')
        recipient.update(recipient_overrides.get(customer, {}))
        document_no = f'DN-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:12].upper()}'
        note_id = conn.execute("""INSERT INTO delivery_notes (document_no, operation_id,
            customer, recipient_name, recipient_phone, address, operator, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (document_no, operation_id, customer, recipient['recipient_name'], recipient['recipient_phone'], recipient['address'], operator, now, now)).lastrowid
        conn.executemany('INSERT INTO delivery_note_sources (note_id, source_type, source_id) VALUES (?, ?, ?)',
                         [(note_id, kind, sid) for kind, sid in refs])
        note_ids.append(note_id)
    return note_ids


def load_delivery_note(conn, note_id):
    row = conn.execute('SELECT * FROM delivery_notes WHERE id=?', (note_id,)).fetchone()
    if row is None:
        return None
    note = dict(row)
    note['sources'] = [dict(row) for row in conn.execute(
        'SELECT source_type, source_id FROM delivery_note_sources WHERE note_id=? ORDER BY source_type, source_id', (note_id,))]
    note['items'] = []
    for source in note['sources']:
        items = delivery_source_items(conn, source['source_type'], source['source_id'])
        if not items or any(item['customer'] != note['customer'] for item in items):
            note['invalidated'] = 1
        note['items'].extend(items)
    if not note['sources']:
        note['invalidated'] = 1
    note['missing_recipient'] = any(not note[field] for field in ('recipient_name', 'recipient_phone', 'address'))
    return note


def normalize_recipient_fields(recipient_name, recipient_phone):
    recipient_name = str(recipient_name or "").strip()
    recipient_phone = str(recipient_phone or "").strip()
    if len(recipient_name) > 100:
        raise ValueError("收货人不能超过 100 个字符")
    if len(recipient_phone) > 100:
        raise ValueError("收货人联系方式不能超过 100 个字符")
    return recipient_name, recipient_phone


def current_specification_snapshots(conn, manual_ids):
    manual_ids = list(dict.fromkeys(int(manual_id) for manual_id in manual_ids))
    if not manual_ids:
        return {}
    placeholders = ",".join("?" for _ in manual_ids)
    rows = conn.execute(
        f"SELECT id, supplier FROM manuals WHERE id IN ({placeholders})",
        manual_ids,
    ).fetchall()
    snapshots = {
        int(row["id"]): str(row["supplier"] or "")
        for row in rows
    }
    if len(snapshots) != len(manual_ids):
        raise ValueError("产品不存在，无法记录发货规格")
    return snapshots
