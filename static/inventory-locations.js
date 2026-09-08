(() => {
  const dialog = document.getElementById('inventory-location-dialog');
  const form = document.getElementById('inventory-location-form');
  if (!dialog || !form) return;
  const errorBox = form.querySelector('[data-location-error]');
  let saving = false;
  document.querySelectorAll('[data-open-location]').forEach((button) => {
    button.addEventListener('click', () => { errorBox.textContent = ''; dialog.showModal(); form.elements.code.focus(); });
  });
  form.querySelector('[data-close-location]').addEventListener('click', () => { if (!saving) dialog.close(); });
  dialog.addEventListener('cancel', (event) => { if (saving) event.preventDefault(); });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving) return;
    saving = true;
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    errorBox.textContent = '正在保存……';
    try {
      const response = await fetch(form.action, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:form.elements.name.value,code:form.elements.code.value,csrf_token:form.dataset.csrf})});
      const data = await response.json().catch(() => ({error:'登录状态已失效，请重新登录后操作。'}));
      if (!response.ok || !data.location) throw new Error(data.error || '新增失败');
      const loc = data.location;
      document.querySelectorAll('select[name="location_id"], [data-batch-location], [data-batch-default-location]').forEach((select) => {
        if (![...select.options].some((option) => option.value === String(loc.id))) select.add(new Option(`${loc.code} / ${loc.name}`, loc.id));
        if (!select.matches('[data-batch-location]')) {
          select.value = String(loc.id);
          select.dispatchEvent(new Event('change', {bubbles:true}));
        }
      });
      form.reset();
      dialog.close();
      const status = document.getElementById('batch-message');
      if (status) status.textContent = '库位已新增并选为统一库位，点击“应用到勾选行”即可使用。';
    } catch (error) { errorBox.textContent = error instanceof TypeError ? '网络异常，请重试；如提示编码已存在，请刷新后选择该库位。' : error.message; }
    finally { saving = false; button.disabled = false; }
  });
})();
