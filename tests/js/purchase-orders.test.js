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
