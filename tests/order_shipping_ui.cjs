const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
  constructor() { this.value='';this.children=[];this.dataset={};this.listeners={};this.disabled=false; }
  addEventListener(name,fn) {(this.listeners[name] ||= []).push(fn);}
  async emit(name,event={}) {for(const fn of this.listeners[name]||[]) await fn({target:this,preventDefault(){},...event});}
  append(...items) {for(const item of items){item.parent=this;this.children.push(item);}}
  replaceChildren(...items){this.children=[];this.append(...items);}
  remove(){this.parent.children=this.parent.children.filter(item=>item!==this);}
  removeAttribute(key){delete this[key];}
  setAttribute(key,value){this[key]=value;}
  focus(){document.activeElement=this;}
  set innerHTML(_){throw Error('unsafe HTML');}
}
const nodes = Object.fromEntries(['order-extra-search','order-extra-results','order-status','order-submit','order-json','order-preview-token','shipment-customer-search','shipment-lines','add-shipment-line'].map(k=>[k,new Element()]));
function row(manual,quantity,customer='客户A') {
  const result = new Element();
  const select=new Element(),qty=new Element(),remark=new Element(),status=new Element(),spec=new Element(),remove=new Element();
  select.options=[{value:'',dataset:{}}];
  if(manual)select.options.push({value:String(manual),dataset:{manualId:String(manual),customer,drawingNo:`P${manual}`,specification:'规格'}});
  select.value=manual?String(manual):'';
  Object.defineProperty(select,'selectedOptions',{get:()=>select.options.filter(o=>o.value===select.value)});
  select.append=opt=>select.options.push(opt);
  qty.value=String(quantity); remark.value='原备注';
  const map={'shipment-order-select':select,'shipment-quantity':qty,'order-line-remark':remark,'order-line-status':status,'order-line-spec':spec,'remove-shipment-line':remove};
  result.querySelector=s=>map[s.slice(6,-1)];
  result.cloneNode=()=>row(null,'');
  result.append(select,qty,remark,status,spec,remove);
  return result;
}
let first=row(1,2),zero=row(2,0);
nodes['shipment-lines'].append(first,zero);
nodes['shipment-lines'].insertBefore=(item)=>nodes['shipment-lines'].append(item);
const form=new Element();form.action='/admin/shipped-orders/new';
form.querySelector=s=>nodes[s.slice(6,-1)];
const rows=()=>nodes['shipment-lines'].children;
global.document={createElement:()=>new Element()};
global.window={location:{assign(url){window.url=url;}}};
const requests=[];
global.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
global.FormData=class {constructor(){this.values={};}set(k,v){this.values[k]=v;}};
global.AbortController=class{signal={};abort(){}};
assert.ok(fs.existsSync('static/order_shipping.js'), 'ordinary editable-row controller must be implemented');
vm.runInThisContext(fs.readFileSync('static/order_shipping.js','utf8'));
initializeOrderShipmentLines(form,{shipmentLines:rows,bindLine(){},syncRecipients(){}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function respond(req,body,status=200){req.resolve({ok:status<400,status,json:async()=>body,redirected:false,headers:{get:()=> 'application/json'}});}
function previewBody(lines, fields={}) {
  return {preview_token:'preview',items:lines.map(l=>({...l,drawing_no:`P${l.manual_id}`,product_name:'产品',
    specification:'规格',unit:'件',order_no:'ORDER',allocation_candidates:[],allocations:[],
    inventory_deducted_quantity:Number(l.quantity),inventory_shortage_quantity:0,unallocated_quantity:0,...fields}))};
}
async function preview(fields={}){
  await flush();const req=requests.shift();assert.ok(req.url.includes('order-preview'));
  const lines=JSON.parse(req.options.body).lines;
  respond(req,previewBody(lines, fields));
  await flush();return lines;
}
(async()=>{
  let lines=await preview();
  assert.deepEqual(lines.map(l=>Number(l.quantity)),[2,0]);
  assert.equal(nodes['order-submit'].disabled,false);
  assert.match(zero.querySelector('[data-order-line-status]').textContent,/本次不发/);
  assert.equal(nodes['order-extra-search'].disabled,true,'must select a concrete customer');
  if(process.argv[2]==='stale-spec'){
    const quantity=first.querySelector('[data-shipment-quantity]');
    const remark=first.querySelector('[data-order-line-remark]');
    const select=first.querySelector('[data-shipment-order-select]');
    const spec=first.querySelector('[data-order-line-spec]');
    quantity.value='7';remark.value='保留 <备注>';remark.selectionStart=3;remark.selectionEnd=3;remark.focus();
    let acceptedSpec;
    let tokenValue=nodes['order-preview-token'].value;
    Object.defineProperty(nodes['order-preview-token'],'value',{
      get:()=>tokenValue,set(value){tokenValue=value;if(value)acceptedSpec=spec.textContent;}
    });
    void form.emit('input',{target:quantity});
    await preview({specification:'新版 <规格>',drawing_no:'NEW-DRAW',product_name:'新品名'});
    assert.equal(acceptedSpec,'新版 <规格>','refresh must display authoritative spec before accepting its token');
    assert.equal(spec.textContent,'新版 <规格>');
    assert.equal(select.selectedOptions[0].dataset.specification,'新版 <规格>');
    assert.match(select.selectedOptions[0].textContent,/NEW-DRAW.*新品名/);
    assert.equal(quantity.value,'7');assert.equal(remark.value,'保留 <备注>');
    assert.equal(document.activeElement,remark);assert.equal(remark.selectionStart,3);assert.equal(remark.selectionEnd,3);

    // A save conflict must render a fresh preview before allowing another save.
    const save=form.emit('submit');await flush();const saveRequest=requests.shift();
    assert.equal(saveRequest.url,form.action);
    assert.equal(JSON.parse(saveRequest.options.body.values.shipment_lines)[0].remark,'保留 <备注>');
    respond(saveRequest,{error:'规格已变化'},409);
    await preview({specification:'保存冲突后的规格',drawing_no:'LATEST',product_name:'刷新品名'});
    await save;
    assert.equal(acceptedSpec,'保存冲突后的规格');
    assert.equal(spec.textContent,'保存冲突后的规格');
    assert.match(select.selectedOptions[0].textContent,/LATEST.*刷新品名/);
    assert.equal(nodes['order-submit'].disabled,false);

    // In-flight responses cannot accept a token or disturb ongoing composition.
    void form.orderLinesChanged();await flush();const inFlight=requests.shift();
    await form.emit('compositionstart',{target:remark});remark.value='保留输入中的中文';
    respond(inFlight,previewBody(JSON.parse(inFlight.options.body).lines,{specification:'应忽略的旧响应'}));await flush();
    assert.equal(spec.textContent,'保存冲突后的规格');
    assert.equal(nodes['order-preview-token'].value,'');assert.equal(nodes['order-submit'].disabled,true);
    await form.emit('input',{target:remark});assert.equal(requests.length,0);
    void form.emit('compositionend',{target:remark});
    const composed=await preview({specification:'输入完成后的规格',drawing_no:'IME',product_name:'最终品名'});
    assert.equal(composed[0].remark,'保留输入中的中文');
    assert.equal(acceptedSpec,'输入完成后的规格');
    assert.equal(quantity.value,'7');assert.equal(remark.value,'保留输入中的中文');
    assert.equal(document.activeElement,remark);assert.equal(first.querySelector('[data-order-line-remark]'),remark);
    console.log('ordinary stale specification and IME behavior passed');return;
  }
  zero.querySelector('[data-order-line-remark]').value='<延后> & 保留';
  void form.emit('input',{target:zero.querySelector('[data-order-line-remark]')});
  lines=await preview();assert.equal(lines[1].remark,'<延后> & 保留');
  nodes['shipment-customer-search'].value='客户A';
  void nodes['shipment-customer-search'].emit('change');
  await preview();
  nodes['order-extra-search'].value='P1';
  void nodes['order-extra-search'].emit('input');await flush();
  const search=requests.shift();assert.match(search.url,/customer=%E5%AE%A2%E6%88%B7A/);
  respond(search,{items:[{manual_id:1,drawing_no:'P1',product_name:'产品',specification:'规格'}]});await flush();
  const button=nodes['order-extra-results'].children[0];
  void button.emit('click');lines=await preview();
  assert.equal(rows().length,2,'duplicate product stays one row');assert.equal(Number(lines[0].quantity),3);
  nodes['order-extra-search'].value='P3';void nodes['order-extra-search'].emit('input');await flush();
  const stale=requests.shift();
  nodes['shipment-customer-search'].value='客户B';void nodes['shipment-customer-search'].emit('change');await preview();
  respond(stale,{items:[{manual_id:3,drawing_no:'STALE'}]});await flush();
  assert.equal(nodes['order-extra-results'].children.length,0,'old customer search must not add a product');
  assert.deepEqual(JSON.parse(nodes['order-json'].value).map(l=>l.customer),['客户A','客户A'],'filter change preserves selected rows');
  first.remove();void form.orderLinesChanged();await flush();
  const allZero=requests.shift();respond(allZero,{error:'至少有一个产品的发货数量必须大于 0'},400);await flush();
  assert.equal(rows().length,1);assert.equal(nodes['order-submit'].disabled,true);
  assert.equal(rows()[0].querySelector('[data-order-line-remark]').value,'<延后> & 保留');
  console.log('ordinary UI behavior passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
