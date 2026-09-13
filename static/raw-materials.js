(function (root) {
  'use strict';
  const types = {
    plate: {label: '板', dimensions: {length: '长度', width: '宽度', thickness: '厚度'}},
    square_tube: {label: '方管', dimensions: {width: '边长 A', height: '边长 B', thickness: '壁厚', length: '定尺长度（选填）'}},
    round_tube: {label: '圆管', dimensions: {width: '外径', thickness: '壁厚', length: '定尺长度（选填）'}},
  };
  function specification(type, values) {
    const config = types[type];
    if (!config) return '';
    const fields = Object.keys(config.dimensions).filter(k => type === 'plate' || k !== 'length');
    const number = value => value && Number.isFinite(Number(value)) ? String(Number(value)) : '—';
    let text = `${config.label} ${type === 'round_tube' ? 'Φ' : ''}${fields.map(k => number(values[k])).join('×')} mm`;
    if (type !== 'plate' && values.length) text += `；定尺 ${number(values.length)} mm`;
    return text;
  }
  function syncRow(row, changedType = false) {
    const select = row.querySelector('[data-material-type]');
    if (!select) return;
    const type = select.value;
    const config = types[type] || types.plate;
    const values = {};
    row.querySelectorAll('[data-material-dimension]').forEach(group => {
      const key = group.dataset.materialDimension;
      const input = group.querySelector('input');
      const label = config.dimensions[key];
      group.hidden = !label;
      input.disabled = !label;
      input.required = Boolean(type && label && !(type !== 'plate' && key === 'length'));
      if (changedType && (!label || key === 'length')) input.value = '';
      group.querySelector('[data-dimension-label]').textContent = label || '';
      input.setAttribute('aria-label', label || key);
      values[key] = input.value;
    });
    const dimensions = row.querySelector('[data-material-dimensions]');
    Object.keys(config.dimensions).forEach(key => dimensions.appendChild(row.querySelector(`[data-material-dimension="${key}"]`)));
    row.querySelector('[data-material-preview]').textContent = specification(type, values);
    const spec = row.querySelector('[data-field="spec"]');
    if (type && spec) spec.value = specification(type, values);
  }
  function mount(rootNode) {
    rootNode.querySelectorAll('[data-material-row]').forEach(row => syncRow(row));
    rootNode.addEventListener('change', event => {
      if (event.target.matches('[data-material-type]')) syncRow(event.target.closest('[data-material-row]'), true);
    });
    rootNode.addEventListener('input', event => {
      const row = event.target.closest('[data-material-row]');
      if (row && event.target.closest('[data-material-dimensions]')) syncRow(row);
    });
  }
  const api = {types, specification, syncRow, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RawMaterials = api;
  if (typeof document !== 'undefined') mount(document);
})(typeof globalThis !== 'undefined' ? globalThis : this);
