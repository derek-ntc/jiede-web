// Exercise the real shipment script with controllable DOM, timers, and transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const all = element => [element, ...element.children.flatMap(all)];
class Element {
  constructor(value = '') {
    this.value = value;
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.classList = {toggle() {}, add() {}};
    this.selectionStart = this.selectionEnd = 0;
    this.selectionDirection = 'none';
  }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  async emit(type, event = {}) {
    for (const listener of this.listeners[type] || []) {
      await listener({target: this, preventDefault() {}, ...event});
    }
  }
  append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
  appendChild(item) { this.append(item); return item; }
  contains(item) { return all(this).includes(item); }
  remove() {
    if (this.contains(document.activeElement)) document.activeElement = null;
    this.parent.children = this.parent.children.filter(item => item !== this);
    this.parent = null;
  }
  replaceChildren(...items) {
    if (this.contains(document.activeElement)) document.activeElement = null;
    this.children.forEach(item => { item.parent = null; });
    this.children = [];
    this.append(...items);
  }
  matches(selector) {
    return (selector === '[data-assembly-item-quantity]' && this.dataset.assemblyItemQuantity !== undefined) ||
      (selector === '[data-assembly-item-remark]' && this.dataset.assemblyItemRemark !== undefined);
  }
  querySelectorAll(selector) { return all(this).slice(1).filter(item => item.matches(selector)); }
  setAttribute(name, value) { this[name] = value; }
  focus() { document.activeElement = this; }
  setSelectionRange(start, end, direction) {
    this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction;
  }
  set innerHTML(_) { throw Error('Unexpected HTML insertion'); }
}

const timers = new Map();
let nextTimer = 0;
global.document = {activeElement: null, querySelectorAll: () => [], createElement: () => new Element()};
global.window = {
  setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; },
  clearTimeout(id) { timers.delete(id); },
  confirm: () => true,
  location: {origin: 'https://test.local', assign() {}},
};
const flush = () => new Promise(resolve => setImmediate(resolve));
const fireTimers = async () => {
  for (const [id, fn] of [...timers]) { timers.delete(id); void fn(); }
  await flush();
};
const pending = [];
global.fetch = (url, options) => new Promise(resolve => {
  pending.push({url, payload: JSON.parse(options.body), signal: options.signal, resolve});
});
const response = (body, status = 200) => ({ok: status < 400, status, json: async () => body});
const items = [
  {manual_id: 1, drawing_no: 'P1', product_name: '正数行', shipped_quantity: 3, remark: '先发'},
  {manual_id: 2, drawing_no: 'P2', product_name: '零数量行', shipped_quantity: 0, remark: '保留备注'},
].map(item => ({...item, specification: '规格', source_kind: 'bom', quantity_per_set: 1,
               calculated_quantity: 3, allocations: [], available_inventory: 9}));
const nodes = {};
for (const name of ['customer', 'drawing', 'set-quantity', 'submit', 'status', 'preview',
                    'warning-summary', 'component-search', 'component-results', 'recalculate']) nodes[name] = new Element();
nodes.customer.value = '客户A'; nodes.drawing.value = 'ASM-100'; nodes['set-quantity'].value = '3';
const initial = new Element();
initial.textContent = JSON.stringify({items, warnings: [], preview_token: 'initial'});
const form = new Element();
form.action = '/admin/shipped-orders/assembly/new';
form.append(...Object.values(nodes), initial);
form.querySelector = selector => selector === '[data-assembly-initial-preview]' ? initial : nodes[selector.slice(15, -1)];
vm.runInThisContext(fs.readFileSync('static/assembly_shipping.js', 'utf8'));
globalThis.__assemblyShippingTestApi.initializeAssemblyShipmentForm(form);
const quantities = () => form.querySelectorAll('[data-assembly-item-quantity]');
const remarkFor = id => form.querySelectorAll('[data-assembly-item-remark]').find(input => input.dataset.manualId === String(id));
const respondPreview = async request => {
  request.resolve(response({
    items: request.payload.selected_manual_ids.map(id => ({
      ...items.find(item => item.manual_id === Number(id)),
      shipped_quantity: Number(request.payload.overrides[id]), remark: request.payload.remarks[id],
    })), warnings: [], preview_token: 'fresh',
  }));
  await flush();
};

