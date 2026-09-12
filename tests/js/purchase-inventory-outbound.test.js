const test = require('node:test');
const assert = require('node:assert/strict');
let api;
try { api = require('../../static/purchase-inventory-outbound.js'); } catch (_) { api = {}; }
const state = () => ({usedBy: '王师傅', outboundAt: '2026-09-12', remark: '领用', rows: [
  {selected: true, lotId: 1, quantity: '2', expectedVersion: 1, availableQuantity: 10, remark: '机架'},
  {selected: false, lotId: 2, quantity: 'invalid', expectedVersion: 1, availableQuantity: 1, remark: '未选'}]});

test('selected rows require a recipient, real date and positive whole available quantity', () => {
  assert.equal(typeof api.validateOutbound, 'function');
  assert.equal(api.validateOutbound(state()).valid, true);
  for (const changes of [{usedBy: ''}, {outboundAt: ''}, {outboundAt: '2026-02-30'}, {rows: []}]) {
    assert.equal(api.validateOutbound({...state(), ...changes}).valid, false);
  }
  for (const quantity of ['0', '-1', '1.5', '11', '1e1']) {
    const s = state(); s.rows[0].quantity = quantity;
    assert.equal(api.validateOutbound(s).valid, false);
  }
});
test('payload contains only checked lots and never client operator or price', () => {
  assert.equal(typeof api.buildPayload, 'function');
  const result = api.buildPayload(state(), {category: 'raw_material', csrf_token: 'csrf', idempotency_key: 'key'});
  assert.deepEqual(result.rows, [{lot_id: 1, quantity: 2, expected_version: 1, remark: '机架'}]);
  assert.equal(result.used_by, '王师傅');
  assert.equal(result.operator, undefined);
});
test('conflict refresh retains edits and checks but updates availability and version', () => {
  assert.equal(typeof api.refreshCandidates, 'function');
  const original = state();
  const result = api.refreshCandidates(original, [{id: 1, available_quantity: 1, version: 3}]);
  assert.equal(result.usedBy, '王师傅');
  assert.equal(result.rows[0].quantity, '2');
  assert.equal(result.rows[0].remark, '机架');
  assert.equal(result.rows[0].selected, true);
  assert.equal(result.rows[0].availableQuantity, 1);
  assert.equal(result.rows[0].expectedVersion, 3);
  assert.equal(result.rows[1].availableQuantity, 0);
  assert.equal(api.validateOutbound(result).valid, false);
  assert.equal(original.rows[0].availableQuantity, 10);
});
test('disappeared lot remains visible but cannot be submitted', () => {
  assert.equal(typeof api.refreshCandidates, 'function');
  const result = api.refreshCandidates(state(), []);
  assert.equal(result.rows[0].selected, true);
  assert.equal(result.rows[0].unavailable, true);
  assert.equal(api.validateOutbound(result).valid, false);
});

