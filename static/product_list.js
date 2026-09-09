function initializeProductAssemblyBatch(root = document) {
  const form = root.querySelector("[data-product-assembly-batch]");
  const selectAll = root.querySelector("[data-product-select-all]");
  const checkboxes = Array.from(root.querySelectorAll("[data-product-row-select]"));
  if (!form || !selectAll) return;

  const count = form.querySelector("[data-product-selection-count]");
  const submitButton = form.querySelector("[data-product-assembly-submit]");
  const deleteButton = form.querySelector("[data-product-delete-open]");
  const deleteDialog = root.querySelector("[data-product-delete-dialog]");
  const deleteList = root.querySelector("[data-product-delete-list]");
  const deleteInputs = root.querySelector("[data-product-delete-inputs]");
  const deleteCount = root.querySelector("[data-product-delete-count]");

  function updateSelectionState() {
    const selected = checkboxes.filter((checkbox) => checkbox.checked);
    selectAll.checked = checkboxes.length > 0 && selected.length === checkboxes.length;
    selectAll.indeterminate = selected.length > 0 && selected.length < checkboxes.length;
    if (count) count.textContent = `已选择 ${selected.length} 个产品`;
    if (submitButton) submitButton.disabled = selected.length === 0;
    if (deleteButton) deleteButton.disabled = selected.length === 0;
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
  if (deleteButton && deleteDialog && deleteList && deleteInputs) {
    deleteButton.addEventListener("click", () => {
      const selected = checkboxes.filter((checkbox) => checkbox.checked);
      if (!selected.length) return;
      const rows = selected.map((checkbox) => {
        const row = document.createElement("li");
        row.textContent = [
          `产品图号：${checkbox.dataset.productDrawingNo || "-"}`,
          `产品名称：${checkbox.dataset.productName || "-"}`,
          `客户：${checkbox.dataset.productCustomer || "-"}`,
          `规格型号：${checkbox.dataset.productSpecification || "-"}`,
        ].join(" ｜ ");
        return row;
      });
      const inputs = selected.map((checkbox) => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "manual_id";
        input.value = checkbox.value;
        return input;
      });
      deleteList.replaceChildren(...rows);
      deleteInputs.replaceChildren(...inputs);
      if (deleteCount) deleteCount.textContent = String(selected.length);
      deleteDialog.showModal();
    });
    root.querySelectorAll("[data-product-delete-close]").forEach((button) => {
      button.addEventListener("click", () => deleteDialog.close());
    });
  }

  updateSelectionState();
}

initializeProductAssemblyBatch();

globalThis.__productAssemblyBatchTestApi = {
  initializeProductAssemblyBatch,
};
