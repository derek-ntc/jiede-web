const MAX_ORDER_QUANTITY = 2147483647;

function normalizeOrderEntryValue(value) {
  return (value || "").trim().toLowerCase();
}

function optionMatchesCustomer(option, customer) {
  const names = option?.dataset.customers ? JSON.parse(option.dataset.customers) : [option?.dataset.customer || ""];
  return names.some((name) => normalizeOrderEntryValue(name) === customer);
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
  if (customerCell) customerCell.textContent = productId ? row._selectedCustomer || option?.dataset.customer || "-" : "-";
}

function filterProductRow(row, customerQuery) {
  const customer = normalizeOrderEntryValue(customerQuery);
  const selects = productSelectsForRow(row);
  const selected = selects.find((select) => select.value)?.selectedOptions?.[0];
  row._selectedCustomer = customerQuery;
  const selectedStillVisible = !customer || optionMatchesCustomer(selected, customer);

  selects.forEach((select) => {
    Array.from(select.options).forEach((option) => {
      const matches = !option.value || !customer ||
        optionMatchesCustomer(option, customer);
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
  const assemblyLabel = row.querySelector("[data-item-assembly-label]");
  if (assemblyLabel) assemblyLabel.textContent = "";
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
    const quantity = Number(item.quantity_per_set) * Number(item.assembly_set_quantity ?? setQuantity);
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
    const assemblyField = row.querySelector('[name="item_assembly_drawing_no"]');
    if (assemblyField) assemblyField.value = item.assembly_drawing_no || "";
    const assemblyLabel = row.querySelector("[data-item-assembly-label]");
    if (assemblyLabel) assemblyLabel.textContent = item.assembly_drawing_no
      ? `${item.assembly_drawing_no} · ${item.assembly_set_quantity} 套` : "";
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
  const assemblyRows = form.querySelector("[data-order-assemblies]");
  const addAssemblyButton = form.querySelector("[data-add-assembly]");
  let availableAssemblies = [];
  let generatedSelection = null;
  const controls = () => assemblyRows
    ? Array.from(assemblyRows.querySelectorAll("[data-order-assembly-row]")).map((row) => ({
        row,
        drawing: row.querySelector("[data-order-assembly-drawing]"),
        quantity: row.querySelector("[data-order-assembly-quantity]"),
      }))
    : [{drawing: assemblyDrawing, quantity: assemblyQuantity}];
  const selection = () => controls().map(({drawing, quantity}) => ({
    drawing: drawing?.value || "", quantity: quantity?.value || "",
  }));
  const selectionKey = () => JSON.stringify([customerFilter?.value, selection()]);
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
    const rows = controls();
    rows.forEach(({row, drawing, quantity}) => {
      if (quantity) {
        quantity.disabled = !drawing?.value;
        if (quantity.disabled) quantity.value = "";
      }
      const remove = row?.querySelector("[data-remove-assembly]");
      if (remove) remove.disabled = rows.length === 1;
    });
    if (addAssemblyButton) addAssemblyButton.disabled = availableAssemblies.length === 0;
    if (generateAssemblyButton) {
      generateAssemblyButton.disabled = !(customerFilter?.value && rows.every(({drawing, quantity}) =>
        drawing?.value && Number.isInteger(Number(quantity?.value)) &&
        Number(quantity?.value) > 0 && Number(quantity?.value) <= MAX_ORDER_QUANTITY
      ));
    }
  }

  async function loadAssemblyOptions() {
    const drawingSelect = controls()[0]?.drawing;
    if (!drawingSelect) return;
    const requestSequence = ++assemblyRequestSequence;
    availableAssemblies = [];
    controls().slice(1).forEach(({row}) => row.remove());
    populateAssemblyOptions(drawingSelect, []);
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
      availableAssemblies = payload.assembly_drawing_numbers || [];
      populateAssemblyOptions(drawingSelect, availableAssemblies);
      setAssemblyStatus(
        payload.assembly_drawing_numbers?.length
          ? "请选择组装图号并填写组装数量。"
          : "该客户尚未配置组装图号，可继续手工添加产品。",
      );
    } catch (error) {
      if (requestSequence !== assemblyRequestSequence) return;
      populateAssemblyOptions(drawingSelect, []);
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
  function assemblyChanged() {
    assemblyGenerationSequence += 1;
    updateAssemblyGenerateState();
    setAssemblyStatus("填写各组装图号的数量后，重新生成配件清单。");
  }
  if (assemblyRows) {
    assemblyRows.addEventListener("change", assemblyChanged);
    assemblyRows.addEventListener("input", assemblyChanged);
    assemblyRows.addEventListener("click", (event) => {
      if (!event.target.matches("[data-remove-assembly]")) return;
      event.target.closest("[data-order-assembly-row]").remove();
      assemblyChanged();
    });
    addAssemblyButton?.addEventListener("click", () => {
      const row = controls()[0].row.cloneNode(true);
      populateAssemblyOptions(row.querySelector("[data-order-assembly-drawing]"), availableAssemblies);
      row.querySelector("[data-order-assembly-quantity]").value = "";
      assemblyRows.appendChild(row);
      assemblyChanged();
    });
  } else {
    assemblyDrawing?.addEventListener("change", assemblyChanged);
    assemblyQuantity?.addEventListener("input", assemblyChanged);
    assemblyQuantity?.addEventListener("change", assemblyChanged);
  }
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
    const generationSelections = selection();
    const generationKey = selectionKey();
    if (!generationCustomer || generationSelections.some(({drawing, quantity}) =>
      !drawing || !Number.isInteger(Number(quantity)) || Number(quantity) <= 0 || Number(quantity) > MAX_ORDER_QUANTITY
    )) {
      setAssemblyStatus(`请选择客户、组装图号，并填写 1 到 ${MAX_ORDER_QUANTITY} 之间的整数套数。`);
      return;
    }
    if (new Set(generationSelections.map(({drawing}) => drawing.toLowerCase())).size !== generationSelections.length) {
      setAssemblyStatus("组装图号不能重复，请合并同一图号的组装数量。");
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
      const assemblies = await Promise.all(generationSelections.map(async ({drawing, quantity}) => {
        const params = new URLSearchParams({customer: generationCustomer, assembly_drawing_no: drawing});
        const response = await fetch(`${assemblyDefinitionUrl}?${params}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "配件清单生成失败");
        if (!payload.items?.length) throw new Error(`${drawing} 没有配置配件`);
        return payload.items.map((item) => ({...item,
          assembly_drawing_no: payload.assembly_drawing_no,
          assembly_set_quantity: Number(quantity),
        }));
      }));
      if (generationSequence !== assemblyGenerationSequence || selectionKey() !== generationKey) {
        setAssemblyStatus("选择已改变，请按当前客户、图号和套数重新生成配件清单。");
        return;
      }
      const items = assemblies.flat();
      replaceRowsWithAssembly(body, rowTemplate, items, 1, deliveryDate?.value || "", generationCustomer);
      generatedSelection = generationKey;
      updateRemoveState();
      setAssemblyStatus(`已生成 ${assemblies.length} 个组装图号、${items.length} 行配件，可继续修改各行数量。`);
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

  form.addEventListener("submit", (event) => {
    if (generatedSelection !== null && generatedSelection !== selectionKey() && hasMeaningfulOrderRows(body)) {
      event.preventDefault();
      setAssemblyStatus("组装选择或数量已改变，请重新生成配件清单后保存。");
      return;
    }
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
