"""Order-linked process quantities. Never writes inventory or shipment data."""
import re

from production_processes import (
    _savepoint, _project_legacy_completion, create_followup_process_snapshot,
    load_followup_process_card,
)


def ensure_order_production_schema(conn):
    for table, column, definition in [
        ('production_followups', 'order_id', 'INTEGER REFERENCES product_orders(id)'),
        ('production_followup_process_steps', 'completed_quantity', 'INTEGER NOT NULL DEFAULT 0 CHECK(completed_quantity >= 0)'),
        ('production_followup_process_steps', 'version', 'INTEGER NOT NULL DEFAULT 0'),
    ]:
        if column not in {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_followup_order
                    ON production_followups(order_id) WHERE order_id IS NOT NULL''')
    # Deliberately no cascading references: audit survives confirmed step removal.
    conn.execute('''CREATE TABLE IF NOT EXISTS order_production_quantity_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, followup_id INTEGER NOT NULL,
        order_id INTEGER NOT NULL, step_id INTEGER NOT NULL, step_name TEXT NOT NULL,
        before_quantity INTEGER NOT NULL, after_quantity INTEGER NOT NULL,
        operator TEXT NOT NULL, created_at TEXT NOT NULL, event TEXT NOT NULL)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_order_production_audit_card
                    ON order_production_quantity_audit(followup_id, id)''')


def start_order_followup(conn, order_id, operator, now):
    with _savepoint(conn, 'start_order_followup'):
        existing = conn.execute('SELECT id FROM production_followups WHERE order_id=?', (order_id,)).fetchone()
        if existing:
            return existing['id']
        order = conn.execute('''SELECT o.*, m.drawing_no, m.product_name,
            COALESCE(NULLIF(TRIM(o.customer), ''), m.customer) AS effective_customer
            FROM product_orders o JOIN manuals m ON m.id=o.manual_id WHERE o.id=?''', (order_id,)).fetchone()
        if not order:
            raise ValueError('订单明细不存在')
        batch_no = f'ORDER-{order_id}'
        suffix = 1
        while conn.execute('SELECT 1 FROM production_followups WHERE batch_no=?', (batch_no,)).fetchone():
            batch_no = f'ORDER-{order_id}-{suffix}'
            suffix += 1
        card = conn.execute('''INSERT INTO production_followups
            (order_id, manual_id, batch_no, customer, ordered_at, drawing_no, product_name,
             created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (order_id, order['manual_id'], batch_no, order['effective_customer'],
             order['ordered_at'], order['drawing_no'], order['product_name'], operator, now, now)).lastrowid
        create_followup_process_snapshot(conn, card, order['manual_id'], now=now)
        return card


def linked_order(conn, followup_id):
    return conn.execute('''SELECT o.* FROM product_orders o
        JOIN production_followups f ON f.order_id=o.id WHERE f.id=?''', (followup_id,)).fetchone()


def _integer(value, label):
    if not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(f'{label}必须为非负整数')
    number = int(value)
    if number > 9223372036854775807:
        raise ValueError(f'{label}超出有效范围，请刷新页面')
    return number