async function deletion() {
  await all(nodes.preview).find(item => item.dataset.assemblyRemoveItem === '1').emit('click');
  assert.deepEqual(form._assemblySelectedIds, ['2']);
  assert.deepEqual(quantities().map(input => input.dataset.manualId), ['2'], 'deleted positive row must disappear immediately');
  await fireTimers();
  assert.equal(pending.length, 1);
  assert.deepEqual(pending[0].payload.selected_manual_ids, ['2']);
  pending[0].resolve(response({error: '至少有一个产品的发货数量必须大于 0'}, 400));
  await flush();
  assert.deepEqual(quantities().map(input => input.dataset.manualId), ['2'], 'all-zero rejection must not resurrect a deleted row');
  assert.equal(quantities()[0].value, '0');
  assert.equal(remarkFor(2).value, '保留备注');
  assert.equal(nodes.submit.disabled, true);
  assert.equal(form._assemblyPreview, null);
  assert.match(nodes.status.textContent, /至少有一个产品.*大于 0/);
  quantities()[0].value = '1';
  await form.emit('input', {target: quantities()[0]});
  await fireTimers();
  await respondPreview(pending[1]);
  assert.deepEqual(quantities().map(input => input.dataset.manualId), ['2']);
  assert.equal(nodes.submit.disabled, false, 'remaining row can recover without resetting selection');
}

async function focus() {
  let remark = remarkFor(1);
  remark.value = '中文备注编辑'; remark.focus(); remark.setSelectionRange(2, 4, 'backward');
  await form.emit('input', {target: remark});
  await fireTimers();
  // Capture the current caret at replacement time, not at request time.
  remark.setSelectionRange(3, 5, 'backward');
  await respondPreview(pending[0]);
  assert.ok(document.activeElement === remarkFor(1), 'preview must retain focus on the active remark');
  remark = remarkFor(1);
  assert.equal(remark.value, '中文备注编辑');
  assert.deepEqual([remark.selectionStart, remark.selectionEnd, remark.selectionDirection], [3, 5, 'backward']);
  remark.value += '后续'; remark.setSelectionRange(8, 8, 'none');
  await form.emit('input', {target: remark});
  await fireTimers(); await respondPreview(pending[1]);
  assert.equal(document.activeElement, remarkFor(1));
  assert.deepEqual([remarkFor(1).selectionStart, remarkFor(1).selectionEnd], [8, 8]);
}

async function composition() {
  const remark = remarkFor(1);
  remark.value = '既有'; remark.focus();
  await form.emit('input', {target: remark});
  await fireTimers();
  assert.equal(pending.length, 1, 'start with an already pending preview');
  await form.emit('compositionstart', {target: remark});
  await respondPreview(pending[0]);
  assert.ok(remarkFor(1) === remark, 'late preview may not replace a composing input');
  assert.ok(document.activeElement === remark);
  remark.value = '既有zhong';
  await form.emit('input', {target: remark, isComposing: true});
  await fireTimers();
  assert.equal(pending.length, 1, 'composition must not schedule a mid-composition preview');
  assert.equal(nodes.submit.disabled, true);
  await form.emit('submit');
  assert.equal(pending.length, 1, 'Enter during IME composition may not fetch or save');
  remark.value = '既有中文'; remark.setSelectionRange(4, 4, 'none');
  await form.emit('compositionend', {target: remark});
  await form.emit('input', {target: remark, isComposing: false});
  await fireTimers();
  assert.equal(pending.length, 2, 'composition completion schedules exactly one preview');
  assert.equal(pending[1].payload.remarks['1'], '既有中文');
  await respondPreview(pending[1]);
  assert.equal(remarkFor(1).value, '既有中文');
  assert.equal(document.activeElement, remarkFor(1));
  assert.deepEqual([remarkFor(1).selectionStart, remarkFor(1).selectionEnd], [4, 4]);
  assert.equal(nodes.submit.disabled, false);
  // A second composition starts while debounce (not fetch) is pending, and
  // finishes without a separate final input event, as some IMEs do.
  const nextRemark = remarkFor(1);
  nextRemark.value = '继续';
  await form.emit('input', {target: nextRemark});
  await form.emit('compositionstart', {target: nextRemark});
  await fireTimers();
  assert.equal(pending.length, 2, 'compositionstart also cancels queued debounce');
  nextRemark.value = '继续输入'; nextRemark.setSelectionRange(4, 4, 'none');
  await form.emit('compositionend', {target: nextRemark});
  await fireTimers();
  assert.equal(pending.length, 3, 'compositionend alone must refresh the final remark');
  assert.equal(pending[2].payload.remarks['1'], '继续输入');
  await respondPreview(pending[2]);
  assert.equal(remarkFor(1).value, '继续输入');
  assert.equal(document.activeElement, remarkFor(1));
}

({deletion, focus, composition}[process.argv[2]])().catch(error => { console.error(error); process.exitCode = 1; });
