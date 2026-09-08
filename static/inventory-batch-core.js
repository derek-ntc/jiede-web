((root) => {
  const maximum = 2147483647;
  function integer(value, minimum, label) {
    if (!/^[0-9]+$/.test(String(value))) throw new Error(`${label}必须为整数`);
    const result = Number(value);
    if (!Number.isSafeInteger(result) || result < minimum || result > maximum) {
      throw new Error(`${label}必须在 ${minimum} 至 ${maximum} 之间`);
    }
    return result;
  }
  function quantityForSets(perSet, sets) {
    const result = integer(perSet, 1, '每套用量') * integer(sets, 1, '组装套数');
    return integer(result, 1, '计算后的零件数量');
  }
  function submissionRows(rows, mode) {
    if (!rows.length || rows.length > 500) throw new Error('请勾选 1 至 500 行明细');
    return rows.map((row) => ({
      key: row.key,
      location_id: integer(row.location_id, 1, '库位'),
      quantity: integer(row.quantity, mode === 'adjust' ? 0 : 1, mode === 'adjust' ? '实际数量' : '入库数量'),
    }));
  }
  const api = {quantityForSets, submissionRows};
  root.InventoryBatchCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
