function initializeAssemblyComponentEditors(root = document) {
  root.querySelectorAll("[data-assembly-component-editor]").forEach((editor) => {
    const rows = editor.querySelector("[data-assembly-component-rows]");
    const template = editor.querySelector("[data-empty-assembly-component-row]");
    const addButton = editor.querySelector("[data-add-assembly-component-row]");

    function clearRow(row) {
      row.querySelectorAll("input").forEach((input) => { input.value = ""; });
    }
    function visibleRows() {
      return Array.from(rows.querySelectorAll("tr")).filter((row) => !row.hidden);
    }
    function bindRemove(row) {
      const button = row.querySelector("[data-remove-assembly-component-row]");
      if (!button) return;
      button.addEventListener("click", () => {
        if (visibleRows().length <= 1) clearRow(row);
        else row.remove();
      });
    }

    visibleRows().forEach(bindRemove);
    addButton?.addEventListener("click", () => {
      const clone = template.cloneNode(true);
      clone.hidden = false;
      clone.removeAttribute("data-empty-assembly-component-row");
      clone.classList.add("assembly-component-row");
      clearRow(clone);
      bindRemove(clone);
      rows.appendChild(clone);
    });
  });
}

async function loadAssemblyOptions(customer, signal) {
  const response = await fetch(
    `/admin/shipped-orders/assembly-options?customer=${encodeURIComponent(customer)}`,
    {signal}
  );
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "加载组装件图号失败");
  return body.assembly_drawing_numbers;
}

async function requestAssemblyPreview(form, signal) {
  const quantityInputs = Array.from(form.querySelectorAll("[data-assembly-item-quantity]"));
  const payload = {
    customer: form.querySelector("[data-assembly-customer]").value,
    assembly_drawing_no: form.querySelector("[data-assembly-drawing]").value,
    set_quantity: form.querySelector("[data-assembly-set-quantity]").value,
    overrides: quantityInputs.length
      ? Object.fromEntries(quantityInputs.map((input) => [input.dataset.manualId, input.value]))
      : (form._assemblyOverrides || {}),
  };
  const response = await fetch(
    form.dataset.assemblyPreviewUrl || "/admin/shipped-orders/assembly-preview",
    {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
    signal,
    }
  );
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "组装发货预览失败");
  return body;
}

function renderAssemblyWarnings(container, warnings) {
  container.replaceChildren(...warnings.map((warning) => {
    const item = document.createElement("p");
    item.className = "assembly-warning";
    item.textContent = warning.message;
    return item;
  }));
}

function appendAssemblyCell(row, label, value) {
  const cell = document.createElement("td");
  cell.dataset.label = label;
  cell.textContent = value;
  row.appendChild(cell);
}

function renderAssemblyPreview(form, preview) {
  const rows = preview.items.map((item) => {
    const row = document.createElement("tr");
    row.className = "assembly-preview-item";
    appendAssemblyCell(row, "配件图号", item.drawing_no || "-");
    appendAssemblyCell(row, "产品名称", item.product_name || "-");
    appendAssemblyCell(row, "每套用量", String(item.quantity_per_set));
    appendAssemblyCell(row, "计算数量", String(item.calculated_quantity));
    const quantityCell = document.createElement("td");
    quantityCell.dataset.label = "实际发货数量";
    const manualId = document.createElement("input");
    manualId.type = "hidden";
    manualId.name = "manual_id";
    manualId.value = String(item.manual_id);
    const quantity = document.createElement("input");
    quantity.type = "number";
    quantity.name = "shipped_quantity";
    quantity.min = "1";
    quantity.step = "1";
    quantity.required = true;
    quantity.value = String(item.shipped_quantity);
    quantity.dataset.assemblyItemQuantity = "";
    quantity.dataset.manualId = String(item.manual_id);
    quantity.setAttribute("aria-label", `${item.drawing_no || "配件"} 实际发货数量`);
    quantityCell.append(manualId, quantity);
    row.appendChild(quantityCell);
    const allocationText = item.allocations.map((allocation) => (
      allocation.order_id === null
        ? `无订单直接发货：${allocation.quantity}`
        : `${allocation.order_no || `订单 ${allocation.order_id}`}：${allocation.quantity}`
    )).join("；");
    appendAssemblyCell(row, "订单分配", allocationText || "-");
    appendAssemblyCell(
      row,
      "库存状态",
      item.inventory_shortage_quantity > 0
        ? `可用 ${item.available_inventory}，库存缺口 ${item.inventory_shortage_quantity}`
        : `可用库存 ${item.available_inventory}`
    );
    return row;
  });
  form.querySelector("[data-assembly-preview]").replaceChildren(...rows);
  form._assemblyOverrides = Object.fromEntries(
    preview.items.map((item) => [String(item.manual_id), String(item.shipped_quantity)])
  );
  renderAssemblyWarnings(form.querySelector("[data-assembly-warning-summary]"), preview.warnings || []);
  form.querySelector("[data-assembly-submit]").disabled = rows.length === 0;
}

