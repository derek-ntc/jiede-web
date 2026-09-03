const MAX_ORDER_QUANTITY = 2147483647;

function normalizeOrderEntryValue(value) {
  return (value || "").trim().toLowerCase();
}

function productSelectsForRow(row) {
  return [
    row.querySelector("[data-product-drawing-select]"),
    row.querySelector("[data-product-name-select]"),
  ].filter(Boolean);
}

function syncProductRow(row, sourceSelect = null) {
  const selects = productSelectsForRow(row);
  const source = sourceSelect || selects.find((select) => select.value) || selects[0];
  const productId = source?.value || "";
  selects.forEach((select) => { select.value = productId; });
  const option = source?.selectedOptions?.[0];
  const customerCell = row.querySelector("[data-customer-name-cell]");
  if (customerCell) customerCell.textContent = option?.dataset.customer || "-";
}

function filterProductRow(row, customerQuery) {
  const customer = normalizeOrderEntryValue(customerQuery);
  const selects = productSelectsForRow(row);
  const selected = selects.find((select) => select.value)?.selectedOptions?.[0];
  const selectedStillVisible = !customer || normalizeOrderEntryValue(selected?.dataset.customer) === customer;

  selects.forEach((select) => {
    Array.from(select.options).forEach((option) => {
      const matches = !option.value || !customer ||
        normalizeOrderEntryValue(option.dataset.customer) === customer;
      option.hidden = !matches;
      option.disabled = !matches;
    });
    if (!selectedStillVisible) select.value = "";
  });
  syncProductRow(row);
}

function applyOrderDeliveryDate(body, value) {
  body.querySelectorAll("[data-order-item]").forEach((row) => {
    const plannedShipDate = row.querySelector('input[name="planned_ship_at"]');
    if (plannedShipDate) plannedShipDate.value = value;
  });
}

function populateAssemblyOptions(select, drawingNumbers) {
  if (!select) return;
  const options = [];
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = drawingNumbers.length
    ? "请选择组装图号"
    : "该客户未配置组装图号";
  options.push(placeholder);
  drawingNumbers.forEach((drawingNo) => {
    const option = document.createElement("option");
    option.value = drawingNo;
    option.textContent = drawingNo;
    options.push(option);
  });
  select.replaceChildren(...options);
  select.disabled = drawingNumbers.length === 0;
}

function resetOrderItemRow(row, plannedShipAt = "") {
  productSelectsForRow(row).forEach((select) => { select.value = ""; });
  row.querySelectorAll("input").forEach((input) => {
    if (input.type === "checkbox") input.checked = false;
    else input.value = "";
  });
  const plannedShipDate = row.querySelector('input[name="planned_ship_at"]');
  if (plannedShipDate) plannedShipDate.value = plannedShipAt;
  syncProductRow(row);
  return row;
}

function replaceRowsWithAssembly(
  body,
  rowTemplate,
  items,
  setQuantity,
  plannedShipAt,
  customer,
) {
  const preparedItems = items.map((item) => {
    const quantity = Number(item.quantity_per_set) * Number(setQuantity);
    if (!Number.isSafeInteger(quantity) || quantity <= 0 || quantity > MAX_ORDER_QUANTITY) {
      throw new Error(`自动计算的配件数量必须在 1 到 ${MAX_ORDER_QUANTITY} 之间`);
    }
    return {item, quantity};
  });
  body.replaceChildren();
  preparedItems.forEach(({item, quantity: calculatedQuantity}) => {
    const row = resetOrderItemRow(rowTemplate.cloneNode(true), plannedShipAt);
    filterProductRow(row, customer);
    const drawingSelect = row.querySelector("[data-product-drawing-select]");
    if (drawingSelect) drawingSelect.value = String(item.manual_id);
    syncProductRow(row, drawingSelect);
    const quantity = row.querySelector('[name="quantity"]');
    if (quantity) {
      quantity.value = String(calculatedQuantity);
    }
    body.appendChild(row);
  });
}

function hasMeaningfulOrderRows(body) {
  return Array.from(body.querySelectorAll("[data-order-item]")).some((row) => {
    const manualId = row.querySelector("[data-product-drawing-select]")?.value || "";
    const quantity = row.querySelector('[name="quantity"]')?.value || "";
    const remark = row.querySelector('[name="remark"]')?.value || "";
    return Boolean(manualId || quantity || remark);
  });
}

