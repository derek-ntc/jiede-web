"""Shared product identity, customer applicability, and recoverable legacy merge."""
import json
from datetime import datetime, timezone


def identity(drawing_no, product_name, specification):
    return tuple(str(value or '').strip().casefold() for value in (drawing_no, product_name, specification))


def customer_matches_sql(alias='manuals'):
    return f'EXISTS (SELECT 1 FROM product_customer_names pcn WHERE pcn.manual_id={alias}.id AND pcn.customer=? COLLATE NOCASE)'


def has_customer(conn, manual_id, customer):
    return conn.execute('SELECT 1 FROM product_customer_names WHERE manual_id=? AND customer=? COLLATE NOCASE',
                        (manual_id, str(customer or '').strip())).fetchone() is not None


def customer_names(conn, manual_id):
    return [r[0] for r in conn.execute('SELECT customer FROM product_customer_names WHERE manual_id=? ORDER BY customer', (manual_id,))]


def link_customer(conn, manual_id, customer, now):
    customer = str(customer or '').strip()
    if not customer:
        return
    conn.execute('INSERT OR IGNORE INTO customers(name,created_at,updated_at) VALUES (?,?,?)', (customer,now,now))
    row = conn.execute('SELECT id FROM customers WHERE name=? COLLATE NOCASE', (customer,)).fetchone()
    conn.execute('INSERT OR IGNORE INTO product_customers(manual_id,customer_id,created_at,updated_at) VALUES (?,?,?,?)', (manual_id,row[0],now,now))