function mount(response) {
  const fs = require('node:fs');
  const vm = require('node:vm');
  const controls = {selected: {checked: true}, quantity: {value: '2'}, remark: {value: '机架'}};
  const quantityText = {textContent: '10'};
  const el = {dataset: {lotId: '1', version: '1', available: '10'}, querySelector(selector) {
    if (selector === '[data-current-quantity]') return quantityText;
    return controls[selector.match(/data-field="(.*?)"/)[1]];
  }};
  const message = {textContent: ''};
  const button = {disabled: false};
  const existingLink = {hidden: true};
  let handler, sent, destination;
  const requests = [];
  const data = {category: 'raw_material', csrf_token: 'csrf', idempotency_key: 'same-key'};
  const form = {elements: {used_by: {value: '王师傅'}, outbound_at: {value: '2026-09-12'}, remark: {value: '备注'}},
    querySelectorAll(selector) { return selector === '[data-outbound-row]' ? [el] : Object.values(controls); },
    querySelector(selector) {
      if (selector === '[data-outbound-message]') return message;
      if (selector === '[data-existing-outbound]') return existingLink;
      return button;
    },
    addEventListener(event, callback) { handler = callback; }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../static/purchase-inventory-outbound.js'), 'utf8'), {
    document: {querySelector(selector) { return selector === '[data-outbound-form]' ? form : {textContent: JSON.stringify(data)}; }},
    window: {location: {href: '/new', assign(url) { destination = url; }}},
    crypto: {getRandomValues(array) { return array.fill(7); }},
    fetch: async (_, options) => {
      sent = JSON.parse(options.body); requests.push(sent);
      const result = typeof response === 'function' ? await response(sent, requests.length) : response;
      if (result instanceof Error) throw result;
      return result;
    }
  });
  return {form, controls, el, quantityText, message, button, existingLink, requests,
    submit: () => handler({preventDefault() {}}), sent: () => sent, destination: () => destination};
}
test('mounted form validates only selected lots without native invalid unchecked fields blocking submit', () => {
  const ui = mount({});
  assert.equal(ui.form.noValidate, true);
});
test('mounted conflict preserves inputs and refreshes version using non-secure-context crypto', async () => {
  const ui = mount({status: 409, ok: false, json: async () => ({error: '库存已变更', conflict_code: 'revision_safe', candidates: [{id: 1, version: 2, available_quantity: 5}]})});
  await ui.submit();
  assert.equal(ui.controls.quantity.value, '2');
  assert.equal(ui.controls.remark.value, '机架');
  assert.equal(ui.controls.selected.checked, true);
  assert.equal(ui.quantityText.textContent, '5');
  assert.match(ui.message.textContent, /已更新当前可用数量/);
  await ui.submit();
  assert.equal(ui.sent().rows[0].expected_version, 2);
  assert.notEqual(ui.sent().idempotency_key, 'same-key');
  assert.equal(ui.button.disabled, false);
});
test('lost success response then edits retries the exact pending command and redirects to the original', async () => {
  const ui = mount((sent, count) => count === 1 ? new Error('lost response') :
    {ok: true, json: async () => ({duplicate: true, redirect_url: '/records/1'})});
  await ui.submit();
  ui.controls.quantity.value = '4';
  ui.controls.remark.value = 'edited row';
  ui.form.elements.used_by.value = 'edited recipient';
  ui.form.elements.outbound_at.value = '';
  ui.form.elements.remark.value = 'edited header';
  await ui.submit();
  assert.equal(ui.requests.length, 2, 'recovery cannot be blocked by edits invalidating the draft');
  assert.deepEqual(ui.requests[1], ui.requests[0]);
  assert.equal(ui.destination(), '/records/1');
});
test('used key conflict never rotates or resubmits and provides existing-document recovery', async () => {
  const ui = mount({status: 409, ok: false, json: async () => ({error: 'changed retry',
    conflict_code: 'idempotency_key_used', existing_outbound: {id: 1, redirect_url: '/records/1'},
    candidates: [{id: 1, version: 2, available_quantity: 8}]})});
  await ui.submit();
  ui.controls.quantity.value = '3';
  await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.existingLink.hidden, false);
  assert.equal(ui.existingLink.href, '/records/1');
  assert.equal(ui.controls.quantity.value, '3');
});
test('unclassified conflict remains unresolved and keeps exact pending command', async () => {
  const ui = mount({status: 409, ok: false, json: async () => ({error: 'unknown', candidates: [{id: 1, version: 2, available_quantity: 8}]})});
  await ui.submit();
  ui.controls.quantity.value = '3';
  await ui.submit();
  assert.deepEqual(ui.requests[1], ui.requests[0]);
});
test('network failure retains exact idempotency key and successful retry redirects', async () => {
  const ui = mount(new Error('network unavailable'));
  await ui.submit();
  await ui.submit();
  assert.equal(ui.sent().idempotency_key, 'same-key');
  assert.equal(ui.controls.quantity.disabled, false);
  const success = mount({ok: true, json: async () => ({duplicate: true, redirect_url: '/records/1'})});
  await success.submit();
  assert.equal(success.destination(), '/records/1');
});
