// Invoked by test_shipping_high_contrast_colors_in_real_browser.
// Opt in with JIEDE_BROWSER_TESTS=1; Node must resolve an installed Playwright.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const responses = JSON.parse(fs.readFileSync(0, 'utf8'));

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      const response = url.origin === 'http://shipping.test' && responses[url.pathname];
      await route.fulfill(response ? {status:200,...response} : {status:404,body:''});
    });
    const header = () => page.locator('.topbar').evaluate(el => {
      const styles = element => {
        const style = getComputedStyle(element);
        return Object.fromEntries(['color','backgroundColor','fontSize','fontFamily','padding','height'].map(key=>[key,style[key]]));
      };
      return [styles(el), styles(el.querySelector('.brand')), styles(el.querySelector('nav a'))];
    });
    for (const width of [1600,390]) {
      await page.setViewportSize({width,height:1000});
      await page.goto('http://shipping.test/admin/customers');
      const originalHeader = await header();
      await page.goto('http://shipping.test/admin/orders');
      assert.equal(await page.locator('.erp-page table th').first().evaluate(el=>getComputedStyle(el).color), 'rgb(71, 85, 96)', 'Order page must retain its original palette');
      for (const path of ['/admin/shipped-orders/create','/admin/shipped-orders']) {
        await page.goto('http://shipping.test'+path);
        assert.deepEqual(await header(), originalHeader, 'Shared dark header must not change');
        for (const mode of path.endsWith('/create') ? ['order','assembly'] : ['history']) {
          if(mode === 'assembly') await page.locator('[data-shipment-mode="assembly"]').click();
          const findings = await page.locator('.container').evaluate(root => {
            const problems = [];
            const luminance = color => {
              const channels = color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4);
              return channels[0]*0.2126+channels[1]*0.7152+channels[2]*0.0722;
            };
            const elements = [...root.querySelectorAll('*')].filter(el=>el.checkVisibility());
            for(const el of elements) {
              const style = getComputedStyle(el);
              const label = el.tagName+'.'+el.className;
              const hasText = [...el.childNodes].some(n=>n.nodeType===Node.TEXT_NODE && n.textContent.trim());
              if((hasText || el.matches('input,select,textarea,button')) && style.color!=='rgb(0, 0, 0)') problems.push(label+' text '+style.color);
              if(el.matches('[placeholder]') && getComputedStyle(el,'::placeholder').color!=='rgb(0, 0, 0)') problems.push(label+' placeholder not black');
              if(el.matches(':disabled') && style.opacity!=='1') problems.push(label+' disabled text is faded');
              if(el.matches('button:disabled')) {
                const rgb = style.backgroundColor.match(/[\d.]+/g).slice(0,3).map(Number);
                if(Math.max(...rgb)-Math.min(...rgb)>15) problems.push(label+' disabled button needs distinct neutral background');
              }
              for(const pseudo of ['::before','::after']) {
                const ps = getComputedStyle(el,pseudo);
                if(!['none','normal','""'].includes(ps.content) && ps.color!=='rgb(0, 0, 0)') problems.push(label+pseudo+' text not black');
              }
              if(el.matches('button,.button,.shipment-subnav a')) {
                const bg = style.backgroundColor;
                if(bg==='rgba(0, 0, 0, 0)' || (luminance(bg)+0.05)/0.05<7) problems.push(label+' black text needs light background: '+bg);
              }
              if(el.matches('.panel,.erp-query-panel,th,td,input:not([type="hidden"]):not([type="checkbox"]),select,textarea')) {
                for(const side of ['Top','Right','Bottom','Left']) {
                  if(parseFloat(style['border'+side+'Width'])>0 && style['border'+side+'Style']!=='none' && luminance(style['border'+side+'Color'])>0.3) problems.push(label+' border too light');
                }
              }
            }
            const selected = root.querySelector('.shipment-subnav a[aria-current="page"]');
            const other = root.querySelector('.shipment-subnav a:not([aria-current])');
            const active = getComputedStyle(selected), inactive = getComputedStyle(other);
            if(luminance(active.backgroundColor)>=luminance(inactive.backgroundColor)-0.08) problems.push('Selected subpage needs visibly darker background');
            if(Number(active.fontWeight)<=Number(inactive.fontWeight)) problems.push('Selected subpage needs stronger text');
            if(document.documentElement.scrollWidth>innerWidth) problems.push('Horizontal overflow');
            return problems;
          });
          assert.deepEqual(findings, [], path+' '+mode+' '+width+'px');
        }
        const button = page.locator('.shipped-page button:not(:disabled)').first();
        await button.hover();
        assert.equal(await button.evaluate(el=>getComputedStyle(el).color),'rgb(0, 0, 0)', 'Hovered button text');
      }
    }
    assert.deepEqual(errors,[]);
    console.log('Shipping contrast, subpage selection, mobile labels and style isolation passed.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
