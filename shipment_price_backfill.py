"""Explicit, optimistic historical price backfill; never owns a transaction."""
import hashlib
import json
from datetime import datetime, timezone

from pricing import format_money_minor, line_total_minor, normalize_currency, parse_money_minor


SOURCE_TABLES = {"ordinary": "product_order_shipments", "assembly_item": "assembly_shipment_items"}


def normalize_sources(sources):
    refs = []
    for source_type, source_id in sources:
        if source_type not in SOURCE_TABLES or type(source_id) is not int or source_id <= 0:
            raise ValueError("发货记录来源无效")
        refs.append((source_type, source_id))
    if len(set(refs)) != len(refs):
        raise ValueError("不能重复选择同一发货记录")
    return sorted(refs)


def _source_state(conn, source_type, source_id):
    row = conn.execute(f"SELECT * FROM {SOURCE_TABLES[source_type]} WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return {"source": None}
    source = dict(row)
    if source_type == "ordinary":
        parent = conn.execute("SELECT * FROM product_orders WHERE id = ?", (source["order_id"],)).fetchone()
        manual_id = parent["manual_id"] if parent else None
    else:
        parent = conn.execute("SELECT * FROM assembly_shipment_batches WHERE id = ?", (source["batch_id"],)).fetchone()
        manual_id = source["manual_id"]
    product = conn.execute(
        "SELECT id, product_name, drawing_no, unit_price_minor, currency FROM manuals WHERE id = ?", (manual_id,),
    ).fetchone()
    claim_key = f"{source_type}:{source_id}"
    # Same effective active_claim_key contract used by the authoritative app assertions.
    claims = {}
    for kind, table in (("finance", "finance_invoice_items"), ("reconciliation", "reconciliation_statement_items")):
        claims[kind] = [dict(claim) for claim in conn.execute(
            f"SELECT * FROM {table} WHERE active_claim_key = ?", (claim_key,),
        ).fetchall()]
    return {"source": source, "parent": dict(parent) if parent else None,
            "product": dict(product) if product else None, "claims": claims}


def build_plan(conn, sources, operator):
    """Snapshot every selected current source, including skipped rows, without writes."""
    refs = normalize_sources(sources)
    eligible, skipped, states, totals = [], [], [], {}
    for source_type, source_id in refs:
        state = _source_state(conn, source_type, source_id)
        states.append([source_type, source_id, state])
        source, product = state["source"], state.get("product")
        item = {"source_type": source_type, "source_id": source_id,
                "product_name": (product or {}).get("product_name", ""),
                "drawing_no": (product or {}).get("drawing_no", "")}
        reason = ""
        if source is None or state.get("parent") is None:
            reason = "发货记录不存在"
        elif state["claims"]["finance"]:
            reason = "已被开票单占用"
        elif state["claims"]["reconciliation"]:
            reason = "已被对账单占用"
        elif source["unit_price_minor"] is not None:
            reason = "已记录价格（含零价格），不覆盖"
        elif product is None:
            reason = "产品不存在"
        elif product["unit_price_minor"] is None:
            reason = "产品未定价"
        else:
            try:
                currency = normalize_currency(product["currency"])
                price = product["unit_price_minor"]
                if type(price) is not int:
                    raise ValueError("产品价格无效")
                price = parse_money_minor(format_money_minor(price, currency), currency)
                quantity = source["shipped_quantity"]
                if type(quantity) is not int or quantity < 0:
                    raise ValueError("发货数量无效")
                total = line_total_minor(price, quantity)
            except (ValueError, TypeError, OverflowError):
                reason = "产品价格无效或币种/数量无效"
            else:
                item.update(manual_id=product["id"], unit_price_minor=price, currency=currency,
                            quantity=quantity, line_total_minor=total)
                eligible.append(item)
                totals[currency] = totals.get(currency, 0) + total
        if reason:
            item["reason"] = reason
            skipped.append(item)
    digest = hashlib.sha256(json.dumps(
        {"operator": operator, "states": states}, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {"sources": refs, "operator": operator, "digest": digest,
            "eligible": eligible, "skipped": skipped, "totals": totals}


def apply_plan(conn, plan, operator, assert_mutable):
    """Validate under caller's write lock, then apply all NULL-only writes atomically.

    The callback must enforce both finance and reconciliation source mutability.
    Exceptions must escape the caller's transaction so its rollback remains authoritative.
    A successful receipt makes replay a no-op without touching audit timestamps.
    """
    if not conn.in_transaction:
        raise ValueError("批量补价必须在调用方写事务内执行")
    if plan["operator"] != operator:
        raise ValueError("预览操作人不匹配")
    if conn.execute("SELECT 1 FROM shipment_price_backfill_runs WHERE digest = ? AND operator = ?",
                    (plan["digest"], operator)).fetchone():
        return {"updated_count": 0}
    fresh = build_plan(conn, plan["sources"], operator)
    if fresh["digest"] != plan["digest"]:
        raise ValueError("价格或发货状态已变化，请重新预览")
    refs = [(item["source_type"], item["source_id"]) for item in fresh["eligible"]]
    assert_mutable(conn, refs)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    for item in fresh["eligible"]:
        cursor = conn.execute(
            f"""UPDATE {SOURCE_TABLES[item['source_type']]}
                SET unit_price_minor = ?, currency = ?, price_recorded_by = ?, price_recorded_at = ?
                WHERE id = ? AND unit_price_minor IS NULL""",
            (item["unit_price_minor"], item["currency"], operator, now, item["source_id"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("发货价格已变化，请重新预览")
        conn.execute(
            """INSERT INTO shipment_price_backfill_audit
               (digest, source_type, source_id, manual_id, unit_price_minor, currency,
                operator, recorded_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '按产品现价补齐')""",
            (plan["digest"], item["source_type"], item["source_id"], item["manual_id"],
             item["unit_price_minor"], item["currency"], operator, now),
        )
    conn.execute("INSERT INTO shipment_price_backfill_runs (digest, operator, recorded_at) VALUES (?, ?, ?)",
                 (plan["digest"], operator, now))
    return {"updated_count": len(fresh["eligible"])}