async function submitAssemblyShipment(form, preview, confirmed) {
  const payload = new FormData(form);
  payload.set("preview_token", preview.preview_token);
  payload.set("confirm_warnings", confirmed ? "1" : "0");
  const response = await fetch(form.action, {method: "POST", body: payload});
  return {status: response.status, body: await response.json()};
}

function setAssemblyStatus(form, message, isError = false) {
  const status = form.querySelector("[data-assembly-status]");
  status.textContent = message;
  status.classList.toggle("assembly-status-error", isError);
}

function setAssemblyLoading(form, loading) {
  form.querySelector("[data-assembly-submit]").dataset.loading = String(loading);
}

function clearAssemblyPreview(form, message) {
  form._assemblyPreview = null;
  const empty = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = 7;
  cell.className = "empty";
  cell.textContent = "暂无组装发货预览";
  empty.appendChild(cell);
  form.querySelector("[data-assembly-preview]").replaceChildren(empty);
  renderAssemblyWarnings(form.querySelector("[data-assembly-warning-summary]"), []);
  form.querySelector("[data-assembly-submit]").disabled = true;
  if (message) setAssemblyStatus(form, message);
}

function populateAssemblyDrawings(form, drawings) {
  const drawing = form.querySelector("[data-assembly-drawing]");
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = drawings.length ? "请选择组装件图号" : "该客户没有可用的组装件配置";
  const options = drawings.map((drawingNo) => {
    const option = document.createElement("option");
    option.value = drawingNo;
    option.textContent = drawingNo;
    return option;
  });
  drawing.replaceChildren(placeholder, ...options);
  drawing.disabled = drawings.length === 0;
}

function sameOriginShippedOrdersRedirect(value) {
  if (typeof value !== "string" || !value) throw new Error("保存响应缺少跳转地址");
  const redirect = new URL(value, window.location.origin);
  if (redirect.origin !== window.location.origin || redirect.pathname !== "/admin/shipped-orders") {
    throw new Error("保存响应包含无效的跳转地址");
  }
  return redirect.href;
}

