"""Pure domain rules for assembly-product shipping."""

import hashlib
import json
from itertools import zip_longest


def parse_positive_int(value, label):
    text = str(value or "").strip()
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须为大于 0 的整数")
    if parsed <= 0 or str(parsed) != text:
        raise ValueError(f"{label}必须为大于 0 的整数")
    return parsed


def normalize_component_rows(drawing_numbers, quantities):
    result = []
    seen = set()
    for drawing, quantity in zip_longest(drawing_numbers, quantities, fillvalue=""):
        drawing = str(drawing or "").strip()
        quantity = str(quantity or "").strip()
        if not drawing and not quantity:
            continue
        if not drawing or not quantity:
            raise ValueError("组装件图号和每套用量必须同时填写")
        key = drawing.casefold()
        if key in seen:
            raise ValueError(f"组装件图号 {drawing} 不能重复")
        seen.add(key)
        result.append(
            {
                "assembly_drawing_no": drawing,
                "quantity_per_set": parse_positive_int(quantity, "每套用量"),
                "sort_order": len(result),
            }
        )
    return result


def expand_components(components, set_quantity, overrides=None):
    set_quantity = parse_positive_int(set_quantity, "组装件套数")
    overrides = overrides or {}
    expanded = []
    for component in components:
        item = dict(component)
        calculated = (
            0 if item.get("source_kind") == "extra" else
            set_quantity * parse_positive_int(component["quantity_per_set"], "每套用量")
        )
        override = overrides.get(int(component["manual_id"]))
        item["calculated_quantity"] = calculated
        item["shipped_quantity"] = (
            parse_positive_int(override, "配件实际发货数量")
            if override is not None
            else parse_positive_int(calculated, "配件实际发货数量")
        )
        expanded.append(item)
    return expanded


def allocate_quantity(quantity, orders):
    remaining = parse_positive_int(quantity, "配件实际发货数量")
    allocations = []
    for order in orders:
        available = max(0, int(order["unshipped_quantity"] or 0))
        used = min(remaining, available)
        if used:
            allocations.append({"order_id": int(order["id"]), "quantity": used})
            remaining -= used
        if remaining == 0:
            break
    if remaining:
        allocations.append({"order_id": None, "quantity": remaining})
    return allocations


def inventory_result(requested_quantity, available_quantity):
    requested = parse_positive_int(requested_quantity, "配件实际发货数量")
    available = max(0, int(available_quantity or 0))
    deducted = min(requested, available)
    return {
        "requested_quantity": requested,
        "deducted_quantity": deducted,
        "shortage_quantity": requested - deducted,
    }


def preview_token(preview):
    canonical_preview = dict(preview)
    canonical_preview.pop("preview_token", None)
    canonical = json.dumps(
        canonical_preview, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