function initializeOrderEntry(root = document) {
  const form = root.querySelector("[data-order-entry]");
  if (!form) return;

  const body = form.querySelector("[data-order-items]");
  const addButton = form.querySelector("[data-add-row]");
  const customerFilter = form.querySelector("[data-order-customer-filter]");
  const deliveryDate = form.querySelector("[data-order-delivery-date]");
  const assemblyDrawing = form.querySelector("[data-order-assembly-drawing]");
  const assemblyQuantity = form.querySelector("[data-order-assembly-quantity]");
  const generateAssemblyButton = form.querySelector("[data-generate-assembly-order]");
  const assemblyStatus = form.querySelector("[data-assembly-order-status]");
  const assemblyOptionsUrl = form.dataset?.assemblyOptionsUrl || "";
  const assemblyDefinitionUrl = form.dataset?.assemblyDefinitionUrl || "";
  const rowTemplate = body.querySelector("[data-order-item]").cloneNode(true);
  let assemblyRequestSequence = 0;
  let assemblyGenerationSequence = 0;

  function filterAllProductRows() {
    body.querySelectorAll("[data-order-item]").forEach((row) => {
      filterProductRow(row, customerFilter?.value || "");
    });
  }

  function updateRemoveState() {
    const rows = body.querySelectorAll("[data-order-item]");
    rows.forEach((row) => {
      row.querySelector("[data-remove-row]").disabled = rows.length === 1;
    });
  }

  function setAssemblyStatus(message) {
    if (assemblyStatus) assemblyStatus.textContent = message;
  }

  function updateAssemblyGenerateState() {
    if (assemblyQuantity) {
      assemblyQuantity.disabled = !assemblyDrawing?.value;
      if (assemblyQuantity.disabled) assemblyQuantity.value = "";
    }
    if (generateAssemblyButton) {
      generateAssemblyButton.disabled = !(
        customerFilter?.value &&
        assemblyDrawing?.value &&
        Number.isInteger(Number(assemblyQuantity?.value)) &&
        Number(assemblyQuantity?.value) > 0 &&
        Number(assemblyQuantity?.value) <= MAX_ORDER_QUANTITY
      );
    }
  }

  async function loadAssemblyOptions() {
    if (!assemblyDrawing) return;
    const requestSequence = ++assemblyRequestSequence;
    assemblyDrawing.disabled = true;
    populateAssemblyOptions(assemblyDrawing, []);
    updateAssemblyGenerateState();
    if (!customerFilter?.value) {
      setAssemblyStatus("选择客户和组装图号后，可按套数自动生成配件数量。");
      return;
    }
    setAssemblyStatus("正在加载该客户的组装图号…");
    try {
      const params = new URLSearchParams({customer: customerFilter.value});
      const response = await fetch(`${assemblyOptionsUrl}?${params}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "组装图号加载失败");
      if (requestSequence !== assemblyRequestSequence) return;
      populateAssemblyOptions(
        assemblyDrawing,
        payload.assembly_drawing_numbers || [],
      );
      setAssemblyStatus(
        payload.assembly_drawing_numbers?.length
          ? "请选择组装图号并填写组装数量。"
          : "该客户尚未配置组装图号，可继续手工添加产品。",
      );
    } catch (error) {
      if (requestSequence !== assemblyRequestSequence) return;
      populateAssemblyOptions(assemblyDrawing, []);
      setAssemblyStatus(error.message || "组装图号加载失败");
    }
    updateAssemblyGenerateState();
  }

  body.addEventListener("change", (event) => {
    if (
      event.target.matches("[data-product-drawing-select]") ||
      event.target.matches("[data-product-name-select]")
    ) {
      syncProductRow(event.target.closest("[data-order-item]"), event.target);
    }
  });

  customerFilter?.addEventListener("input", filterAllProductRows);
  customerFilter?.addEventListener("change", () => {
    assemblyGenerationSequence += 1;
    filterAllProductRows();
    loadAssemblyOptions();
  });
  assemblyDrawing?.addEventListener("change", () => {
    assemblyGenerationSequence += 1;
    updateAssemblyGenerateState();
    if (assemblyDrawing.value) {
      setAssemblyStatus("填写组装数量后生成配件清单。");
    }
  });
  assemblyQuantity?.addEventListener("input", () => {
    assemblyGenerationSequence += 1;
    updateAssemblyGenerateState();
  });
  assemblyQuantity?.addEventListener("change", () => {
    assemblyGenerationSequence += 1;
    updateAssemblyGenerateState();
  });
  deliveryDate?.addEventListener("input", () => {
    applyOrderDeliveryDate(body, deliveryDate.value);
  });
  deliveryDate?.addEventListener("change", () => {
    applyOrderDeliveryDate(body, deliveryDate.value);
  });

  body.addEventListener("click", (event) => {
    if (!event.target.matches("[data-remove-row]")) return;
    event.target.closest("[data-order-item]").remove();
    updateRemoveState();
  });

  addButton.addEventListener("click", () => {
    const row = resetOrderItemRow(
      rowTemplate.cloneNode(true),
      deliveryDate?.value || "",
    );
    body.appendChild(row);
    filterProductRow(row, customerFilter?.value || "");
    updateRemoveState();
  });

  generateAssemblyButton?.addEventListener("click", async () => {
    const generationCustomer = customerFilter?.value || "";
    const generationDrawing = assemblyDrawing?.value || "";
    const generationSetQuantity = assemblyQuantity?.value || "";
    const setQuantity = Number(generationSetQuantity);
    if (
      !generationCustomer ||
      !generationDrawing ||
      !Number.isInteger(setQuantity) ||
      setQuantity <= 0 ||
      setQuantity > MAX_ORDER_QUANTITY
    ) {
      setAssemblyStatus(`请选择客户、组装图号，并填写 1 到 ${MAX_ORDER_QUANTITY} 之间的整数套数。`);
      return;
    }
    if (
      hasMeaningfulOrderRows(body) &&
      !window.confirm("生成组装配件清单将替换当前产品清单，是否继续？")
    ) {
      return;
    }
    const generationSequence = ++assemblyGenerationSequence;
    generateAssemblyButton.disabled = true;
    setAssemblyStatus("正在生成配件清单…");
    try {
      const params = new URLSearchParams({
        customer: generationCustomer,
        assembly_drawing_no: generationDrawing,
      });
      const response = await fetch(`${assemblyDefinitionUrl}?${params}`);
      const payload = await response.json();
      if (
        generationSequence !== assemblyGenerationSequence ||
        customerFilter.value !== generationCustomer ||
        assemblyDrawing.value !== generationDrawing ||
        assemblyQuantity.value !== generationSetQuantity
      ) {
        setAssemblyStatus("选择已改变，请按当前客户、图号和套数重新生成配件清单。");
        return;
      }
      if (!response.ok) throw new Error(payload.error || "配件清单生成失败");
      if (!payload.items?.length) throw new Error("该组装图号没有配置配件");
      replaceRowsWithAssembly(
        body,
        rowTemplate,
        payload.items,
        setQuantity,
        deliveryDate?.value || "",
        generationCustomer,
      );
      assemblyDrawing.value = payload.assembly_drawing_no;
      updateRemoveState();
      setAssemblyStatus(`已生成 ${payload.items.length} 种配件，可继续修改各行数量。`);
    } catch (error) {
      if (generationSequence === assemblyGenerationSequence) {
        setAssemblyStatus(error.message || "配件清单生成失败");
      }
    }
    updateAssemblyGenerateState();
  });

  body.querySelectorAll("[data-order-item]").forEach((row) => syncProductRow(row));
  filterAllProductRows();
  updateRemoveState();
  updateAssemblyGenerateState();

  form.addEventListener("submit", () => {
    body.querySelectorAll("[data-order-item]").forEach((row) => {
      row.querySelectorAll("[data-status-checkbox]").forEach((checkbox) => {
        const target = row.querySelector(`input[type="hidden"][name="${checkbox.dataset.statusTarget}"]`);
        if (target) target.value = checkbox.checked ? "1" : "";
      });
    });
  });
}

initializeOrderEntry();

globalThis.__orderEntryTestApi = {
  applyOrderDeliveryDate,
  filterProductRow,
  populateAssemblyOptions,
  replaceRowsWithAssembly,
  initializeOrderEntry,
  syncProductRow,
};
