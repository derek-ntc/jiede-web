const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const script = path.join(__dirname, '../../static/purchase-receipts.js');
const api = fs.existsSync(script) ? require(script) : {};

test('carton uses height and raw material uses thickness', () => {
  assert.equal(typeof api.visibleDimensionFields, 'function', 'receipt module missing');
  assert.deepEqual(api.visibleDimensionFields('carton'), ['length', 'width', 'height']);
  assert.deepEqual(api.visibleDimensionFields('raw_material'), ['length', 'width', 'thickness']);
});

test('only checked rows submit; edits remain verbatim and unchecked invalid rows are ignored', () => {
  assert.equal(typeof api.buildReceiptPayload, 'function', 'receipt module missing');
  const payload = api.buildReceiptPayload({idempotency_key: 'same', preview_token: 'signed'}, [
    {checked: true, fields: {purchase_order_item_id: '1', length: '1290', actual_quantity: '40'}},
    {checked: false, fields: {purchase_order_item_id: '2', actual_quantity: 'bad'}}
  ]);
  assert.deepEqual(payload.rows, [{purchase_order_item_id: '1', length: '1290', actual_quantity: '40'}]);
  assert.equal(payload.idempotency_key, 'same');
});

test('over receipt confirmation reuses exact payload and idempotency key', async () => {
  assert.equal(typeof api.submitReceipt, 'function', 'receipt module missing');
  const payload = {idempotency_key: 'same', rows: [{actual_quantity: '101', remark: 'keep'}]};
  const sent = [];
  const send = async body => {
    sent.push(JSON.parse(JSON.stringify(body)));
    return sent.length === 1 ? {status: 409, data: {conflict_code: 'revision_safe', needs_confirmation: true, error: '超收'}} : {status: 201, data: {id: 3}};
  };
  const result = await api.submitReceipt(payload, send, () => true);
  assert.equal(result.status, 201);
  assert.deepEqual(sent, [payload, {...payload, confirm_over_receipt: true}]);
  assert.equal(payload.confirm_over_receipt, undefined);
});

test('stale conflict and cancelled confirmation preserve input without automatic retry', async () => {
  assert.equal(typeof api.submitReceipt, 'function', 'receipt module missing');
  for (const confirmation of [true, false]) {
    const payload = {idempotency_key: 'same', rows: [{remark: 'untouched'}]};
    let calls = 0;
    const result = await api.submitReceipt(payload, async () => {
      calls++;
      return {status: 409, data: {conflict_code: 'revision_safe', needs_confirmation: confirmation, current: {rows: []}}};
    }, () => false);
    assert.equal(result.status, 409);
    assert.equal(calls, 1);
    assert.deepEqual(payload, {idempotency_key: 'same', rows: [{remark: 'untouched'}]});
  }
});

