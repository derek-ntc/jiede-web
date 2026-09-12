(function (root) {
  'use strict';
  function normalizeRows(rows) { return rows.map(row => ({...row})); }
  function addRow(rows) { return [...normalizeRows(rows), {}]; }
  function hasLegacySource(row) { return Boolean(row.legacy_source || row.legacy_id); }
  function removeRow(rows, index) {
    const result = normalizeRows(rows);
    if (index >= 0 && index < result.length && !hasLegacySource(result[index])) result.splice(index, 1);
    return result.length ? result : [{}];
  }
  function moveRow(rows, index, direction) {
    const result = normalizeRows(rows);
    const target = index + direction;
    if (index >= 0 && index < result.length && target >= 0 && target < result.length) {
      [result[index], result[target]] = [result[target], result[index]];
    }
    return result;
  }
  function fieldName(index, field) { return `items[${index}][${field}]`; }
  function supplierForSelection(id, suppliers, saved) {
    if (saved && String(saved.id) === String(id)) return {...saved};
    return {...(suppliers.find(supplier => String(supplier.id) === String(id)) || {})};
  }
  function lineAmount(price, quantity) {
    price = String(price || '').trim();
    quantity = String(quantity || '').trim();
    if (!price) return '未录价';
    if (!/^\d+(?:\.\d{1,2})?$/.test(price) || !/^[1-9]\d*$/.test(quantity)) return '待校验';
    const [whole, fraction = ''] = price.split('.');
    const amount = (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))) * BigInt(quantity);
    return `¥${amount / 100n}.${String(amount % 100n).padStart(2, '0')}`;
  }
  function mount(form) {
    const body = form.querySelector('[data-purchase-rows]');
    const prototype = body.querySelector('[data-purchase-row]').cloneNode(true);
    function updateAmount(row) {
      const output = row.querySelector('[data-line-amount]');
      if (output) output.textContent = lineAmount(row.querySelector('[data-field="unit_price"]').value, row.querySelector('[data-field="quantity"]').value);
    }
    body.querySelectorAll('[data-purchase-row]').forEach(updateAmount);
    body.addEventListener('input', event => {
      const row = event.target.closest('[data-purchase-row]');
      if (row) updateAmount(row);
    });
    function read() {
      return Array.from(body.children, row => ({
        ...Object.fromEntries(Array.from(row.querySelectorAll('[data-field]'), input => [input.dataset.field, input.value])),
        legacy_source: row.dataset.legacySource || '', legacy_id: row.dataset.legacyId || ''
      }));
    }
    function render(rows) {
      body.replaceChildren(...rows.map((row, index) => {
        const element = prototype.cloneNode(true);
        element.querySelectorAll('[data-field]').forEach(input => {
          input.name = fieldName(index, input.dataset.field);
          input.value = row[input.dataset.field] ?? '';
        });
        element.dataset.legacySource = row.legacy_source || '';
        element.dataset.legacyId = row.legacy_id || '';
        element.querySelector('[data-row-action="remove"]').disabled = hasLegacySource(row);
        element.querySelector('[data-legacy-note]').hidden = !hasLegacySource(row);
        updateAmount(element);
        return element;
      }));
    }
    form.querySelector('[data-add-row]').addEventListener('click', () => render(addRow(read())));
    body.addEventListener('click', event => {
      const button = event.target.closest('[data-row-action]');
      if (!button) return;
      const index = Array.from(body.children).indexOf(button.closest('[data-purchase-row]'));
      const action = button.dataset.rowAction;
      render(action === 'remove' ? removeRow(read(), index) : moveRow(read(), index, action === 'up' ? -1 : 1));
    });
    form.querySelector('[data-delivery-profile]').addEventListener('change', event => {
      const option = event.target.selectedOptions[0];
      if (!option.value) return;
      for (const [field, key] of Object.entries({delivery_address: 'address', recipient: 'recipient', recipient_phone: 'phone', remark: 'remark'})) {
        form.elements.namedItem(field).value = option.dataset[key] || '';
      }
    });
    const supplierData = JSON.parse(form.querySelector('[data-supplier-data]').textContent);
    const supplierSelect = form.elements.namedItem('supplier_id');
    function updateSupplier() {
      const supplier = supplierForSelection(supplierSelect.value, supplierData.suppliers, supplierData.saved);
      form.querySelectorAll('[data-supplier-field]').forEach(element => {
        element.textContent = supplier[element.dataset.supplierField] || '';
      });
    }
    supplierSelect.addEventListener('change', updateSupplier);
    updateSupplier();
  }
  const api = {normalizeRows, addRow, removeRow, moveRow, fieldName, supplierForSelection, lineAmount, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PurchaseOrders = api;
  if (typeof document !== 'undefined') document.querySelectorAll('[data-purchase-form]').forEach(mount);
})(typeof globalThis !== 'undefined' ? globalThis : this);
