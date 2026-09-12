(function () {
  'use strict';
  function visibleDimensionFields(category) {
    if (category === 'carton') return ['length', 'width', 'height'];
    if (category === 'raw_material') return ['length', 'width', 'thickness'];
    return ['length', 'width', 'height', 'thickness'];
  }
  function buildReceiptPayload(header, rows) {
    return {...header, rows: rows.filter(row => row.checked).map(row => ({...row.fields}))};
  }
  async function submitReceipt(payload, send, confirm) {
    const result = await send(payload);
    if (result.status === 409 && result.data.conflict_code === 'revision_safe' &&
        result.data.needs_confirmation && await confirm(result.data)) {
      return send({...payload, confirm_over_receipt: true});
    }
    return result;
  }
  function init(root) {
    const form = root.querySelector('[data-receipt-form]');
    const voidForm = root.querySelector('[data-void-receipt]');
    if (voidForm) voidForm.addEventListener('submit', event => {
      if (!window.confirm('确认作废到货单并冲销本单库存？')) event.preventDefault();
    });
    if (!form) return;
    const state = JSON.parse(form.querySelector('[data-receipt-data]').textContent);
    const message = form.querySelector('[data-receipt-message]');
    const summary = form.querySelector('[data-conflict-summary]');
    const refresh = form.querySelector('[data-refresh-preview]');
    const submit = form.querySelector('[type="submit"]');
    const existingLink = form.querySelector('[data-existing-receipt]');
    let currentConflict = null;
    let busy = false;
    let pendingBody = null;
    let recoveryRequired = false;
    function showCurrent(data) {
      if (!data.current) return;
      summary.textContent = data.current.rows.map(row => `${row.item_name || row.drawing_no || row.material}：订购 ${row.ordered_quantity}，累计到货 ${row.actual_quantity}，累计合格 ${row.qualified_quantity}，剩余 ${row.remaining_quantity}`).join('\n');
    }
    refresh.addEventListener('click', () => {
      if (busy || recoveryRequired || !currentConflict) return;
      state.preview_token = currentConflict.preview_token;
      const rows = new Map(currentConflict.current.rows.map(row => [String(row.id), row]));
      form.querySelectorAll('[data-receipt-row]').forEach(element => {
        const row = rows.get(element.dataset.itemId);
        if (row) {
          element.querySelector('[data-current-ordered]').textContent = row.ordered_quantity;
          element.querySelector('[data-current-actual]').textContent = row.actual_quantity;
          element.querySelector('[data-current-qualified]').textContent = row.qualified_quantity;
          element.querySelector('[data-current-remaining]').textContent = row.remaining_quantity;
        }
        const select = element.querySelector('[data-field="location_id"]');
        const selected = select.value;
        select.replaceChildren(new Option('请选择仓位', ''));
        currentConflict.current.locations.forEach(location => select.add(new Option(`${location.code} · ${location.name}`, String(location.id))));
        if (selected && !currentConflict.current.locations.some(location => String(location.id) === selected)) {
          select.add(new Option('原选仓位已停用，请重新选择', selected));
          select.options[select.options.length - 1].disabled = true;
        }
        select.value = selected;
      });
      refresh.hidden = true;
      currentConflict = null;
      message.textContent = '已采用最新预览，填写值已保留。请核对最新数量及仓位后再次提交。';
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || recoveryRequired) return;
      if (pendingBody === null) {
        const rows = Array.from(form.querySelectorAll('[data-receipt-row]')).map(element => ({
          checked: element.querySelector('[data-row-selected]').checked,
          fields: {purchase_order_item_id: element.dataset.itemId, ...Object.fromEntries(
            Array.from(element.querySelectorAll('[data-field]')).map(input => [input.dataset.field, input.value]))}
        }));
        const draft = buildReceiptPayload({...state, received_at: form.elements.received_at.value, remark: form.elements.remark.value}, rows);
        if (!draft.rows.length) { message.textContent = '请至少勾选一条到货明细。'; return; }
        pendingBody = JSON.stringify(draft);
      }
      const payload = JSON.parse(pendingBody);
      busy = true;
      submit.disabled = true;
      refresh.hidden = true;
      currentConflict = null;
      message.textContent = '正在保存…';
      try {
        const send = async body => {
          // Capture the confirmed command too, before a possibly lost committed response.
          pendingBody = JSON.stringify(body);
          const response = await fetch(form.action, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: pendingBody});
          if (response.redirected) throw new Error('登录状态或权限已变化，请在新窗口确认登录状态');
          if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('会话或请求已失效，请在新页面确认登录状态；本页填写值已保留。');
          return {status: response.status, data: await response.json()};
        };
        const result = await submitReceipt(payload, send, data => {
          showCurrent(data);
          const remaining = new Map((data.current?.rows || []).map(row => [String(row.id), row.remaining_quantity]));
          const excess = payload.rows.filter(row => Number(row.actual_quantity) > remaining.get(String(row.purchase_order_item_id)))
            .map(row => `${row.item_name || row.material}：本次 ${row.actual_quantity}，剩余 ${remaining.get(String(row.purchase_order_item_id))}`).join('\n');
          return window.confirm(`${data.error}\n${excess}\n确认超收并入库？`);
        });
        if ((result.status === 200 || result.status === 201) &&
            typeof result.data.redirect_url === 'string' && result.data.redirect_url) {
          recoveryRequired = true;
          window.location.assign(result.data.redirect_url);
          return;
        }
        if (result.status === 409 && result.data.conflict_code === 'idempotency_key_used') {
          recoveryRequired = true;
          message.textContent = '该提交标识已有到货单，不能把修改内容另存为第二张。填写内容已保留，请先查看原到货单核对。';
          if (result.data.existing_receipt && existingLink) {
            existingLink.href = result.data.existing_receipt.redirect_url;
            existingLink.hidden = false;
          }
        } else if (result.status === 409 && result.data.conflict_code === 'revision_safe') {
          // Only a server-confirmed unused key permits edits or preview adoption.
          pendingBody = null;
          message.textContent = result.data.error || '保存失败，填写值已保留。';
          showCurrent(result.data);
          if (result.data.current && result.data.preview_token && !result.data.needs_confirmation) {
            currentConflict = result.data;
            refresh.hidden = false;
          }
        } else message.textContent = (result.data.error || '提交结果尚未确认') + '。再次提交只核对上次原始内容，不提交后续编辑。';
      } catch (error) {
        message.textContent = (error.message || '网络异常') + '。原始提交内容与标识已保留；再次提交只核对原始内容，不提交后续编辑。请勿重复新建单据。';
      }
      finally { busy = false; submit.disabled = recoveryRequired; }
    });
  }
  const api = {visibleDimensionFields, buildReceiptPayload, submitReceipt, init};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document !== 'undefined') init(document);
})();
