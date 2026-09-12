/* Product-level ordinary shipment rows. Preview never replaces editable DOM. */
function orderRecipientHintText(values) {
  const labels = {recipient_name: '收货人', recipient_phone: '收货电话', address: '收货地址'};
  const missing = Object.keys(labels).filter(key => !String(values[key] || '').trim());
  if (missing.length) return `${missing.map(key => labels[key]).join('、')}未填写，请补充并核对。`;
  if (values.recipient_source === 'contact') return '姓名和电话来自客户联系人，请核对。';
  return '仅保存到本次送货单，不修改客户资料。';
}

function createOrderRecipientController(form, shipmentLines) {
  const defaults = JSON.parse(form.querySelector('[data-recipient-defaults]').textContent);
  const groups = form.querySelector('[data-recipient-groups]');
  const overrides = form.querySelector('[data-recipient-overrides]');
  const edits = new Map();
  return function syncRecipients() {
    const customers = [...new Set(shipmentLines().map(line => {
      const select = line.querySelector('[data-shipment-order-select]');
      return select.value ? select.selectedOptions[0]?.dataset.customer || '' : null;
    }).filter(value => value !== null))];
    groups.replaceChildren();
    const active = {};
    customers.forEach(customer => {
      if (!edits.has(customer)) edits.set(customer, {...(defaults[customer] || {})});
      const values = edits.get(customer);
      active[customer] = Object.fromEntries(
        ['recipient_name', 'recipient_phone', 'address'].map(key => [key, values[key] || ''])
      );
      const group = document.createElement('fieldset');
      const legend = document.createElement('legend');
      legend.textContent = `${customer || '未填写客户'} · 本次收货资料`;
      group.append(legend);
      [['recipient_name', '收货人'], ['recipient_phone', '收货电话'], ['address', '收货地址']].forEach(([key, title]) => {
        const label = document.createElement('label');
        label.textContent = title;
        const input = document.createElement('input');
        input.dataset.recipientField = key;
        input.value = values[key] || '';
        input.maxLength = key === 'address' ? 1000 : 100;
        input.addEventListener('input', () => {
          values[key] = input.value;
          active[customer][key] = input.value;
          overrides.value = JSON.stringify(active);
          hint.textContent = orderRecipientHintText(values);
        });
        label.append(input); group.append(label);
      });
      const hint = document.createElement('p');
      hint.dataset.recipientHint = '';
      hint.textContent = orderRecipientHintText(values);
      group.append(hint); groups.append(group);
    });
    overrides.value = JSON.stringify(active);
  };
}