def record_quantity_event(conn, followup_id, order_id, step, quantity, operator, now, event):
    conn.execute('''INSERT INTO order_production_quantity_audit
        (followup_id, order_id, step_id, step_name, before_quantity, after_quantity,
         operator, created_at, event) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (followup_id, order_id, step['id'], step['name'], step['completed_quantity'],
         quantity, operator, now, event))


def set_process_quantity(conn, followup_id, step_id, quantity, expected_version, operator, now):
    quantity = _integer(quantity, '累计完成数量')
    version = _integer(expected_version, '版本（请刷新页面）')
    if not str(operator or '').strip():
        raise ValueError('生产工艺操作人不能为空')
    with _savepoint(conn, 'set_process_quantity'):
        order = linked_order(conn, followup_id)
        if order is None:
            raise ValueError('订单工艺卡不存在')
        step = conn.execute('''SELECT * FROM production_followup_process_steps
            WHERE id=? AND followup_id=?''', (step_id, followup_id)).fetchone()
        if step is None:
            raise ValueError('工艺不属于当前订单工艺卡')
        if quantity > order['quantity']:
            raise ValueError(f"累计完成数量不能超过订单需求 {order['quantity']}")
        completed = quantity == order['quantity'] and quantity > 0
        completed_at = (step['completed_at'] or now) if completed else ''
        completed_by = (step['completed_by'] or operator) if completed else ''
        updated = conn.execute('''UPDATE production_followup_process_steps
            SET completed_quantity=?, version=version+1, completed_at=?, completed_by=?, updated_at=?
            WHERE id=? AND followup_id=? AND version=?''',
            (quantity, completed_at, completed_by, now, step_id, followup_id, version))
        if updated.rowcount != 1:
            raise ValueError('进度已被更新，请刷新页面后重试')
        if quantity != step['completed_quantity']:
            record_quantity_event(conn, followup_id, order['id'], step, quantity, operator, now, 'quantity')
        _project_legacy_completion(conn, followup_id, step['name'], completed_at, now)
    return load_followup_process_card(conn, followup_id)


def validate_order_production_change(conn, order_id, manual_id, quantity):
    card = conn.execute('SELECT id, manual_id FROM production_followups WHERE order_id=?', (order_id,)).fetchone()
    if card is None:
        return
    if int(manual_id) != card['manual_id']:
        raise ValueError('已关联订单工艺卡，不能更换产品')
    maximum = conn.execute('''SELECT COALESCE(MAX(completed_quantity), 0)
        FROM production_followup_process_steps WHERE followup_id=?''', (card['id'],)).fetchone()[0]
    if int(quantity) < maximum:
        raise ValueError(f'订单数量不能小于工艺已完成数量 {maximum}')


def refresh_order_production_demand(conn, order_id, previous_quantity, now, operator):
    card = conn.execute('SELECT id FROM production_followups WHERE order_id=?', (order_id,)).fetchone()
    if not card:
        return
    order = linked_order(conn, card['id'])
    conn.execute('''UPDATE production_followups SET customer=COALESCE(NULLIF(TRIM(?), ''),
        (SELECT customer FROM manuals WHERE id=?)), ordered_at=?, updated_at=? WHERE id=?''',
        (order['customer'], order['manual_id'], order['ordered_at'], now, card['id']))
    if previous_quantity == order['quantity']:
        return
    for step in load_followup_process_card(conn, card['id']):
        completed = step['completed_quantity'] == order['quantity'] and order['quantity'] > 0
        stamp = (step['completed_at'] or now) if completed else ''
        conn.execute('''UPDATE production_followup_process_steps SET version=version+1,
            completed_at=?, completed_by=?, updated_at=? WHERE id=?''',
            (stamp, (step['completed_by'] or operator) if completed else '', now, step['id']))
        _project_legacy_completion(conn, card['id'], step['name'], stamp, now)


def validate_order_production_delete(conn, order_id):
    if conn.execute('SELECT 1 FROM production_followups WHERE order_id=?', (order_id,)).fetchone():
        raise ValueError('已关联订单工艺卡，不能删除订单；无数量历史的工艺卡可确认删除后解除关联')


def validate_followup_delete(conn, followup_id, confirmed=False):
    if not linked_order(conn, followup_id):
        return
    if conn.execute('SELECT 1 FROM order_production_quantity_audit WHERE followup_id=? LIMIT 1', (followup_id,)).fetchone():
        raise ValueError('订单工艺卡已有数量变更历史，不能删除（归零也不能解除）')
    if not confirmed:
        raise ValueError('请确认删除未记录完成数量的订单工艺卡并解除关联')


def decorate_order_processes(conn, order):
    item = dict(order)
    card = conn.execute('SELECT * FROM production_followups WHERE order_id=?', (order['id'],)).fetchone()
    item['followup'] = dict(card) if card else None
    item['processes'] = load_followup_process_card(conn, card['id']) if card else []
    demand = order['quantity']
    for step in item['processes']:
        step['percent'] = round(step['completed_quantity'] * 100 / demand, 1) if demand else 0
        step['status'] = '已完成' if step['completed_quantity'] == demand and demand else ('进行中' if step['completed_quantity'] else '未开始')
    item['progress'] = round(sum(s['completed_quantity'] for s in item['processes']) * 100 / (demand * len(item['processes'])), 1) if demand and item['processes'] else None
    return item
