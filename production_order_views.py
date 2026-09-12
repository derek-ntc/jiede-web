"""Read-only presentation groups; original order row IDs remain untouched."""


def order_group_key(row):
    row = dict(row)
    customer = str(row.get('customer') or '').strip() or row.get('product_customer', '')
    return (customer, row.get('order_no', ''), row.get('ordered_at', ''),
            row.get('assembly_drawing_no') or '', row.get('assembly_set_quantity') or 0)


def group_order_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(order_group_key(row), []).append(dict(row))
    results = []
    for key, members in grouped.items():
        dates = sorted({r.get('planned_ship_at') for r in members if r.get('planned_ship_at')})
        results.append(dict(anchor_id=min(r['id'] for r in members), rows=members,
            customer=key[0], order_no=key[1], ordered_at=key[2], assembly_drawing_no=key[3],
            assembly_set_quantity=key[4], line_count=len(members),
            products=' / '.join(dict.fromkeys(r.get('product_name', '') for r in members)),
            planned_ship_from=dates[0] if dates else '', planned_ship_to=dates[-1] if dates else '',
            completed=all(r.get('unshipped_quantity', r.get('quantity', 1)) <= 0 for r in members)))
    return results


def fetch_order_rows(conn):
    return conn.execute(f'''SELECT o.*, m.drawing_no, m.product_name, m.supplier,
        m.customer AS product_customer,
        COALESCE(i.total, 0) AS material_inventory_total,
        COALESCE(s.shipped_total, o.shipped_quantity, 0) AS shipped_quantity_total,
        COALESCE(s.last_shipped_at, o.shipped_at, '') AS shipped_at_display,
        o.quantity - COALESCE(s.shipped_total, o.shipped_quantity, 0) AS unshipped_quantity
        FROM product_orders o JOIN manuals m ON m.id=o.manual_id
        LEFT JOIN ({order_shipment_summary_subquery(include_assembly=True, conn=conn)}) s ON s.order_id=o.id
        LEFT JOIN (SELECT manual_id, SUM(quantity) total FROM inventory_balances GROUP BY manual_id) i
        ON i.manual_id=o.manual_id ORDER BY o.ordered_at DESC, o.id DESC''').fetchall()


def fetch_order_group(conn, anchor_id):
    rows = fetch_order_rows(conn)
    anchor = next((r for r in rows if r['id'] == anchor_id), None)
    if anchor is None:
        raise LookupError('订单明细不存在')
    return group_order_rows([r for r in rows if order_group_key(r) == order_group_key(anchor)])[0]


def matched_order_groups(rows, query='', customer=''):
    fields = ('order_no', 'assembly_drawing_no', 'customer', 'product_customer', 'supplier',
              'drawing_no', 'product_name', 'material_stock_status', 'remark', 'carton_status',
              'inventory_status', 'recent_ship_status')
    return [g for g in group_order_rows(rows)
            if (not customer or g['customer'] == customer)
            and (not query or any(query.casefold() in str(row.get(f) or '').casefold()
                                  for row in g['rows'] for f in fields))]


def sort_order_rows(rows, sort='ordered_at', direction='desc'):
    """Keep existing incomplete-first / completed-date ordering in detail views."""
    aliases = {'material_stock_status': 'material_inventory_total',
               'shipped_quantity': 'shipped_quantity_total', 'shipped_at': 'shipped_at_display'}
    field = aliases.get(sort, sort)
    numeric = {'quantity', 'assembly_set_quantity', 'unshipped_quantity',
               'material_inventory_total', 'shipped_quantity_total'}
    def value(row):
        if field == 'customer':
            return order_group_key(row)[0].casefold()
        raw = row.get(field)
        return int(raw or 0) if field in numeric else str(raw or '').casefold()
    items = [dict(r) for r in rows]
    items.sort(key=lambda r: (r.get('ordered_at', ''), r['id']), reverse=True)
    pending = [r for r in items if r['unshipped_quantity'] > 0]
    completed = [r for r in items if r['unshipped_quantity'] <= 0]
    pending.sort(key=value, reverse=direction != 'asc')
    completed.sort(key=lambda r: r.get('ordered_at', ''))
    return pending + completed


def order_shipment_summary_subquery(include_assembly=False, conn=None):
    """Shared existing shipment summary for both legacy rows and order groups."""
    if include_assembly and conn is not None:
        include_assembly = len(conn.execute('''SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('assembly_shipment_batches',
            'assembly_shipment_items', 'assembly_shipment_allocations')''').fetchall()) == 3
    source = 'product_order_shipments'
    if include_assembly:
        source = '''(
            SELECT order_id, shipped_quantity, shipped_at FROM product_order_shipments
            UNION ALL
            SELECT a.order_id, a.quantity AS shipped_quantity, b.shipped_at
            FROM assembly_shipment_allocations a
            JOIN assembly_shipment_items i ON i.id=a.item_id
            JOIN assembly_shipment_batches b ON b.id=i.batch_id
            WHERE a.order_id IS NOT NULL
        ) AS all_shipments'''
    return f'''SELECT order_id, SUM(shipped_quantity) AS shipped_total,
               MAX(shipped_at) AS last_shipped_at FROM {source} GROUP BY order_id'''
