(function () {
  'use strict';
  function validateOutbound(state) {
    if (!String(state.usedBy || '').trim()) return {valid: false, error: '请填写领用人'};
    const date = String(state.outboundAt || '');
    const parsed = new Date(date + 'T00:00:00Z');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
      return {valid: false, error: '请填写有效出库日期'};
    }
    const selected = state.rows.filter(row => row.selected);
    if (!selected.length) return {valid: false, error: '请至少勾选一个库存批次'};
    for (const row of selected) {
      if (row.unavailable) return {valid: false, error: '所选批次已不可出库，请取消勾选并复核'};
      if (!/^[1-9]\d*$/.test(String(row.quantity)) || !Number.isSafeInteger(Number(row.quantity)) || Number(row.quantity) > 2147483647) {
        return {valid: false, error: '出库数量必须是正整数'};
      }
      if (Number(row.quantity) > Number(row.availableQuantity)) return {valid: false, error: '出库数量不能超过当前可用数量'};
    }
    return {valid: true};
  }
  function buildPayload(state, data) {
    return {category: data.category, csrf_token: data.csrf_token, idempotency_key: data.idempotency_key,
      outbound_at: state.outboundAt, used_by: state.usedBy, remark: state.remark,
      rows: state.rows.filter(row => row.selected).map(row => ({lot_id: row.lotId, quantity: Number(row.quantity),
        expected_version: Number(row.expectedVersion), remark: row.remark}))};
  }
  function refreshCandidates(state, candidates) {
    const current = new Map(candidates.map(row => [String(row.id), row]));
    return {...state, rows: state.rows.map(row => {
      const fresh = current.get(String(row.lotId));
      return {...row, availableQuantity: fresh ? fresh.available_quantity : 0,
        expectedVersion: fresh ? fresh.version : row.expectedVersion, unavailable: !fresh};
    })};
  }
  const api = {validateOutbound, buildPayload, refreshCandidates};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  const form = document.querySelector('[data-outbound-form]');
  if (!form) return;
  // Only checked rows participate in validation; unchecked numeric drafts may be invalid.
  form.noValidate = true;
  const data = JSON.parse(document.querySelector('[data-outbound-data]').textContent);
  const elements = Array.from(form.querySelectorAll('[data-outbound-row]'));
  const message = form.querySelector('[data-outbound-message]');
  const submit = form.querySelector('button[type="submit"]');
  let inFlight = false;
  function readState() {
    return {usedBy: form.elements.used_by.value, outboundAt: form.elements.outbound_at.value,
      remark: form.elements.remark.value, rows: elements.map(el => ({lotId: Number(el.dataset.lotId),
        expectedVersion: Number(el.dataset.version), availableQuantity: Number(el.dataset.available),
        unavailable: el.dataset.unavailable === 'true', selected: el.querySelector('[data-field="selected"]').checked,
        quantity: el.querySelector('[data-field="quantity"]').value, remark: el.querySelector('[data-field="remark"]').value}))};
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (inFlight) return;
    const state = readState();
    const check = validateOutbound(state);
    if (!check.valid) { message.textContent = check.error; return; }
    inFlight = true;
    submit.disabled = true;
    // Freeze edits while a command is in flight; preserve the exact key/content on network retry.
    const inputs = Array.from(form.querySelectorAll('input,textarea'));
    inputs.forEach(input => { input.disabled = true; });
    try {
      const response = await fetch(form.action || window.location.href, {method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        body: JSON.stringify(buildPayload(state, data))});
      if (response.redirected) throw new Error('登录状态或权限已变化，请在新窗口重新登录后重试');
      const result = await response.json();
      if (response.ok) { window.location.assign(result.redirect_url); return; }
      if (response.status === 409 && Array.isArray(result.candidates)) {
        const refreshed = refreshCandidates(state, result.candidates);
        refreshed.rows.forEach((row, index) => {
          const el = elements[index];
          el.dataset.available = row.availableQuantity;
          el.dataset.version = row.expectedVersion;
          el.dataset.unavailable = String(row.unavailable);
          el.querySelector('[data-current-quantity]').textContent = row.unavailable ? '不可出库' : String(row.availableQuantity);
          el.querySelector('[data-field="quantity"]').max = row.availableQuantity;
        });
        // A definitive conflict saved nothing. New reviewed content receives a new command key.
        data.idempotency_key = Array.from(globalThis.crypto.getRandomValues(new Uint8Array(24)),
          byte => byte.toString(16).padStart(2, '0')).join('');
        message.textContent = (result.error || '库存已变化') + '。已更新当前可用数量；填写内容已保留，请复核后再次提交。';
      } else message.textContent = result.error || '出库未保存，请复核后重试';
    } catch (error) {
      message.textContent = error.message + '。填写内容与提交标识已保留，请勿重复新建单据。';
    } finally {
      inFlight = false;
      submit.disabled = false;
      inputs.forEach(input => { input.disabled = false; });
    }
  });
})();