test('mounted stale preview refresh retains edits and key, then sends new signed preview', async () => {
  assert.equal(typeof api.init, 'function');
  // DOM surface delegates real event wiring and payload construction to production.
  const events = {};
  const node = props => ({hidden: false, textContent: '', ...props});
  const location = node({value: '1', options: [], replaceChildren(option) { this.options = [option]; }, add(option) { this.options.push(option); }});
  const fields = [node({dataset: {field: 'remark'}, value: '<b>keep edit</b>'}), location];
  location.dataset = {field: 'location_id'};
  const ordered = node({textContent: 90}); const actual = node(); const qualified = node(); const remaining = node();
  const row = {dataset: {itemId: '1'}, querySelector(selector) {
    return {'[data-row-selected]': {checked: true}, '[data-current-actual]': actual,
      '[data-current-ordered]': ordered, '[data-current-qualified]': qualified, '[data-current-remaining]': remaining,
      '[data-field="location_id"]': location}[selector];
  }, querySelectorAll: () => fields};
  const refresh = node({addEventListener: (type, handler) => { events.refresh = handler; }});
  const nodes = {'[data-receipt-data]': node({textContent: JSON.stringify({idempotency_key: 'same', preview_token: 'old'})}),
    '[data-receipt-message]': node(), '[data-conflict-summary]': node(), '[data-refresh-preview]': refresh, '[type="submit"]': node()};
  const form = {action: '/receipt', elements: {received_at: {value: '2026-09-12'}, remark: {value: 'header'}},
    querySelector: selector => nodes[selector], querySelectorAll: () => [row],
    addEventListener: (type, handler) => { events.submit = handler; }};
  const originalFetch = global.fetch; const originalWindow = global.window; const originalOption = global.Option;
  const sent = []; let redirected;
  global.Option = function(text, value) { this.text = text; this.value = value; };
  global.window = {confirm: () => false, location: {assign: url => { redirected = url; }}};
  global.fetch = async (url, options) => {
    sent.push(JSON.parse(options.body));
    return {status: sent.length === 1 ? 409 : 201, headers: {get: () => 'application/json'}, json: async () => sent.length === 1 ? {
      error: 'stale', conflict_code: 'revision_safe', needs_confirmation: false, preview_token: 'fresh', current: {
        rows: [{id: 1, item_name: '板材', ordered_quantity: 100, actual_quantity: 40, qualified_quantity: 30, remaining_quantity: 60}],
        locations: [{id: 1, code: 'A', name: '原料区'}]}
    } : {redirect_url: '/detail'}};
  };
  try {
    api.init({querySelector: selector => selector === '[data-receipt-form]' ? form : null});
    await events.submit({preventDefault() {}});
    assert.equal(refresh.hidden, false);
    assert.equal(sent.length, 1);
    assert.equal(fields[0].value, '<b>keep edit</b>');
    events.refresh();
    assert.equal(ordered.textContent, 100);
    assert.equal(actual.textContent, 40);
    assert.equal(remaining.textContent, 60);
    assert.equal(location.value, '1');
    await events.submit({preventDefault() {}});
    assert.equal(sent[1].preview_token, 'fresh');
    assert.equal(sent[1].idempotency_key, 'same');
    assert.deepEqual(sent[1].rows, [{purchase_order_item_id: '1', remark: '<b>keep edit</b>', location_id: '1'}]);
    assert.equal(redirected, '/detail');
  } finally { global.fetch = originalFetch; global.window = originalWindow; global.Option = originalOption; }
});

function mountReceipt(respond, confirm = () => false) {
  const vm = require('node:vm');
  const fields = {
    actual_quantity: {value: '40'}, qualified_quantity: {value: '30'},
    location_id: {value: '1'}, remark: {value: 'original row'}
  };
  Object.entries(fields).forEach(([field, input]) => { input.dataset = {field}; });
  const selected = {checked: true};
  const row = {dataset: {itemId: '1'},
    querySelector: selector => selector === '[data-row-selected]' ? selected : null,
    querySelectorAll: () => Object.values(fields)};
  const events = {};
  const message = {textContent: ''};
  const summary = {textContent: ''};
  const button = {disabled: false};
  const existingLink = {hidden: true};
  const refresh = {hidden: true, addEventListener: (_, handler) => { events.refresh = handler; }};
  const nodes = {'[data-receipt-data]': {textContent: JSON.stringify({idempotency_key: 'same-key', preview_token: 'signed', csrf_token: 'csrf'})},
    '[data-receipt-message]': message, '[data-conflict-summary]': summary,
    '[data-refresh-preview]': refresh, '[type="submit"]': button, '[data-existing-receipt]': existingLink};
  const form = {action: '/receipt', elements: {received_at: {value: '2026-09-12'}, remark: {value: 'original header'}},
    querySelector: selector => nodes[selector],
    querySelectorAll: selector => selector === '[data-receipt-row]' ? [row] : [...Object.values(fields), selected],
    addEventListener: (_, handler) => { events.submit = handler; }};
  const requests = [];
  let destination;
  vm.runInNewContext(fs.readFileSync(script, 'utf8'), {
    document: {querySelector: selector => selector === '[data-receipt-form]' ? form : null},
    window: {confirm, location: {assign: url => { destination = url; }}},
    fetch: async (_, options) => {
      requests.push(options.body);
      const result = await respond(requests.length);
      if (result instanceof Error) throw result;
      return {headers: {get: () => 'application/json'}, ...result};
    }
  });
  return {form, fields, selected, message, button, existingLink, refresh, requests,
    submit: () => events.submit({preventDefault() {}}), adopt: () => events.refresh(), destination: () => destination};
}

