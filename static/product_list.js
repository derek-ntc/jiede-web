function initializeProductAssemblyBatch(root = document) {
  const form = root.querySelector("[data-product-assembly-batch]");
  const selectAll = root.querySelector("[data-product-select-all]");
  const checkboxes = Array.from(root.querySelectorAll("[data-product-row-select]"));
  if (!form || !selectAll) return;

  const count = form.querySelector("[data-product-selection-count]");
  const submitButton = form.querySelector("[data-product-assembly-submit]");

  function updateSelectionState() {
    const selected = checkboxes.filter((checkbox) => checkbox.checked);
    selectAll.checked = checkboxes.length > 0 && selected.length === checkboxes.length;
    selectAll.indeterminate = selected.length > 0 && selected.length < checkboxes.length;
    if (count) count.textContent = `已选择 ${selected.length} 个产品`;
    if (submitButton) submitButton.disabled = selected.length === 0;
  }

  selectAll.addEventListener("change", () => {
    checkboxes.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
    updateSelectionState();
  });
  checkboxes.forEach((checkbox) => {
    checkbox.addEventListener("change", updateSelectionState);
  });
  form.addEventListener("submit", (event) => {
    const selectedCount = checkboxes.filter((checkbox) => checkbox.checked).length;
    if (!selectedCount) {
      event.preventDefault();
      return;
    }
    const drawingNo = form.elements.assembly_drawing_no.value.trim();
    const quantity = form.elements.quantity_per_set.value;
    if (!window.confirm(`确定为 ${selectedCount} 个产品设置组装图号 ${drawingNo}，每套 ${quantity} 个吗？`)) {
      event.preventDefault();
    }
  });

  updateSelectionState();
}

initializeProductAssemblyBatch();

globalThis.__productAssemblyBatchTestApi = {
  initializeProductAssemblyBatch,
};
