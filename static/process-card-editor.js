(() => {
  const busy = new WeakSet();
  document.addEventListener('submit', async (event) => {
    const form = event.target;
    const card = form.closest('[data-process-card]');
    if (!card || event.defaultPrevented || form.method.toLowerCase() !== 'post' || !new URL(form.action).pathname.includes('/processes')) return;
    event.preventDefault();
    if (busy.has(card)) return;
    if (form.dataset.productionProcessConfirm && !window.confirm(form.dataset.productionProcessConfirm)) return;
    busy.add(card);
    const data = new FormData(form);
    const isSave = form.hasAttribute('data-process-save');
    const edits = [...card.querySelectorAll('[data-process-edit]')]
      .filter(input => input.value !== input.defaultValue)
      .map(input => [input.id, input.value]);
    const controls = [...card.querySelectorAll('input, textarea, select, button')].map(input => [input, input.disabled]);
    const scroll = [window.scrollX, window.scrollY];
    const tables = [...card.querySelectorAll('.table-wrap')].map(table => table.scrollLeft);
    let status = card.querySelector('[data-process-status]');
    controls.forEach(([input]) => { input.disabled = true; });
    try {
      const response = await fetch(form.action, { method:'POST', body:data, credentials:'same-origin' });
      if (!response.ok) throw new Error('保存失败，请检查网络或登录状态后重试');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const error = doc.querySelector('.message.error, .flash.error');
      if (error) throw new Error(error.textContent.trim());
      const replacement = doc.getElementById(card.id);
      if (!replacement) throw new Error('操作已提交，请刷新页面查看最新工艺卡');
      if (!isSave) {
        const quantityAction = form.action.match(/processes\/(\d+)\/(complete|revert)$/);
        for (const [id, value] of edits) {
          if (quantityAction && id === `qty-${quantityAction[1]}`) continue;
          const input = replacement.querySelector(`#${CSS.escape(id)}`);
          if (input) input.value = value;
        }
      }
      card.replaceWith(replacement);
      [...replacement.querySelectorAll('.table-wrap')].forEach((table, index) => { table.scrollLeft = tables[index] || 0; });
      status = replacement.querySelector('[data-process-status]');
      if (status) status.textContent = isSave ? '本规格工艺参数已全部保存' : '操作成功；修改中的参数可点击“保存本规格”统一保存';
      window.scrollTo(...scroll);
      requestAnimationFrame(() => window.scrollTo(...scroll));
    } catch (error) {
      if (status) { status.textContent = error.message; status.classList.add('error'); }
    } finally {
      controls.forEach(([input, disabled]) => { input.disabled = disabled; });
      busy.delete(card);
    }
  });
})();
