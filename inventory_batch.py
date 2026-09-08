"""Batch inventory queries and atomic validation; writes reuse the stock ledger."""
import hashlib
import json
import re
import uuid
from datetime import datetime

MAX_QUANTITY = 2_147_483_647
MAX_ROWS = 500


class InventoryConflict(ValueError):
    def __init__(self, message, needs_confirmation=False):
        super().__init__(message)
        self.needs_confirmation = needs_confirmation


def ensure_batch_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory_batch_receipts (
        token_digest TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
        batch_no TEXT NOT NULL UNIQUE, mode TEXT NOT NULL, operator TEXT NOT NULL,
        row_count INTEGER NOT NULL, created_at TEXT NOT NULL)""")


def whole_number(value, label, minimum=0):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(f'{label}必须为整数')
    if len(str(value)) > 10 or not minimum <= int(value) <= MAX_QUANTITY:
        raise ValueError(f'{label}必须在 {minimum} 至 {MAX_QUANTITY} 之间')
    return int(value)


def load_batch_data(conn, mode, filters):
    customer, assembly, order_no, query = (filters.get(k, '').strip() for k in ('customer', 'assembly', 'order_no', 'q'))
    locations = [dict(r) for r in conn.execute('SELECT id,name,code FROM warehouse_locations WHERE enabled=1 ORDER BY code COLLATE NOCASE,id')]
    active = {r['id'] for r in locations}
    location_id = whole_number(filters['location_id'], '库位', 1) if filters.get('location_id') else None
    if location_id and location_id not in active:
        raise ValueError('所选库位已停用或不存在')
    order_mode = mode == 'inbound' and bool(order_no)
    fields = 'm.id AS manual_id,m.product_name,m.drawing_no,m.sku,m.default_location_id'
    params = []
    where = []
    if order_mode:
        sql = f'''SELECT {fields}, o.id AS order_id, o.order_no, TRIM(o.customer) AS customer,
            o.quantity AS ordered_quantity, COALESCE(o.inventory_received_quantity,0) AS received,
            o.assembly_drawing_no AS assembly,
            COALESCE((SELECT quantity_per_set FROM product_assembly_components pc
                WHERE pc.manual_id=m.id AND pc.assembly_drawing_no=o.assembly_drawing_no COLLATE NOCASE LIMIT 1),0) AS quantity_per_set
            FROM product_orders o JOIN manuals m ON m.id=o.manual_id'''
        where.append('o.order_no=?')
        params.append(order_no)
        if customer:
            where.append('TRIM(o.customer)=?')
            params.append(customer)
        if assembly:
            where.append('o.assembly_drawing_no=? COLLATE NOCASE')
            params.append(assembly)
    else:
        sql = f'''SELECT {fields}, TRIM(m.customer) AS customer, 0 AS order_id,
            '' AS order_no, 0 AS ordered_quantity, 0 AS received,
            ? AS assembly, COALESCE((SELECT quantity_per_set FROM product_assembly_components pc
              WHERE pc.manual_id=m.id AND pc.assembly_drawing_no=? COLLATE NOCASE LIMIT 1),0) AS quantity_per_set
            FROM manuals m'''
        params.extend([assembly, assembly])
        if customer and not order_no:
            where.append('TRIM(m.customer)=?')
            params.append(customer)
        if order_no:
            where.append("""EXISTS (SELECT 1 FROM product_orders o WHERE o.manual_id=m.id
                AND o.order_no=? AND (?='' OR TRIM(o.customer)=?)
                AND (?='' OR o.assembly_drawing_no=? COLLATE NOCASE))""")
            params.extend([order_no, customer, customer, assembly, assembly])
        elif assembly:
            where.append('EXISTS (SELECT 1 FROM product_assembly_components pc WHERE pc.manual_id=m.id AND pc.assembly_drawing_no=? COLLATE NOCASE)')
            params.append(assembly)
    if query:
        where.append('(m.product_name LIKE ? OR m.drawing_no LIKE ? OR m.sku LIKE ?)')
        params.extend(['%' + query + '%'] * 3)
    sql += (' WHERE ' + ' AND '.join(where)) if where else ''
    sql += ' ORDER BY m.id' + (',o.id' if order_mode else '') + ' LIMIT 501'
    products = [dict(r) for r in conn.execute(sql, params)]
    query_error = ''
    if len(products) > MAX_ROWS:
        query_error = '结果超过 500 行，请缩小客户、订单或产品筛选范围'
        products = []
    rows = []
    for product in products:
        balances = {str(r['location_id']): int(r['quantity']) for r in conn.execute(
            'SELECT location_id,quantity FROM inventory_balances WHERE manual_id=?', (product['manual_id'],))}
        product['balances'] = balances
        product['total_stock'] = sum(balances.values())
        default = product['default_location_id']
        if default not in active:
            default = locations[0]['id'] if locations else ''
        choices = [location_id or default]
        if mode == 'adjust' and not location_id:
            choices = [int(loc) for loc in balances if int(loc) in active] or [default]
        for loc in choices:
            row = dict(product, location_id=loc)
            row['key'] = (f"o:{row['order_id']}" if order_mode else f"p:{row['manual_id']}")
            if mode == 'adjust':
                row['key'] += f':{loc}'
            rows.append(row)
    if len(rows) > MAX_ROWS:
        query_error = '产品库位明细超过 500 行，请选择具体库位后查询'
        rows = []
    customers = [r[0] for r in conn.execute("""SELECT customer FROM (
        SELECT TRIM(customer) AS customer FROM manuals UNION SELECT TRIM(customer) FROM product_orders)
        WHERE customer!='' ORDER BY customer COLLATE NOCASE""")]
    assemblies = [r[0] for r in conn.execute("""SELECT DISTINCT pc.assembly_drawing_no
        FROM product_assembly_components pc JOIN manuals m ON m.id=pc.manual_id
        WHERE (?='' OR TRIM(m.customer)=?) UNION SELECT DISTINCT assembly_drawing_no
        FROM product_orders WHERE assembly_drawing_no!='' AND (?='' OR TRIM(customer)=?) ORDER BY 1""", (customer,customer,customer,customer))]
    orders = [r[0] for r in conn.execute("SELECT DISTINCT order_no FROM product_orders WHERE (?='' OR TRIM(customer)=?) ORDER BY order_no", (customer,customer))]
    return dict(rows=rows, locations=locations, customers=customers, assemblies=assemblies, orders=orders, order_mode=order_mode, query_error=query_error)


def apply_batch(conn, snapshot, payload, token_digest, operator, transact):
    mode = snapshot['mode']
    submitted = payload.get('rows')
    remark = payload.get('remark', '')
    if not isinstance(remark, str) or len(remark) > 500:
        raise ValueError('备注不能超过 500 字')
    remark = remark.strip()
    if mode == 'adjust' and not remark:
        raise ValueError('请填写库存调整原因')
    if not isinstance(submitted, list) or not 1 <= len(submitted) <= MAX_ROWS:
        raise ValueError('请勾选 1 至 500 行明细')
    request_digest = hashlib.sha256(json.dumps([mode, submitted, remark], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    receipt = conn.execute('SELECT * FROM inventory_batch_receipts WHERE token_digest=?', (token_digest,)).fetchone()
    if receipt:
        if receipt['request_digest'] != request_digest:
            raise InventoryConflict('这批数据已提交，请重新查询后操作')
        return dict(batch_no=receipt['batch_no'], row_count=receipt['row_count'], duplicate=True)
    allowed = {row['key']: row for row in snapshot['rows']}
    seen, targets, validated, warnings = set(), set(), [], []
    for item in submitted:
        if not isinstance(item, dict) or not isinstance(item.get('key'), str):
            raise ValueError('入库明细格式无效')
        key = item['key']
        if key not in allowed or key in seen:
            raise ValueError('明细重复或不属于当前查询结果，请重新查询')
        seen.add(key)
        row = allowed[key]
        quantity = whole_number(item.get('quantity'), '实际数量' if mode == 'adjust' else '入库数量', 0 if mode == 'adjust' else 1)
        loc = whole_number(item.get('location_id'), '库位', 1)
        if not conn.execute('SELECT id FROM warehouse_locations WHERE id=? AND enabled=1', (loc,)).fetchone():
            raise ValueError('库位不存在或已停用，请重新选择')
        product = conn.execute('SELECT id,customer FROM manuals WHERE id=?', (row['manual_id'],)).fetchone()
        if not product:
            raise InventoryConflict('产品已删除，请重新查询')
        order_id = row['order_id'] if mode == 'inbound' else 0
        if order_id:
            order = conn.execute('SELECT * FROM product_orders WHERE id=?', (order_id,)).fetchone()
            if not order or order['manual_id'] != row['manual_id'] or order['customer'].strip() != row['customer'] or order['order_no'] != row['order_no']:
                raise InventoryConflict('订单信息已变化，请重新查询')
            if int(order['inventory_received_quantity'] or 0) != row['received'] or order['quantity'] != row['ordered_quantity']:
                raise InventoryConflict('订单入库进度已变化，请重新查询后核对')
            if row['received'] + quantity > MAX_QUANTITY:
                raise ValueError('累计订单入库数量过大')
            if row['received'] + quantity > row['ordered_quantity']:
                warnings.append(f"{row['drawing_no'] or row['product_name']} 超过订单剩余未入库数量")
        elif mode == 'inbound' and product['customer'].strip() != row['customer']:
            raise InventoryConflict('产品客户已变化，请重新查询')
        balance = conn.execute('SELECT quantity FROM inventory_balances WHERE manual_id=? AND location_id=?', (row['manual_id'],loc)).fetchone()
        current = int(balance['quantity']) if balance else 0
        if mode == 'adjust':
            target = (row['manual_id'], loc)
            if target in targets:
                raise ValueError('同一产品的同一库位不能重复调整')
            targets.add(target)
            if current != row['balances'].get(str(loc), 0):
                raise InventoryConflict('库存已发生变化，请重新查询后填写盘点数量')
            delta = quantity - current
        else:
            delta = quantity
        validated.append((row, loc, delta, current, quantity, order_id))
    if warnings and payload.get('confirm_over_receipt') is not True:
        raise InventoryConflict('；'.join(warnings) + '。是否仍要入库？', True)
    # Different orders may refer to the same product/location; validate their sum.
    totals = {}
    for row, loc, delta, current, quantity, order_id in validated:
        key = (row['manual_id'], loc)
        totals[key] = totals.get(key, current) + delta
        if not 0 <= totals[key] <= 9_223_372_036_854_775_807:
            raise ValueError('库存数量超出可保存范围')
    batch_no = ('RK' if mode == 'inbound' else 'TZ') + datetime.utcnow().strftime('%Y%m%d') + uuid.uuid4().hex[:10].upper()
    changed = 0
    for row, loc, delta, current, quantity, order_id in validated:
        if not delta:
            continue
        detail = f'批次 {batch_no}；' + (f'盘点 {current} → {quantity}；' if mode == 'adjust' else '') + remark
        transact(conn, 'adjust' if mode == 'adjust' else 'in', row['manual_id'], delta,
                 to_location_id=loc, related_order_type='库存调整' if mode == 'adjust' else '生产入库',
                 related_order_id=order_id or '', related_order_no=row['order_no'] if order_id else '',
                 customer=row['customer'], remark=detail)
        changed += 1
    conn.execute('INSERT INTO inventory_batch_receipts VALUES (?,?,?,?,?,?,?)',
                 (token_digest,request_digest,batch_no,mode,operator,changed,datetime.utcnow().isoformat(timespec='seconds')))
    return dict(batch_no=batch_no,row_count=changed,duplicate=False)
