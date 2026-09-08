"""Schema and field helpers shared by the shipping workflow."""


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
