(() => {
  const source = document.getElementById('inventory-batch-data');
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const core = globalThis.InventoryBatchCore;
  const records = new Map(data.rows.map((row) => [row.key, row]));
  const rows = [...document.querySelectorAll('[data-row-key]')];
  const form = document.getElementById('inventory-batch-form');
  const query = document.getElementById('batch-query');
  const message = document.getElementById('batch-message');
  const selectAll = document.querySelector('[data-select-all]');
  let busy = false, dirty = false, completed = false;
  const selected = () => rows.filter((row) => row.querySelector('[data-row-select]').checked);
  const report = (text) => { message.textContent = text; };
  function updateSelection() {
    const count = selected().length;
    document.getElementById('batch-selected-count').textContent = `已选 ${count} 行`;
    selectAll.checked = count > 0 && count === rows.length;
    selectAll.indeterminate = count > 0 && count < rows.length;
  }
  function locationChanged(row) {
    const record = records.get(row.dataset.rowKey);
    const location = row.querySelector('[data-batch-location]').value;
    const current = record.balances[location] || 0;
    row.querySelector('[data-location-stock]').textContent = current;
    if (data.mode === 'adjust') row.querySelector('[data-batch-quantity]').value = current;
  }
  rows.forEach((row) => {
    row.addEventListener('input', (event) => {
      if (event.target.matches('[data-batch-quantity]')) row.querySelector('[data-row-select]').checked = true;
      dirty = true;
      updateSelection();
    });
    row.addEventListener('change', (event) => {
      if (event.target.matches('[data-batch-location]')) {
        locationChanged(row);
        row.querySelector('[data-row-select]').checked = true;
      }
      dirty = true;
      updateSelection();
    });
  });
  selectAll.addEventListener('change', () => {
    rows.forEach((row) => { row.querySelector('[data-row-select]').checked = selectAll.checked; });
    dirty = true;
    updateSelection();
  });
  document.querySelector('[data-apply-location]').addEventListener('click', () => {
    const value = document.getElementById('batch-default-location').value;
    if (!value || !selected().length) return report('请先勾选明细并选择统一库位。');
    selected().forEach((row) => {
      row.querySelector('[data-batch-location]').value = value;
      locationChanged(row);
    });
    dirty = true;
    report(data.mode === 'adjust' ? '库位已设置，实际数量已重置为所选库位当前库存，请填写盘点结果。' : '库位已应用到勾选行。');
  });
  document.querySelector('[data-apply-sets]')?.addEventListener('click', () => {
    try {
      const picked = selected();
      if (!picked.length) throw new Error('请先勾选需要按套数计算的明细。');
      const sets = document.getElementById('batch-set-quantity').value;
      const values = picked.map((row) => core.quantityForSets(records.get(row.dataset.rowKey).quantity_per_set, sets));
      picked.forEach((row, index) => { row.querySelector('[data-batch-quantity]').value = values[index]; });
      dirty = true;
      report('已按套数 × 每套用量填写，仍可逐行修改。');
    } catch (error) { report(`${error.message}。请确认已选择组装图号，且勾选行有每套用量。`); }
  });
  query.addEventListener('submit', (event) => {
    if (busy || (dirty && !window.confirm('重新查询将清空当前勾选和填写的数据，是否继续？'))) event.preventDefault();
    else dirty = false;
  });
  document.getElementById('batch-customer').addEventListener('change', () => {
    query.elements.assembly.value = '';
    query.elements.order_no.value = '';
    query.requestSubmit();
  });
  window.addEventListener('beforeunload', (event) => {
    if ((dirty || busy) && !completed) { event.preventDefault(); event.returnValue = ''; }
  });
  function setBusy(value) {
    busy = value;
    document.querySelectorAll('#inventory-batch-form input, #inventory-batch-form select, #inventory-batch-form textarea, #inventory-batch-form button, .batch-tools input, .batch-tools select, .batch-tools button, #batch-query input, #batch-query select, #batch-query button')
      .forEach((element) => { element.disabled = value; });
  }
  async function save(payload) {
    const response = await fetch(window.location.pathname, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const result = await response.json().catch(() => ({error: '登录状态或页面凭证失效，请重新登录后查询。'}));
    return {response, result};
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || completed) return;
    try {
      const entries = selected().map((row) => ({key: row.dataset.rowKey, location_id: row.querySelector('[data-batch-location]').value, quantity: row.querySelector('[data-batch-quantity]').value}));
      const payload = {token: data.token, csrf_token: data.csrf_token, rows: core.submissionRows(entries, data.mode), remark: form.elements.remark.value.trim()};
      if (data.mode === 'adjust' && !payload.remark) throw new Error('请填写库存调整原因。');
      if (!window.confirm(`确认${data.mode === 'adjust' ? '按盘点实际数量调整' : '入库'}这 ${entries.length} 行明细？系统将记录库存流水。`)) return;
      setBusy(true);
      report('正在整批校验并保存，请勿关闭页面……');
      let {response, result} = await save(payload);
      if (!response.ok && result.needs_confirmation) {
        if (!window.confirm(result.error)) { report('已取消，本批数据尚未入库。'); return; }
        ({response, result} = await save({...payload, confirm_over_receipt: true}));
      }
      if (!response.ok) throw new Error(result.error || '保存失败，请检查明细。');
      if (!result.redirect_url) throw new Error('未取得保存结果，请重新查询流水后核对。');
      completed = true;
      dirty = false;
      report(`${result.duplicate ? '本批之前已保存，未重复写入' : '保存成功'}：${result.row_count} 行。`);
      window.location.assign(result.redirect_url);
    } catch (error) {
      report(error instanceof TypeError ? '网络异常，保存结果尚未确认。请保留本页并再次提交，同一批次不会重复入库。' : error.message);
    } finally { if (!completed) setBusy(false); }
  });
})();
