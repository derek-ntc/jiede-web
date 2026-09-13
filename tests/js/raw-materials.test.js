const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../../static/raw-materials.js');
test('specification distinguishes plate sides, tube diameter and optional cut length', () => {
  assert.equal(api.specification('plate', {length:'2000',width:'1000',thickness:'3'}), '板 2000×1000×3 mm');
  assert.equal(api.specification('square_tube', {length:'6000',width:'50',height:'30',thickness:'2'}), '方管 50×30×2 mm；定尺 6000 mm');
  assert.equal(api.specification('round_tube', {width:'60',height:'30',thickness:'3'}), '圆管 Φ60×3 mm');
  assert.equal(api.specification('round_tube', {width:'60'}), '圆管 Φ60×— mm');
});