def _columns(conn, table):
    return {r['name'] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def ensure_shared_products(conn):
    """Called inside the application initialization transaction after all tables exist."""
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn.execute('''CREATE TABLE IF NOT EXISTS product_customers (
        manual_id INTEGER NOT NULL REFERENCES manuals(id) ON DELETE CASCADE,
        customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(manual_id,customer_id))''')
    conn.execute('''CREATE VIEW IF NOT EXISTS product_customer_names AS
        SELECT pc.manual_id, c.id AS customer_id, c.name AS customer
        FROM product_customers pc JOIN customers c ON c.id=pc.customer_id
        UNION SELECT m.id, c.id, TRIM(m.customer) FROM manuals m
        LEFT JOIN customers c ON c.name=TRIM(m.customer)
        WHERE TRIM(COALESCE(m.customer,''))!='' ''')
    conn.execute('''CREATE TABLE IF NOT EXISTS product_merge_archive (
        source_id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL,
        original_json TEXT NOT NULL, merged_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS product_code_aliases (
        code TEXT NOT NULL, manual_id INTEGER NOT NULL REFERENCES manuals(id) ON DELETE CASCADE,
        PRIMARY KEY(code,manual_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS product_merge_conflicts (
        source_id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, reason TEXT NOT NULL)''')
    if 'customer' not in _columns(conn, 'product_assembly_components'):
        conn.execute('''CREATE TABLE product_assembly_components_shared (
            id INTEGER PRIMARY KEY AUTOINCREMENT, manual_id INTEGER NOT NULL REFERENCES manuals(id) ON DELETE CASCADE,
            assembly_drawing_no TEXT NOT NULL, quantity_per_set INTEGER NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            customer TEXT NOT NULL DEFAULT '', UNIQUE(manual_id,customer,assembly_drawing_no))''')
        conn.execute('''INSERT INTO product_assembly_components_shared
            SELECT pc.id,pc.manual_id,pc.assembly_drawing_no,pc.quantity_per_set,pc.sort_order,
                   pc.created_at,pc.updated_at,TRIM(m.customer)
            FROM product_assembly_components pc JOIN manuals m ON m.id=pc.manual_id''')
        conn.execute('DROP TABLE product_assembly_components')
        conn.execute('ALTER TABLE product_assembly_components_shared RENAME TO product_assembly_components')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_shared_bom_customer ON product_assembly_components(customer,assembly_drawing_no,manual_id)')
    conn.execute('''CREATE TRIGGER IF NOT EXISTS shared_bom_legacy_customer AFTER INSERT ON product_assembly_components
        WHEN NEW.customer='' BEGIN UPDATE product_assembly_components
        SET customer=COALESCE((SELECT TRIM(customer) FROM manuals WHERE id=NEW.manual_id),'') WHERE id=NEW.id; END''')
    conn.execute('CREATE TABLE IF NOT EXISTS shared_product_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
    if conn.execute("SELECT 1 FROM shared_product_migrations WHERE version='v1'").fetchone():
        return
    rows = list(conn.execute('SELECT * FROM manuals ORDER BY id'))
    groups = {}
    for row in rows:
        link_customer(conn, row['id'], row['customer'], now)
        key = identity(row['drawing_no'],row['product_name'],row['supplier'])
        if key[0] and key[1]:
            groups.setdefault(key, []).append(row)
    for products in groups.values():
        target = products[0]
        for source in products[1:]:
            target = conn.execute('SELECT * FROM manuals WHERE id=?', (target['id'],)).fetchone()
            reason = _merge_conflict(conn, target, source)
            if reason:
                conn.execute('INSERT OR REPLACE INTO product_merge_conflicts VALUES (?,?,?)', (source['id'],target['id'],reason))
                continue
            _merge_product(conn, target, source, now)
    conn.execute("INSERT INTO shared_product_migrations VALUES ('v1',?)", (now,))


def _merge_conflict(conn, target, source):
    # Different units/prices/recipes need a human decision, never silently discard them.
    if target['unit'] and source['unit'] and target['unit'] != source['unit']:
        return '单位不一致'
    if target['unit_price_minor'] is not None and source['unit_price_minor'] is not None and (
            target['unit_price_minor'],target['currency']) != (source['unit_price_minor'],source['currency']):
        return '产品价格不一致'
    configured = [conn.execute('SELECT 1 FROM manual_process_configs WHERE manual_id=?', (r['id'],)).fetchone() for r in (target,source)]
    if all(configured):
        steps = [[r['name'].casefold() for r in conn.execute('SELECT name FROM manual_process_steps WHERE manual_id=? ORDER BY sort_order,id', (p['id'],))] for p in (target,source)]
        if steps[0] != steps[1]:
            return '生产工艺顺序不一致'
    for field in ('pack_quantity','pack_carton_size','pack_weight'):
        if target[field] and source[field] and target[field] != source[field]:
            return '包装信息不一致'
    conflict = conn.execute('''SELECT 1 FROM product_assembly_components a JOIN product_assembly_components b
        ON a.customer=b.customer AND a.assembly_drawing_no=b.assembly_drawing_no COLLATE NOCASE
        WHERE a.manual_id=? AND b.manual_id=? AND a.quantity_per_set!=b.quantity_per_set''', (target['id'],source['id'])).fetchone()
    return '同一客户组装件用量不一致' if conflict else ''


def _merge_product(conn, target, source, now):
    keep, old = target['id'], source['id']
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    related = {t:[dict(r) for r in conn.execute(f'SELECT * FROM "{t}" WHERE manual_id=?', (old,))]
               for t in tables if 'manual_id' in _columns(conn,t)}
    conn.execute('INSERT INTO product_merge_archive VALUES (?,?,?,?)',
                 (old,keep,json.dumps(dict(product=dict(source),related=related),ensure_ascii=False),now))
    for field in ('unit','unit_price_minor','filename','original_filename','sku','barcode','model','category','description_html','remark','default_location_id','pack_quantity','pack_carton_size','pack_weight'):
        if target[field] in (None,'') and source[field] not in (None,''):
            conn.execute(f'UPDATE manuals SET "{field}"=? WHERE id=?', (source[field],keep))
            if field == 'unit_price_minor':
                conn.execute('UPDATE manuals SET currency=? WHERE id=?', (source['currency'],keep))
            elif field == 'filename':
                conn.execute('UPDATE manuals SET file_type=? WHERE id=?', (source['file_type'],keep))
    conn.execute('UPDATE manuals SET min_stock=? WHERE id=?', (max(target['min_stock'] or 0,source['min_stock'] or 0),keep))
    # Fill legacy blank order customers BEFORE replacing the referenced product.
    conn.execute("UPDATE product_orders SET customer=? WHERE manual_id=? AND TRIM(COALESCE(customer,''))=''", (source['customer'],old))
    conn.execute("UPDATE product_orders SET customer=? WHERE manual_id=? AND TRIM(COALESCE(customer,''))=''", (target['customer'],keep))
    for code in (source['sku'],source['barcode'],source['qr_code'],f'P{old:06d}'):
        if code:
            conn.execute('INSERT OR IGNORE INTO product_code_aliases VALUES (?,?)', (code,keep))
    for row in related.get('inventory_balances',[]):
        existing = conn.execute('SELECT quantity FROM inventory_balances WHERE manual_id=? AND location_id=?', (keep,row['location_id'])).fetchone()
        total = row['quantity'] + (existing[0] if existing else 0)
        if not 0 <= total <= 9223372036854775807:
            raise ValueError('合并库存数量超出可保存范围')
        conn.execute('''INSERT INTO inventory_balances(manual_id,location_id,quantity,updated_at) VALUES (?,?,?,?)
            ON CONFLICT(manual_id,location_id) DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at''', (keep,row['location_id'],total,now))
    conn.execute('DELETE FROM inventory_balances WHERE manual_id=?', (old,))
    for row in related.get('product_customers',[]):
        conn.execute('INSERT OR IGNORE INTO product_customers VALUES (?,?,?,?)', (keep,row['customer_id'],row['created_at'],now))
    conn.execute('DELETE FROM product_customers WHERE manual_id=?', (old,))
    for row in related.get('product_assembly_components',[]):
        match = conn.execute('''SELECT id FROM product_assembly_components WHERE manual_id=? AND customer=?
            AND assembly_drawing_no=? COLLATE NOCASE''', (keep,row['customer'],row['assembly_drawing_no'])).fetchone()
        if match:
            conn.execute('DELETE FROM product_assembly_components WHERE id=?', (row['id'],))
        else:
            conn.execute('UPDATE product_assembly_components SET manual_id=? WHERE id=?', (keep,row['id']))
    for row in related.get('manual_process_configs',[]):
        conn.execute('INSERT OR IGNORE INTO manual_process_configs VALUES (?,?,?)', (keep,row['created_at'],now))
    for row in related.get('manual_process_steps',[]):
        same = conn.execute('SELECT id FROM manual_process_steps WHERE manual_id=? AND name=? COLLATE NOCASE', (keep,row['name'])).fetchone()
        if same:
            conn.execute('DELETE FROM manual_process_steps WHERE id=?', (row['id'],))
        else:
            conn.execute('UPDATE manual_process_steps SET manual_id=? WHERE id=?', (keep,row['id']))
    conn.execute('DELETE FROM manual_process_configs WHERE manual_id=?', (old,))
    for row in related.get('product_code_aliases',[]):
        conn.execute('INSERT OR IGNORE INTO product_code_aliases VALUES (?,?)', (row['code'],keep))
    conn.execute('DELETE FROM product_code_aliases WHERE manual_id=?', (old,))
    # Legacy primary-file backfills can point both products at one physical
    # upload. Keep a single row so deleting an apparent duplicate cannot remove
    # the file underneath the other reference. Source rows remain in the archive.
    for row in related.get('manual_files',[]):
        existing = conn.execute('SELECT id FROM manual_files WHERE manual_id=? AND filename=?', (keep,row['filename'])).fetchone()
        if existing:
            conn.execute('DELETE FROM manual_files WHERE id=?', (row['id'],))
        else:
            conn.execute('UPDATE manual_files SET manual_id=? WHERE id=?', (keep,row['id']))
    handled = {'inventory_balances','product_customers','product_assembly_components','manual_process_configs','manual_process_steps','product_code_aliases','manual_files'}
    for table in related.keys() - handled:
        conn.execute(f'UPDATE "{table}" SET manual_id=? WHERE manual_id=?', (keep,old))
    conn.execute('DELETE FROM manuals WHERE id=?', (old,))
    conn.execute('DELETE FROM product_merge_conflicts WHERE source_id=?', (old,))