function initializeAssemblyShipmentForm(form) {
  const customer = form.querySelector("[data-assembly-customer]");
  const drawing = form.querySelector("[data-assembly-drawing]");
  const sets = form.querySelector("[data-assembly-set-quantity]");
  const submitButton = form.querySelector("[data-assembly-submit]");
  let previewTimer = null;
  let previewGeneration = 0;
  let optionsGeneration = 0;
  let previewPending = false;
  let submitPending = false;
  let optionsController = null;
  let previewController = null;

  const initialPreviewNode = form.querySelector("[data-assembly-initial-preview]");
  if (initialPreviewNode?.textContent) {
    try {
      const initialPreview = JSON.parse(initialPreviewNode.textContent);
      form._assemblyPreview = initialPreview;
      renderAssemblyPreview(form, initialPreview);
    } catch (error) {
      clearAssemblyPreview(form, "组装发货初始预览无效。");
      setAssemblyStatus(form, "组装发货初始预览无效。", true);
    }
  }

  const cancelPreview = () => {
    previewGeneration += 1;
    window.clearTimeout(previewTimer);
    previewController?.abort();
    previewController = null;
    previewPending = false;
    setAssemblyLoading(form, false);
    return previewGeneration;
  };

  const refreshPreview = async (generation = cancelPreview()) => {
    const customerValue = customer.value.trim();
    const drawingValue = drawing.value.trim();
    const setValue = sets.value.trim();
    if (!customerValue || !drawingValue || !setValue) {
      clearAssemblyPreview(form, "请选择客户、组装件图号和套数后预览。");
      return;
    }
    const controller = new AbortController();
    previewController = controller;
    previewPending = true;
    submitButton.disabled = true;
    setAssemblyLoading(form, true);
    setAssemblyStatus(form, "正在更新组装发货预览…");
    try {
      const preview = await requestAssemblyPreview(form, controller.signal);
      if (generation !== previewGeneration || controller !== previewController) return;
      form._assemblyPreview = preview;
      renderAssemblyPreview(form, preview);
      setAssemblyStatus(form, preview.warnings.length ? "请核对下方警告后保存。" : "预览已更新，可以保存。");
    } catch (error) {
      if (generation !== previewGeneration || controller !== previewController || error.name === "AbortError") return;
      clearAssemblyPreview(form, error.message || "组装发货预览失败。");
      setAssemblyStatus(form, error.message || "组装发货预览失败。", true);
    } finally {
      if (generation === previewGeneration && controller === previewController) {
        previewController = null;
        previewPending = false;
        setAssemblyLoading(form, false);
      }
    }
  };

  const schedulePreview = () => {
    const generation = cancelPreview();
    clearAssemblyPreview(form, "正在等待输入完成后更新预览…");
    previewTimer = window.setTimeout(() => refreshPreview(generation), 300);
  };

  customer.addEventListener("change", async () => {
    const generation = ++optionsGeneration;
    optionsController?.abort();
    optionsController = new AbortController();
    cancelPreview();
    form._assemblyOverrides = {};
    clearAssemblyPreview(form, "正在加载该客户的组装件图号…");
    populateAssemblyDrawings(form, []);
    const selectedCustomer = customer.value.trim();
    if (!selectedCustomer) return;
    try {
      const drawings = await loadAssemblyOptions(selectedCustomer, optionsController.signal);
      if (generation !== optionsGeneration || customer.value.trim() !== selectedCustomer) return;
      populateAssemblyDrawings(form, drawings);
      setAssemblyStatus(form, drawings.length ? "请选择组装件图号和套数后预览。" : "该客户没有可用的组装件配置。");
    } catch (error) {
      if (generation !== optionsGeneration || error.name === "AbortError") return;
      populateAssemblyDrawings(form, []);
      setAssemblyStatus(form, error.message || "加载组装件图号失败。", true);
    } finally {
      if (generation === optionsGeneration) optionsController = null;
    }
  });
  drawing.addEventListener("change", () => {
    form._assemblyOverrides = {};
    schedulePreview();
  });
  sets.addEventListener("input", schedulePreview);
  form.addEventListener("input", (event) => {
    if (!event.target.matches("[data-assembly-item-quantity]")) return;
    form._assemblyOverrides = Object.fromEntries(
      Array.from(form.querySelectorAll("[data-assembly-item-quantity]"))
        .map((input) => [input.dataset.manualId, input.value])
    );
    schedulePreview();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitPending || previewPending) return;
    const preview = form._assemblyPreview;
    if (!preview) {
      await refreshPreview(cancelPreview());
      return;
    }
    const warnings = preview.warnings || [];
    const confirmed = warnings.length === 0 || window.confirm(
      `请确认以下组装发货警告：\n${warnings.map((warning) => warning.message).join("\n")}`
    );
    if (!confirmed) {
      setAssemblyStatus(form, "已取消保存，请修改后重新确认。");
      return;
    }
    submitPending = true;
    submitButton.disabled = true;
    setAssemblyLoading(form, true);
    setAssemblyStatus(form, "正在保存组装发货…");
    try {
      const result = await submitAssemblyShipment(form, preview, confirmed);
      if (result.status === 409) {
        if (result.body.preview) {
          form._assemblyPreview = result.body.preview;
          renderAssemblyPreview(form, result.body.preview);
        } else clearAssemblyPreview(form);
        setAssemblyStatus(form, result.body.error || "订单或库存状态已变化，请按最新结果重新确认。", true);
        return;
      }
      if (result.status === 201) {
        window.location.assign(sameOriginShippedOrdersRedirect(result.body.redirect_url));
        return;
      }
      throw new Error(result.body.error || "组装发货保存失败。");
    } catch (error) {
      setAssemblyStatus(form, error.message || "组装发货保存失败。", true);
      submitButton.disabled = !form._assemblyPreview;
    } finally {
      submitPending = false;
      setAssemblyLoading(form, false);
    }
  });
}

function initializeShipmentModes(root = document) {
  root.querySelectorAll("[data-shipment-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.shipmentMode;
      root.querySelectorAll("[data-shipment-mode]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      root.querySelectorAll("[data-shipment-mode-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.shipmentModePanel !== mode;
      });
    });
  });
}

initializeAssemblyComponentEditors();
document.querySelectorAll("[data-assembly-shipment-form]").forEach(initializeAssemblyShipmentForm);
initializeShipmentModes();

globalThis.__assemblyShippingTestApi = {
  initializeAssemblyComponentEditors,
  initializeAssemblyShipmentForm,
};
