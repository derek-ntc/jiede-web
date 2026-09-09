"""Schema and field helpers shared by the shipping workflow."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid


def _table_columns(conn, table_name):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _table_fingerprint(conn, table_name):
    """Count and hash every stored field (including source IDs and row IDs)."""
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(f'SELECT rowid, * FROM "{table_name}" ORDER BY rowid'):
        values = [[type(value).__name__, value.hex() if isinstance(value, bytes) else value]
                  for value in row]
        encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        count += 1
    return count, digest.hexdigest()


def ensure_supplemental_source_type(conn, table_name):
    """Verified, transactional widening of the three historical source CHECKs.

    Preserve the original table definition, all data, explicit schema objects,
    and AUTOINCREMENT high-water marks. A savepoint never commits caller work;
    interrupted copies/swaps roll back and startup can safely retry.
    """
    if table_name not in {'delivery_note_sources', 'finance_invoice_items',
                          'reconciliation_statement_items'}:
        raise ValueError('Unsupported source table')
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    if row is None:
        return
    schema = row[0]
    check = re.compile(r"(CHECK\s*\(\s*source_type\s+IN\s*\()([^)]*)(\)\s*\))", re.I)
    matches = list(check.finditer(schema))
    if len(matches) != 1:
        raise RuntimeError(f'Unrecognized source constraint: {table_name}')
    if "'supplemental'" in matches[0][2]:
        return
    new_schema = check.sub(lambda match: match[1] + match[2] + ", 'supplemental'" + match[3], schema)
    replacement = f'{table_name}_supplemental_migration_{uuid.uuid4().hex}'
    new_schema, replaced = re.subn(
        r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"' + table_name + r'"|`' + table_name + r'`|\[' + table_name + r'\]|' + table_name + r')(?=\s*\()',
        f'CREATE TABLE "{replacement}"', new_schema, count=1, flags=re.I,
    )
    if replaced != 1:
        raise RuntimeError(f'Unrecognized table definition: {table_name}')
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    savepoint = f'source_migration_{uuid.uuid4().hex}'
    conn.execute(f'SAVEPOINT {savepoint}')
    legacy_alter = conn.execute('PRAGMA legacy_alter_table').fetchone()[0]
    try:
        objects = [row[0] for row in conn.execute("""SELECT sql FROM sqlite_master
            WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL
            ORDER BY type, name""", (table_name,))]
        sequence = conn.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (table_name,)).fetchone()
        before = _table_fingerprint(conn, table_name)
        conn.execute(new_schema)
        columns = ', '.join('"' + row['name'].replace('"', '""') + '"'
                            for row in conn.execute(f'PRAGMA table_info("{table_name}")'))
        conn.execute(f'INSERT INTO "{replacement}" (rowid, {columns}) SELECT rowid, {columns} FROM "{table_name}"')
        if _table_fingerprint(conn, replacement) != before:
            raise RuntimeError(f'Source migration verification failed: {table_name}')
        conn.execute(f'DROP TABLE "{table_name}"')
        # Existing triggers/views on OTHER tables refer to the original name
        # during this brief gap. Do not rewrite or validate their SQL mid-swap.
        conn.execute('PRAGMA legacy_alter_table=ON')
        conn.execute(f'ALTER TABLE "{replacement}" RENAME TO "{table_name}"')
        for statement in objects:
            conn.execute(statement)
        if sequence is not None:
            conn.execute('UPDATE sqlite_sequence SET seq=? WHERE name=?', (sequence[0], table_name))
        if _table_fingerprint(conn, table_name) != before:
            raise RuntimeError(f'Source migration verification failed: {table_name}')
    except Exception:
        conn.execute(f'ROLLBACK TO {savepoint}')
        raise
    finally:
        conn.execute(f'PRAGMA legacy_alter_table={legacy_alter}')
        conn.execute(f'RELEASE {savepoint}')


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
            "remark": (
                "ALTER TABLE assembly_shipment_items "
                "ADD COLUMN remark TEXT NOT NULL DEFAULT ''"
            ),
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
    if 'customer_id' not in _table_columns(conn, 'delivery_notes'):
        # Old rows deliberately remain NULL: a historical name alone cannot
        # establish ownership if a rename/reassignment already happened.
        conn.execute('ALTER TABLE delivery_notes ADD COLUMN customer_id INTEGER')
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_note_sources (
        note_id INTEGER NOT NULL REFERENCES delivery_notes(id),
        source_type TEXT NOT NULL CHECK(source_type IN ('ordinary', 'assembly', 'supplemental')),
        source_id INTEGER NOT NULL,
        PRIMARY KEY(note_id, source_type, source_id),
        UNIQUE(source_type, source_id)
    )""")
    ensure_supplemental_source_type(conn, 'delivery_note_sources')
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_note_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER NOT NULL REFERENCES delivery_notes(id) ON DELETE CASCADE,
        sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
        manual_id INTEGER,
        source_type TEXT CHECK(source_type IN ('ordinary', 'assembly', 'supplemental')),
        source_id INTEGER,
        order_no TEXT NOT NULL DEFAULT '', assembly_drawing_no TEXT NOT NULL DEFAULT '',
        drawing_no TEXT NOT NULL DEFAULT '', product_name TEXT NOT NULL DEFAULT '',
        specification TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity >= 0),
        remark TEXT NOT NULL DEFAULT '' CHECK(length(remark) <= 500),
        shipped_at TEXT NOT NULL DEFAULT '',
        UNIQUE(note_id, sort_order),
        CHECK((source_type IS NULL AND source_id IS NULL) OR
              (source_type IS NOT NULL AND source_id IS NOT NULL AND source_id > 0))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS supplemental_shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id INTEGER NOT NULL REFERENCES delivery_operations(id),
        manual_id INTEGER NOT NULL,
        customer TEXT NOT NULL CHECK(length(trim(customer)) > 0),
        drawing_no TEXT NOT NULL, product_name TEXT NOT NULL,
        specification_snapshot TEXT NOT NULL DEFAULT '',
        sku TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '',
        shipped_quantity INTEGER NOT NULL CHECK(typeof(shipped_quantity)='integer' AND shipped_quantity > 0),
        inventory_deducted_quantity INTEGER NOT NULL CHECK(typeof(inventory_deducted_quantity)='integer' AND inventory_deducted_quantity >= 0),
        inventory_shortage_quantity INTEGER NOT NULL CHECK(typeof(inventory_shortage_quantity)='integer' AND inventory_shortage_quantity >= 0),
        shipped_at TEXT NOT NULL, logistics_no TEXT NOT NULL DEFAULT '',
        remark TEXT NOT NULL DEFAULT '' CHECK(length(remark) <= 500),
        unit_price_minor INTEGER CHECK(unit_price_minor IS NULL OR (typeof(unit_price_minor)='integer' AND unit_price_minor >= 0)),
        currency TEXT NOT NULL,
        price_recorded_by TEXT NOT NULL DEFAULT '', price_recorded_at TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK(inventory_deducted_quantity + inventory_shortage_quantity = shipped_quantity)
    )""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_supplemental_shipments_operation ON supplemental_shipments(operation_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_supplemental_shipments_customer ON supplemental_shipments(customer, shipped_at)')
    conn.execute('''CREATE TABLE IF NOT EXISTS supplemental_shipment_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES supplemental_shipments(id),
        filename TEXT NOT NULL, original_filename TEXT NOT NULL, content_type TEXT NOT NULL,
        file_size INTEGER NOT NULL, uploaded_at TEXT NOT NULL, uploaded_ip TEXT NOT NULL,
        uploaded_user_agent TEXT NOT NULL)''')
    # Batch references survive replacement of assembly item IDs. Changes and
    # invalidation participate in the source transaction (including rollbacks).
    for source_type, table in [('ordinary', 'product_order_shipments'),
                               ('assembly', 'assembly_shipment_batches'),
                               ('supplemental', 'supplemental_shipments')]:
        for action in ('UPDATE', 'DELETE'):
            invalidation = ', invalidated = 1' if action == 'DELETE' else ''
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS delivery_{source_type}_{action.lower()}
                AFTER {action} ON {table} BEGIN
                UPDATE delivery_notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    {invalidation}
                WHERE id IN (SELECT note_id FROM delivery_note_sources
                    WHERE source_type = '{source_type}' AND source_id = OLD.id);
                END""")
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS delivery_zero_{source_type}_{action.lower()}
                AFTER {action} ON {table} BEGIN
                UPDATE delivery_notes SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') {invalidation}
                WHERE operation_id IN (SELECT n.operation_id FROM delivery_notes n
                    JOIN delivery_note_sources s ON s.note_id=n.id
                    WHERE s.source_type='{source_type}' AND s.source_id=OLD.id)
                AND NOT EXISTS (SELECT 1 FROM delivery_note_sources s WHERE s.note_id=delivery_notes.id);
                END''')
    for action in ('UPDATE', 'DELETE'):
        invalidation = ', invalidated=1' if action == 'DELETE' else ''
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS delivery_operation_{action.lower()}
            AFTER {action} ON delivery_operations BEGIN
            UPDATE delivery_notes SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') {invalidation}
            WHERE operation_id=OLD.id;
            END''')
    conn.execute("""CREATE TRIGGER IF NOT EXISTS delivery_ordinary_order_update
        AFTER UPDATE OF order_no, manual_id, customer ON product_orders BEGIN
        UPDATE delivery_notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id IN (SELECT d.note_id FROM delivery_note_sources d
            JOIN product_order_shipments s ON s.id=d.source_id
            WHERE d.source_type='ordinary' AND s.order_id=OLD.id);
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
            s.shipped_quantity, m.id AS manual_id, m.drawing_no, m.product_name, m.unit, s.remark,
            COALESCE(NULLIF(o.customer, ''), m.customer, '') AS customer, c.id AS customer_id,
            COALESCE(s.specification_snapshot, m.supplier, '') AS specification,
            s.specification_snapshot IS NULL AS specification_is_fallback,
            o.order_no, s.signature_status, s.signature_image, s.signed_at
            FROM product_order_shipments s JOIN product_orders o ON o.id=s.order_id
            JOIN manuals m ON m.id=o.manual_id
            LEFT JOIN customers c ON c.name=COALESCE(NULLIF(o.customer, ''), m.customer, '')
            WHERE s.id=?""", (source_id,)).fetchall()
    elif source_type == 'assembly':
        rows = conn.execute("""SELECT b.id AS source_id, b.customer, c.id AS customer_id, b.shipped_at,
            i.shipped_quantity, i.manual_id, i.drawing_no, i.product_name, COALESCE(m.unit, '') AS unit,
            COALESCE(i.specification_snapshot, m.supplier, '') AS specification,
            i.specification_snapshot IS NULL AS specification_is_fallback,
            b.assembly_drawing_no, i.remark,
            COALESCE((SELECT group_concat(allocation_label, ' / ') FROM (
                SELECT CASE WHEN o.id IS NULL
                    THEN CASE WHEN EXISTS (
                        SELECT 1 FROM assembly_shipment_allocations linked
                        WHERE linked.item_id=i.id AND linked.order_id IS NOT NULL
                    ) THEN '未关联订单（' || SUM(a.quantity) || '）' ELSE '未关联订单' END
                    ELSE o.order_no END AS allocation_label,
                    CASE WHEN o.id IS NULL THEN 1 ELSE 0 END AS no_order
                FROM assembly_shipment_allocations a
                LEFT JOIN product_orders o ON o.id=a.order_id
                WHERE a.item_id=i.id
                GROUP BY a.order_id, o.id, o.order_no
                ORDER BY no_order, o.order_no
            )), '未关联订单') AS order_no,
            '' AS signature_status,
            '' AS signature_image, '' AS signed_at
            FROM assembly_shipment_batches b JOIN assembly_shipment_items i ON i.batch_id=b.id
            LEFT JOIN manuals m ON m.id=i.manual_id
            LEFT JOIN customers c ON c.name=b.customer WHERE b.id=? ORDER BY i.id""", (source_id,)).fetchall()
    elif source_type == 'supplemental':
        rows = conn.execute("""SELECT s.id AS source_id, s.manual_id, s.customer,
            c.id AS customer_id, s.shipped_at, s.shipped_quantity, s.drawing_no,
            s.product_name, s.unit, s.specification_snapshot AS specification,
            0 AS specification_is_fallback, '未关联订单' AS order_no,
            '' AS assembly_drawing_no, s.remark,
            '' AS signature_status, '' AS signature_image, '' AS signed_at
            FROM supplemental_shipments s LEFT JOIN customers c ON c.name=s.customer
            WHERE s.id=?""", (source_id,)).fetchall()
    else:
        raise ValueError('送货单来源类型无效')
    return [dict(row, source_type=source_type, order_customer=row['customer']) for row in rows]


def supplemental_source_select():
    """Shared priced-source projection; never fall back to mutable master data."""
    return """SELECT 'supplemental' AS source_type, id AS source_id,
        trim(customer) AS customer_name, '未关联订单' AS order_no,
        NULL AS assembly_batch_id, '' AS assembly_drawing_no, shipped_at,
        'BC-' || id AS delivery_no, manual_id, sku, drawing_no, product_name,
        model, specification_snapshot AS specification, unit,
        shipped_quantity AS quantity, unit_price_minor, currency,
        unit_price_minor * shipped_quantity AS line_total_minor, remark,
        price_recorded_by, price_recorded_at
        FROM supplemental_shipments WHERE shipped_quantity > 0"""


def format_delivery_timestamp(value):
    if not value:
        return '-'
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S（北京时间）')


def parse_order_shipment_lines(form):
    """Normalize modern product rows and legacy order/quantity form lists."""
    from assembly_shipping import parse_nonnegative_int, parse_positive_int
    if 'shipment_lines' in form:
        try:
            rows = json.loads(form['shipment_lines'])
        except (TypeError, ValueError) as error:
            raise ValueError('发货明细格式无效') from error
        legacy = False
    else:
        ids, quantities = form.getlist('order_id'), form.getlist('shipped_quantity')
        remarks = form.getlist('line_remark')
        if len(ids) != len(quantities) or (remarks and len(remarks) != len(ids)):
            raise ValueError('订单、数量和备注必须一一对应')
        rows = [dict(order_id=oid, quantity=qty, remark=remarks[i] if remarks else '', source_kind='order')
                for i, (oid, qty) in enumerate(zip(ids, quantities)) if str(oid).strip() or str(qty).strip()]
        legacy = True
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ValueError('请提交 1 至 500 行发货明细')
    normalized = []
    for raw in rows:
        if not isinstance(raw, dict) or raw.get('source_kind') not in ('order', 'extra'):
            raise ValueError('发货明细来源无效')
        remark = raw.get('remark', '')
        if not isinstance(remark, str) or len(remark) > 500:
            raise ValueError('发货行备注不能超过 500 个字符')
        order_id = raw.get('order_id')
        if order_id is not None:
            order_id = parse_positive_int(order_id, '订单 ID')
        if raw['source_kind'] == 'order' and order_id is None:
            raise ValueError('请选择有效的订单')
        normalized.append(dict(
            order_id=order_id, source_kind=raw['source_kind'],
            manual_id=None if legacy else parse_positive_int(raw.get('manual_id'), '产品 ID'),
            customer=None if legacy else str(raw.get('customer') or '').strip(),
            quantity=parse_nonnegative_int(raw.get('quantity'), '发货数量'),
            remark=remark, legacy=legacy))
    if not any(row['quantity'] > 0 for row in normalized):
        raise ValueError('至少有一个产品的发货数量必须大于 0')
    return normalized


def create_delivery_notes(conn, operation_id, source_groups, recipient_overrides, operator,
                          *, display_lines=None):
    """Group authoritative sources by customer; caller owns commit/rollback.

    source_groups is an iterable of (source_type, source_id) references.
    display_lines optionally supplies the full ordered document, including zero
    rows, with an explicit customer per row. Without it, snapshot source rows.
    Source links are optional (a merged row may span several source records).
    An additional customer may have only zero rows: each such row must belong
    to that customer's current product/optional order. The operation still
    requires positive sources, but that customer's note has snapshot rows only.
    """
    groups = {}
    source_lines = []
    for source_type, source_id in source_groups:
        items = delivery_source_items(conn, source_type, source_id)
        if not items:
            raise ValueError('送货单来源不存在')
        groups.setdefault(items[0]['customer'], []).append((source_type, source_id))
        source_lines.extend(items)
    if not groups:
        raise ValueError('送货单必须包含发货来源')
    line_groups = {customer: [] for customer in groups}
    for raw in source_lines if display_lines is None else display_lines:
        line = dict(raw)
        customer = line.get('customer')
        if customer not in groups or not groups[customer]:
            # Zero-only customer documents have no accounting source. Accept
            # only a real, currently customer-owned product; never arbitrary text.
            manual = conn.execute('SELECT customer FROM manuals WHERE id=?', (line.get('manual_id'),)).fetchone()
            if line.get('quantity') != 0 or not customer or manual is None or manual['customer'] != customer:
                raise ValueError('送货单明细客户与发货来源不一致')
            if line.get('order_id') is not None:
                order = conn.execute('SELECT manual_id,customer FROM product_orders WHERE id=?', (line['order_id'],)).fetchone()
                if order is None or order['manual_id'] != line.get('manual_id') or (order['customer'] or manual['customer']) != customer:
                    raise ValueError('送货单明细客户与订单不一致')
            groups.setdefault(customer, [])
            line_groups.setdefault(customer, [])
        quantity = line.get('quantity', line.get('shipped_quantity'))
        if type(quantity) is not int or not 0 <= quantity <= 2_147_483_647:
            raise ValueError('送货单数量必须为非负整数')
        remark = str(line.get('remark') or '')
        if len(remark) > 500:
            raise ValueError('发货行备注不能超过 500 个字符')
        kind, sid = line.get('source_type'), line.get('source_id')
        if (kind is not None or sid is not None) and (kind, sid) not in groups[customer]:
            raise ValueError('送货单明细来源不属于当前客户发货')
        line.update(quantity=quantity, remark=remark)
        line_groups[customer].append(line)
    if any(not lines for lines in line_groups.values()):
        raise ValueError('送货单必须包含明细')
    now = datetime.now(timezone.utc).isoformat()
    note_ids = []
    for customer, refs in sorted(groups.items()):
        defaults = conn.execute('SELECT id, recipient_name, recipient_phone, address FROM customers WHERE name=?', (customer,)).fetchone()
        recipient = dict(defaults) if defaults else dict(recipient_name='', recipient_phone='', address='')
        recipient.update(recipient_overrides.get(customer, {}))
        document_no = f'DN-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:12].upper()}'
        note_id = conn.execute("""INSERT INTO delivery_notes (document_no, operation_id,
            customer, recipient_name, recipient_phone, address, operator, created_at, updated_at, customer_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (document_no, operation_id, customer, recipient['recipient_name'], recipient['recipient_phone'], recipient['address'], operator, now, now,
             defaults['id'] if defaults else None)).lastrowid
        conn.executemany('INSERT INTO delivery_note_sources (note_id, source_type, source_id) VALUES (?, ?, ?)',
                         [(note_id, kind, sid) for kind, sid in refs])
        for sort_order, line in enumerate(line_groups[customer]):
            conn.execute('''INSERT INTO delivery_note_items
                (note_id,sort_order,manual_id,source_type,source_id,order_no,assembly_drawing_no,
                 drawing_no,product_name,specification,unit,quantity,remark,shipped_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (note_id, sort_order, line.get('manual_id'), line.get('source_type'), line.get('source_id'),
                 *[str(line.get(field) or '') for field in ('order_no', 'assembly_drawing_no',
                     'drawing_no', 'product_name', 'specification', 'unit')],
                 line['quantity'], line['remark'], str(line.get('shipped_at') or '')))
        note_ids.append(note_id)
    return note_ids


def load_delivery_note(conn, note_id):
    row = conn.execute('SELECT * FROM delivery_notes WHERE id=?', (note_id,)).fetchone()
    if row is None:
        return None
    note = dict(row)
    note['sources'] = [dict(row) for row in conn.execute(
        'SELECT source_type, source_id FROM delivery_note_sources WHERE note_id=? ORDER BY source_type, source_id', (note_id,))]
    snapshots = [dict(row) for row in conn.execute(
        'SELECT * FROM delivery_note_items WHERE note_id=? ORDER BY sort_order, id', (note_id,))]
    note['has_item_snapshots'] = bool(snapshots)
    note['items'] = []
    live_signature_metadata = {}
    for source in note['sources']:
        items = delivery_source_items(conn, source['source_type'], source['source_id'])
        if items:
            live_signature_metadata[(source['source_type'], source['source_id'])] = {
                field: items[0][field] for field in ('signature_status', 'signature_image', 'signed_at')
            }
        # ID-bound notes must never fall back to a matching name (which may now
        # belong to a different customer). Unbound legacy/no-master sources use
        # only exact original names; unknown historical renames remain invalid.
        if not items or any(
            item['customer_id'] != note['customer_id']
            if note['customer_id'] is not None
            else item['customer'] != note['customer']
            for item in items
        ):
            note['invalidated'] = 1
        if not snapshots:
            note['items'].extend(items)
    if snapshots:
        note['items'] = [dict(item, shipped_quantity=item['quantity'],
                             customer=note['customer'], order_customer=note['customer'],
                             customer_id=note['customer_id'], specification_is_fallback=0,
                             signature_status='', signature_image='', signed_at='')
                         for item in snapshots]
        for item in note['items']:
            item.update(live_signature_metadata.get((item['source_type'], item['source_id']), {}))
    if not note['sources'] and (not snapshots or any(item['quantity'] != 0 for item in snapshots)):
        note['invalidated'] = 1
    if not note['sources'] and note['customer_id'] is not None and conn.execute(
            'SELECT id FROM customers WHERE id=?', (note['customer_id'],)).fetchone() is None:
        note['invalidated'] = 1
    note['missing_recipient'] = any(not note[field] for field in ('recipient_name', 'recipient_phone', 'address'))
    return note


def bind_legacy_delivery_customer_identity(conn, customer_id, previous_name):
    """Bind provably owned old notes immediately BEFORE a controlled rename.

    Caller owns the same transaction as customer/source renaming. Never repair
    an already invalidated note or infer ownership across a name mismatch.
    """
    candidates = conn.execute('''SELECT id FROM delivery_notes WHERE customer_id IS NULL
        AND customer=? AND invalidated=0''', (previous_name,)).fetchall()
    for row in candidates:
        note = load_delivery_note(conn, row['id'])
        source_items = [item for source in note['sources'] for item in
                        delivery_source_items(conn, source['source_type'], source['source_id'])]
        if not note['invalidated'] and source_items and all(item['customer_id'] == customer_id for item in source_items):
            conn.execute('UPDATE delivery_notes SET customer_id=? WHERE id=?', (customer_id, row['id']))


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
