const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modulePath = path.join(__dirname, '../../static/purchase-orders.js');

test('browser row editor module is available', () => assert.ok(fs.existsSync(modulePath)));
const api = fs.existsSync(modulePath) ? require(modulePath) : {};
test('category setup preserves common values and stable ids without mutating input', () => {
  const source = [{id: '42', item_name: '螺栓', quantity: '10', spec: 'M8'}];
  const rows = api.normalizeRows(source, 'other');
  assert.equal(rows[0].item_name, '螺栓');
  assert.equal(rows[0].quantity, '10');
  assert.equal(rows[0].id, '42');
  rows[0].item_name = 'changed';
  assert.equal(source[0].item_name, '螺栓');
});
test('add remove and reorder retain stable ids and isolate new rows', () => {
  let rows = api.addRow([{id: '1', item_name: 'A'}, {id: '2', item_name: 'B'}]);
  assert.equal(rows.length, 3);
  assert.ok(!rows[2].id);
  rows = api.moveRow(rows, 1, -1);
  assert.deepEqual(rows.map(r => r.id || ''), ['2', '1', '']);
  rows = api.removeRow(rows, 1);
  assert.deepEqual(rows.map(r => r.id || ''), ['2', '']);
});
test('field names reindex and boundary moves are harmless', () => {
  assert.equal(api.fieldName(2, 'quantity'), 'items[2][quantity]');
  assert.deepEqual(api.moveRow([{id: '1'}], 0, -1), [{id: '1'}]);
  assert.equal(api.removeRow([{id: '1'}], 0).length, 1);
});
test('legacy provenance rows cannot be removed and newly added rows remain removable', () => {
  const legacy = {id: '1', legacy_source: 'carton_purchases', legacy_id: '9', item_name: '来源', dimension_text: '异形尺寸 40×30×20'};
  assert.deepEqual(api.removeRow([legacy], 0), [legacy]);
  const rows = api.addRow([legacy]);
  assert.deepEqual(api.removeRow(rows, 1), [legacy]);
  assert.deepEqual(api.moveRow(rows, 0, 1), [{}, legacy]);
  assert.deepEqual(rows[1], {});
});
test('supplier selection clears missing values and switching back restores the saved snapshot', () => {
  const saved = {id: 1, name: '旧供方', phone: '00123', address: '旧地址'};
  const suppliers = [{id: 1, name: '已更新', phone: '00999'}, {id: 2, name: '乙方'}];
  assert.deepEqual(api.supplierForSelection('1', suppliers, saved), saved);
  assert.deepEqual(api.supplierForSelection('2', suppliers, saved), suppliers[1]);
  assert.deepEqual(api.supplierForSelection('', suppliers, saved), {});
  assert.deepEqual(api.supplierForSelection('1', suppliers, saved), saved);
  assert.deepEqual(api.supplierForSelection('1', suppliers, null), suppliers[0]);
});
test('editable amount preview preserves cents, missing prices, zero and large integers', () => {
  assert.equal(api.lineAmount('0.29', '3'), '¥0.87');
  assert.equal(api.lineAmount('', '3'), '未录价');
  assert.equal(api.lineAmount('0', '3'), '¥0.00');
  assert.equal(api.lineAmount('42949672.98', '2147483647'), '¥92233720368547758.06');
  for (const [price, qty] of [['1.001', '3'], ['-1', '2'], ['2', '1.5']]) assert.equal(api.lineAmount(price, qty), '待校验');
});
test('mounted supplier change events update all visible fields and preserve delivery edits', () => {
  // A minimal form surface: native EventTargets exercise the actual mount wiring,
  // while read-only display nodes expose the user-visible values it writes.
  const supplier = Object.assign(new EventTarget(), {value: '1'});
  const body = Object.assign(new EventTarget(), {
    querySelector: () => ({cloneNode() { return this; }}), querySelectorAll: () => []
  });
  const displays = ['name', 'code', 'contact', 'phone', 'email', 'address'].map(key => ({dataset: {supplierField: key}, textContent: ''}));
  const fields = {supplier_id: supplier, delivery_address: {value: '本单手改地址'}};
  const nodes = {
    '[data-purchase-rows]': body, '[data-add-row]': new EventTarget(),
    '[data-delivery-profile]': new EventTarget(),
    '[data-supplier-data]': {textContent: JSON.stringify({saved: {id: 1, name: '历史供方', code: '0001', contact: '旧联系人', phone: '00123', email: 'old@example.invalid', address: '旧地址'}, suppliers: [{id: 1, name: '档案已改'}, {id: 2, name: '<b>乙方</b>', code: '0002'}]})}
  };
  api.mount({querySelector: selector => nodes[selector], querySelectorAll: () => displays, elements: {namedItem: name => fields[name]}});
  assert.deepEqual(displays.map(node => node.textContent), ['历史供方', '0001', '旧联系人', '00123', 'old@example.invalid', '旧地址']);
  supplier.value = '2';
  supplier.dispatchEvent(new Event('change'));
  assert.deepEqual(displays.map(node => node.textContent), ['<b>乙方</b>', '0002', '', '', '', '']);
  assert.equal(fields.delivery_address.value, '本单手改地址');
  supplier.value = '1';
  supplier.dispatchEvent(new Event('change'));
  assert.equal(displays[0].textContent, '历史供方');
  assert.equal(displays[3].textContent, '00123');
  supplier.value = '';
  supplier.dispatchEvent(new Event('change'));
  assert.deepEqual(displays.map(node => node.textContent), ['', '', '', '', '', '']);
});