test('mounted lost receipt response then edits resends exact original bytes and recovers original receipt', async () => {
  const ui = mountReceipt(count => count === 1 ? new Error('lost committed response') :
    {status: 200, json: async () => ({duplicate: true, redirect_url: '/records/1'})});
  await ui.submit();
  ui.fields.actual_quantity.value = '60';
  ui.fields.remark.value = 'edited row';
  ui.form.elements.remark.value = 'edited header';
  ui.form.elements.received_at.value = '';
  ui.selected.checked = false;
  await ui.submit();
  assert.equal(ui.requests.length, 2, 'invalid later edits must not block recovery');
  assert.equal(ui.requests[1], ui.requests[0]);
  assert.equal(JSON.parse(ui.requests[1]).rows[0].actual_quantity, '40');
  assert.equal(ui.destination(), '/records/1');
  await ui.submit();
  assert.equal(ui.requests.length, 2, 'successful recovery latches the key');
});

test('mounted used receipt key latches submit and exposes recovery without adopting a preview', async () => {
  let confirmations = 0;
  const ui = mountReceipt(() => ({status: 409, json: async () => ({error: 'changed retry',
    conflict_code: 'idempotency_key_used', needs_confirmation: true,
    existing_receipt: {id: 1, redirect_url: '/records/1'}, preview_token: 'fresh', current: {rows: [], locations: []}})}),
  () => { confirmations++; return true; });
  await ui.submit();
  ui.fields.actual_quantity.value = '60';
  ui.adopt();
  await ui.submit();
  assert.equal(confirmations, 0, 'a persisted key cannot trigger a revised confirmed request');
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.refresh.hidden, true);
  assert.equal(ui.existingLink.hidden, false);
  assert.equal(ui.existingLink.href, '/records/1');
  assert.equal(ui.fields.actual_quantity.value, '60');
});

test('mounted ambiguous responses retain the pending command and cannot adopt an unclassified preview', async () => {
  for (const response of [
    {status: 409, json: async () => ({error: 'unknown', preview_token: 'fresh', current: {rows: [], locations: []}})},
    {status: 500, json: async () => ({error: 'server error'})},
    {status: 200, json: async () => ({})},
    {status: 200, redirected: true, json: async () => ({redirect_url: '/login'})}
  ]) {
    const ui = mountReceipt(() => response);
    await ui.submit();
    ui.fields.actual_quantity.value = '60';
    assert.equal(ui.refresh.hidden, true);
    ui.adopt();
    await ui.submit();
    assert.equal(ui.requests.length, 2);
    assert.equal(ui.requests[1], ui.requests[0]);
    assert.equal(ui.destination(), undefined);
  }
});

test('mounted lost over-receipt confirmed response retries the confirmed frozen command', async () => {
  const ui = mountReceipt(count => count === 1 ? {status: 409, json: async () => ({error: '超收',
    conflict_code: 'revision_safe', needs_confirmation: true, current: {rows: [{id: 1, remaining_quantity: 10}]}})} :
    count === 2 ? new Error('lost confirmed response') :
      {status: 200, json: async () => ({duplicate: true, redirect_url: '/records/1'})}, () => true);
  await ui.submit();
  assert.equal(ui.requests.length, 2);
  assert.equal(JSON.parse(ui.requests[1]).confirm_over_receipt, true);
  ui.fields.actual_quantity.value = '60';
  await ui.submit();
  assert.equal(ui.requests[2], ui.requests[1]);
  assert.equal(ui.destination(), '/records/1');
});

test('mounted explicit unused-key rejection permits corrected draft and cancelled over-receipt revision', async () => {
  for (const needsConfirmation of [false, true]) {
    const ui = mountReceipt(count => count === 1 ? {status: 409, json: async () => ({error: 'rejected',
      conflict_code: 'revision_safe', needs_confirmation: needsConfirmation})} :
      {status: 201, json: async () => ({redirect_url: '/records/2'})});
    await ui.submit();
    ui.fields.actual_quantity.value = '20';
    await ui.submit();
    assert.equal(JSON.parse(ui.requests[1]).rows[0].actual_quantity, '20');
    assert.equal(JSON.parse(ui.requests[1]).idempotency_key, 'same-key');
    assert.equal(JSON.parse(ui.requests[1]).confirm_over_receipt, undefined);
    assert.equal(ui.destination(), '/records/2');
  }
});
