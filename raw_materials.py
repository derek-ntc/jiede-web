"""Raw-material shape semantics shared by procurement and inventory.

Length is plate length or optional tube cut length. Width is plate width,
first rectangular side or outside diameter; height is the second tube side.
Untyped historical records retain their original dimensions.
"""
from decimal import Decimal

MATERIAL_TYPES = {'plate': '板', 'square_tube': '方管', 'round_tube': '圆管'}


def _number(value):
    if value in (None, ''):
        return '—'
    return format(Decimal(str(value)).normalize(), 'f')


def material_specification(item):
    item = dict(item)
    kind = item.get('material_type', '')
    if not kind:
        parts = [item.get('spec') or item.get('dimension_text') or '']
        parts.extend(f'{label} mm：{_number(item[key])}' for key, label in
                     (('length', '长'), ('width', '宽'), ('thickness', '厚度'))
                     if item.get(key) not in (None, ''))
        return '／'.join(part for part in parts if part)
    fields = {'plate': ('length', 'width', 'thickness'),
              'square_tube': ('width', 'height', 'thickness'),
              'round_tube': ('width', 'thickness')}[kind]
    text = MATERIAL_TYPES[kind] + ' ' + ('Φ' if kind == 'round_tube' else '')
    text += '×'.join(_number(item.get(k)) for k in fields) + ' mm'
    if kind != 'plate' and item.get('length') is not None:
        text += '；定尺 ' + _number(item['length']) + ' mm'
    return text


def normalize_material_dimensions(row):
    kind = row.get('material_type', '')
    if not kind:
        return
    if kind not in MATERIAL_TYPES:
        raise ValueError('请选择有效的材料类型')
    required = {'plate': ('length', 'width', 'thickness'),
                'square_tube': ('width', 'height', 'thickness'),
                'round_tube': ('width', 'thickness')}[kind]
    if any(row.get(k) is None for k in required):
        raise ValueError(f'请填写{MATERIAL_TYPES[kind]}的完整规格尺寸')
    if kind != 'square_tube':
        row['height'] = None
    if kind != 'plate':
        limit = min(row['width'], row['height']) if kind == 'square_tube' else row['width']
        if row['thickness'] * 2 >= limit:
            raise ValueError('管材壁厚必须小于外径或较短边的一半')
    row['spec'] = material_specification(row)