function initializeOrderShipmentLines(form, {shipmentLines, bindLine, syncRecipients}) {
  const get = name => form.querySelector(`[data-${name}]`);
  const customer = get('shipment-customer-search');
  const search = get('order-extra-search');
  const results = get('order-extra-results');
  const status = get('order-status');
  const submit = get('order-submit');
  const json = get('order-json');
  const token = get('order-preview-token');
  const template = shipmentLines()[0].cloneNode(true);
  let generation = 0, searchGeneration = 0, composing = false, saving = false;
  const field = (row, name) => row.querySelector(`[data-${name}]`);
  const selection = row => field(row, 'shipment-order-select').selectedOptions[0];
  const identity = row => {
    const option = selection(row);
    return option?.value ? `${option.dataset.customer}\n${option.dataset.manualId}` : null;
  };
  function readRows() {
    return shipmentLines().filter(row => identity(row)).map(row => {
      const option = selection(row), select = field(row, 'shipment-order-select');
      const extra = select.value.startsWith('extra:');
      return {manual_id: Number(option.dataset.manualId), customer: option.dataset.customer,
        source_kind: extra ? 'extra' : 'order', ...(extra ? {} : {order_id: Number(select.value)}),
        quantity: field(row, 'shipment-quantity').value,
        remark: field(row, 'order-line-remark').value};
    });
  }
  function updateLocalRows() {
    for (const row of shipmentLines()) {
      const option = selection(row), selected = Boolean(identity(row));
      const select = field(row, 'shipment-order-select');
      const quantity = field(row, 'shipment-quantity');
      select.required = false;
      quantity.required = selected;
      quantity.min = '0'; quantity.max = '2147483647';
      field(row, 'order-line-spec').textContent = option?.dataset.specification || '';
      field(row, 'order-line-status').textContent = selected && quantity.value === '0' ? '本次不发' : '';
      field(row, 'remove-shipment-line').hidden = !selected && shipmentLines().length <= 1;
    }
  }
  async function refresh() {
    const current = ++generation;
    token.value = ''; submit.disabled = true;
    updateLocalRows();
    const lines = readRows(); json.value = JSON.stringify(lines);
    if (!lines.length) {status.textContent = '请选择订单或添加当前客户产品';return;}
    if (composing) return;
    status.textContent = '正在核对订单和库存…';
    try {
      const response = await fetch('/admin/shipped-orders/order-preview', {
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lines})});
      const data = await response.json();
      if (generation !== current || composing) return;
      if (!response.ok) {status.textContent=data.error || '预览失败';return;}
      const selected = shipmentLines().filter(row=>identity(row));
      data.items.forEach((item,index)=>{
        const row = selected[index], option = selection(row);
        const candidate = item.allocation_candidates.find(order=>String(order.id)===option.value);
        Object.assign(option.dataset, {drawingNo:item.drawing_no,productName:item.product_name,
          specification:item.specification,unit:item.unit});
        if (candidate) Object.assign(option.dataset, {orderNo:candidate.order_no,unshipped:String(candidate.unshipped_quantity)});
        option.textContent = option.value.startsWith('extra:')
          ? `${item.drawing_no || '-'} / ${item.product_name || '-'} / ${item.customer}（追加产品）`
          : `${option.dataset.orderNo || item.order_no} / ${item.drawing_no || '-'} / ${item.product_name || '-'} / 未发 ${option.dataset.unshipped || 0} / ${item.customer}`;
        field(row, 'order-line-spec').textContent=item.specification || '';
        field(row, 'shipment-quantity').placeholder=candidate ? `订单未发 ${candidate.unshipped_quantity}` : '';
        const messages = [];
        if (Number(item.quantity) === 0) messages.push('本次不发');
        if (item.unallocated_quantity) messages.push(`未关联订单 ${item.unallocated_quantity}：将保存为补充发货`);
        if (item.inventory_shortage_quantity) messages.push(`库存不足：扣减 ${item.inventory_deducted_quantity}，缺货 ${item.inventory_shortage_quantity}`);
        field(selected[index], 'order-line-status').textContent=messages.join('；');
      });
      status.textContent = '已核对；0 行仅保留在送货单，库存不足只扣除现有库存。';
      token.value=data.preview_token;
      submit.disabled=false;
    } catch {if(generation===current)status.textContent='预览失败，请重试';}
  }
  form.orderLinesChanged = refresh;
  function mergeSelected() {
    const seen = new Map();
    for (const row of shipmentLines()) {
      const key=identity(row); if(!key)continue;
      if (!seen.has(key)) {seen.set(key,row);continue;}
      const target=seen.get(key), amount=field(target,'shipment-quantity');
      amount.value=String(Number(amount.value||0)+Number(field(row,'shipment-quantity').value||0));
      const prior=field(target,'order-line-remark'), added=field(row,'order-line-remark').value;
      if(added && prior.value!==added)prior.value=[prior.value,added].filter(Boolean).join('\n');
      row.remove();
    }
    syncRecipients();
  }
  form.addEventListener('change', () => {mergeSelected();void refresh();});
  form.addEventListener('input', event => {
    if (event.target === search || event.target === customer) return;
    if (shipmentLines().some(row => ['shipment-quantity','order-line-remark'].some(name=>field(row,name)===event.target))) void refresh();
  });
  form.addEventListener('compositionstart',()=>{composing=true;++generation;token.value='';submit.disabled=true;});
  form.addEventListener('compositionend',()=>{composing=false;void refresh();});
  function customerChanged() {
    ++searchGeneration;results.replaceChildren();search.value='';search.disabled=!customer.value;
    search.placeholder=customer.value?'输入当前客户图号或名称':'添加产品前请选择具体客户';
  }
  customer.addEventListener('change',()=>{customerChanged();void refresh();});
  search.addEventListener('input', async()=>{
    const current=++searchGeneration, chosen=customer.value;
    results.replaceChildren();if(!chosen)return;
    try {
      const response=await fetch(`/admin/shipped-orders/order-product-options?customer=${encodeURIComponent(chosen)}&q=${encodeURIComponent(search.value.trim())}`);
      const data=await response.json();
      if(current!==searchGeneration || chosen!==customer.value)return;
      if(!response.ok){results.textContent=data.error || '产品搜索失败';return;}
      for(const product of data.items){
        const button=document.createElement('button');button.type='button';button.className='secondary compact-button';
        button.textContent=`添加 ${product.drawing_no} / ${product.product_name} / ${product.specification || '-'}`;
        button.addEventListener('click',()=>{
          if(chosen!==customer.value)return;
          let row=shipmentLines().find(row=>identity(row)===`${chosen}\n${product.manual_id}`);
          if(row){const quantity=field(row,'shipment-quantity');quantity.value=String(Number(quantity.value||0)+1);quantity.focus();}
          else {
            row=shipmentLines().find(row=>!identity(row)) || template.cloneNode(true);
            if(!shipmentLines().includes(row)){get('shipment-lines').insertBefore(row,get('add-shipment-line'));bindLine(row);}
            const select=field(row,'shipment-order-select'),option=document.createElement('option');
            option.value=`extra:${product.manual_id}`;option.textContent=`${product.drawing_no} / ${product.product_name} / ${chosen}（追加产品）`;
            Object.assign(option.dataset,{manualId:String(product.manual_id),customer:chosen,drawingNo:product.drawing_no,productName:product.product_name,specification:product.specification});
            select.append(option);select.value=option.value;
            field(row,'shipment-quantity').value='1';field(row,'order-line-remark').value='';
          }
          syncRecipients();void refresh();
        });results.append(button);
      }
    }catch{if(current===searchGeneration)results.textContent='产品搜索失败，请重试';}
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(composing || saving)return;
    if(!token.value){await refresh();return;}
    saving=true;submit.disabled=true;
    const data=new FormData(form);data.set('shipment_lines',JSON.stringify(readRows()));data.set('preview_token',token.value);
    try{
      const response=await fetch(form.action,{method:'POST',body:data});
      if(response.redirected){window.location.assign(response.url);return;}
      if(response.status===409){
        const contentType=response.headers?.get('content-type') || '';
        if(contentType.includes('application/json')){
          const body=await response.json();await refresh();status.textContent=body.error || '状态已变化，请核对最新预览后再次保存';
        }else status.textContent='此操作已提交不同内容，请刷新页面后新建发货';
      }else status.textContent='保存失败，所有更改已回滚；请重试';
    }catch{status.textContent='未能确认保存结果；请用相同内容重试，系统会防止重复发货';}
    finally{saving=false;submit.disabled=!token.value;}
  });
  customerChanged();void refresh();
}
