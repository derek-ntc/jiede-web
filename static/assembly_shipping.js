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
    selected_manual_ids: form._assemblySelectedIds ?? null,
    overrides: form._assemblyOverrides || Object.fromEntries(
      quantityInputs.map((input) => [input.dataset.manualId, input.value])
    ),
    remarks: form._assemblyRemarks || {},
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
    appendAssemblyCell(row, "规格型号", item.specification || "-");
    appendAssemblyCell(row, "来源", item.source_kind === "extra" ? "临时追加" : "BOM");
    appendAssemblyCell(row, "每套用量", item.source_kind === "extra" ? "—" : String(item.quantity_per_set));
    appendAssemblyCell(row, "计算数量", item.source_kind === "extra" ? "—" : String(item.calculated_quantity));
    const quantityCell = document.createElement("td");
    quantityCell.dataset.label = "实际发货数量";
    const manualId = document.createElement("input");
    manualId.type = "hidden";
    manualId.name = "manual_id";
    manualId.value = String(item.manual_id);
    const selectedId = document.createElement("input");
    selectedId.type = "hidden";
    selectedId.name = "selected_manual_ids";
    selectedId.value = String(item.manual_id);
    const quantity = document.createElement("input");
    quantity.type = "number";
    quantity.name = "shipped_quantity";
    quantity.min = "0";
    quantity.max = "2147483647";
    quantity.step = "1";
    quantity.required = true;
    quantity.value = String(item.shipped_quantity);
    quantity.readOnly = form.dataset.assemblyFinanceClaimed === "1";
    quantity.dataset.assemblyItemQuantity = "";
    quantity.dataset.manualId = String(item.manual_id);
    quantity.setAttribute("aria-label", `${item.drawing_no || "配件"} 实际发货数量`);
    quantityCell.append(manualId, selectedId, quantity);
    row.appendChild(quantityCell);
    const remarkCell = document.createElement("td");
    remarkCell.dataset.label = "本行备注";
    const remark = document.createElement("input");
    remark.type = "text";
    remark.name = "line_remark";
    remark.maxLength = 500;
    remark.value = item.remark || "";
    remark.readOnly = form.dataset.assemblyFinanceClaimed === "1";
    remark.dataset.assemblyItemRemark = "";
    remark.dataset.manualId = String(item.manual_id);
    remark.setAttribute("aria-label", `${item.drawing_no || "配件"} 本行备注`);
    remarkCell.appendChild(remark);
    row.appendChild(remarkCell);
    const allocationText = item.allocations.map((allocation) => (
      allocation.order_id === null
        ? `无订单直接发货：${allocation.quantity}`
        : `${allocation.order_no || `订单 ${allocation.order_id}`}：${allocation.quantity}`
    )).join("；");
    appendAssemblyCell(row, "订单分配", item.shipped_quantity === 0 ? "本次不发" : allocationText || "-");
    appendAssemblyCell(
      row,
      "库存状态",
      item.shipped_quantity === 0 ? `本次不发，不扣库存（可用 ${item.available_inventory}）` : item.inventory_shortage_quantity > 0
        ? `可用 ${item.available_inventory}，库存缺口 ${item.inventory_shortage_quantity}`
        : `可用库存 ${item.available_inventory}`
    );
    const actions = document.createElement("td");
    actions.dataset.label = "本次调整";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除";
    remove.dataset.assemblyRemoveItem = String(item.manual_id);
    remove.disabled = form.dataset.assemblyFinanceClaimed === "1";
    remove.addEventListener("click", () => form._assemblyRemoveItem?.(String(item.manual_id)));
    actions.appendChild(remove);
    row.appendChild(actions);
    return row;
  });
  form.querySelector("[data-assembly-preview]").replaceChildren(...rows);
  form._assemblyOverrides = Object.fromEntries(
    preview.items.map((item) => [String(item.manual_id), String(item.shipped_quantity)])
  );
  form._assemblyRemarks = Object.fromEntries(
    preview.items.map((item) => [String(item.manual_id), item.remark || ""])
  );
  form._assemblySelectedIds = preview.items.map((item) => String(item.manual_id));
  form._assemblyItems = preview.items;
  renderAssemblyWarnings(form.querySelector("[data-assembly-warning-summary]"), preview.warnings || []);
  form.querySelector("[data-assembly-submit]").disabled = !preview.items.some((item) => item.shipped_quantity > 0);
}

async function submitAssemblyShipment(form, preview, confirmed) {
  const payload = new FormData(form);
  // The marker distinguishes an explicitly empty selection from a legacy client.
  payload.set("assembly_selection_explicit", "1");
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
  cell.colSpan = 11;
  cell.className = "empty";
  cell.textContent = form._assemblySelectedIds?.length === 0
    ? "本次明细为空，请搜索追加至少一个配件。" : "暂无组装发货预览";
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
  const allowedPath = redirect.pathname === "/admin/shipped-orders" ||
    /^\/admin\/delivery-notes\/operations\/[1-9]\d*$/.test(redirect.pathname);
  if (redirect.origin !== window.location.origin || !allowedPath) {
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
  let candidateController = null;
  let candidateGeneration = 0;
  let candidateTimer = null;
  const search = form.querySelector("[data-assembly-component-search]");
  const candidates = form.querySelector("[data-assembly-component-results]");
  const locked = form.dataset.assemblyFinanceClaimed === "1";
  form._assemblySelectedIds = null;

  const cancelCandidates = () => {
    candidateGeneration += 1;
    window.clearTimeout(candidateTimer);
    candidateController?.abort();
    candidateController = null;
    candidates?.replaceChildren();
  };

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
    if (form._assemblySelectedIds?.length === 0) {
      clearAssemblyPreview(form, "请追加至少一个配件后保存。");
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
      form._assemblyPreview = null;
      submitButton.disabled = true;
      renderAssemblyWarnings(form.querySelector("[data-assembly-warning-summary]"), []);
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
    form._assemblyPreview = null;
    submitButton.disabled = true;
    renderAssemblyWarnings(form.querySelector("[data-assembly-warning-summary]"), []);
    setAssemblyStatus(form, "正在等待输入完成后更新预览…");
    previewTimer = window.setTimeout(() => refreshPreview(generation), 300);
  };

  form._assemblyRemoveItem = (manualId) => {
    if (locked || submitPending) return;
    form._assemblySelectedIds = (form._assemblySelectedIds || []).filter((id) => id !== manualId);
    delete form._assemblyOverrides[manualId];
    delete form._assemblyRemarks[manualId];
    schedulePreview();
  };

  form.querySelector("[data-assembly-recalculate]")?.addEventListener("click", () => {
    if (locked || submitPending) return;
    for (const item of form._assemblyItems || []) {
      if (item.source_kind !== "extra" && form._assemblySelectedIds?.includes(String(item.manual_id))) {
        form._assemblyOverrides[String(item.manual_id)] = String(Number(sets.value) * item.quantity_per_set);
      }
    }
    schedulePreview();
  });

  search?.addEventListener("input", () => {
    cancelCandidates();
    if (locked || submitPending || !customer.value.trim() || !drawing.value.trim()) return;
    const generation = candidateGeneration;
    const selectedCustomer = customer.value.trim();
    const selectedDrawing = drawing.value.trim();
    candidateTimer = window.setTimeout(async () => {
      const controller = new AbortController();
      candidateController = controller;
      try {
        const response = await fetch(`/admin/shipped-orders/component-options?customer=${encodeURIComponent(selectedCustomer)}&q=${encodeURIComponent(search.value.trim())}`, {signal: controller.signal});
        const body = await response.json();
        if (generation !== candidateGeneration || customer.value.trim() !== selectedCustomer || drawing.value.trim() !== selectedDrawing) return;
        if (!response.ok) throw new Error(body.error || "加载配件失败");
        const buttons = body.items.map((item) => {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.assemblyAddItem = String(item.manual_id);
          const alreadySelected = form._assemblySelectedIds?.includes(String(item.manual_id));
          button.textContent = `${alreadySelected ? "合并 +1" : "追加"} ${item.drawing_no || "—"} / ${item.product_name || "—"} / ${item.specification || "—"}`;
          button.addEventListener("click", () => {
            if (locked || submitPending || generation !== candidateGeneration || form._assemblySelectedIds === null) return;
            const id = String(item.manual_id);
            if (form._assemblySelectedIds.includes(id)) {
              const current = Number(form._assemblyOverrides[id]);
              if (!Number.isInteger(current) || current < 0 || current >= 2147483647) {
                setAssemblyStatus(form, "请先填写有效数量，再合并追加。", true);
                return;
              }
              form._assemblyOverrides[id] = String(current + 1);
            } else {
              form._assemblySelectedIds.push(id);
              form._assemblyOverrides[id] = "1";
              form._assemblyRemarks[id] = "";
            }
            cancelCandidates();
            schedulePreview();
          });
          return button;
        });
        candidates.replaceChildren(...buttons);
      } catch (error) {
        if (generation === candidateGeneration && error.name !== "AbortError") setAssemblyStatus(form, error.message || "加载配件失败", true);
      }
    }, 250);
  });

  customer.addEventListener("change", async () => {
    const recipientFields = form.querySelector('[data-assembly-recipient-fields]');
    if (recipientFields) {
      const defaults = JSON.parse(form.querySelector('[data-assembly-recipient-defaults]').textContent);
      const recipient = defaults[customer.value.trim()] || {};
      recipientFields.hidden = !customer.value.trim();
      ['recipient_name', 'recipient_phone', 'address'].forEach(key => {
        recipientFields.querySelector(`[name="${key}"]`).value = recipient[key] || '';
      });
    }
    const generation = ++optionsGeneration;
    optionsController?.abort();
    optionsController = new AbortController();
    cancelPreview();
    cancelCandidates();
    if (search) search.value = "";
    form._assemblySelectedIds = null;
    form._assemblyItems = [];
    form._assemblyOverrides = {};
    form._assemblyRemarks = {};
    clearAssemblyPreview(form, "已重置本次删减和追加，正在加载该客户的组装件图号…");
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
    cancelCandidates();
    if (search) search.value = "";
    form._assemblySelectedIds = null;
    form._assemblyItems = [];
    form._assemblyOverrides = {};
    form._assemblyRemarks = {};
    clearAssemblyPreview(form);
    schedulePreview();
    setAssemblyStatus(form, "已重置本次删减和追加，正在按组装件配置预览…");
  });
  sets.addEventListener("input", schedulePreview);
  form.addEventListener("input", (event) => {
    const isQuantity = event.target.matches("[data-assembly-item-quantity]");
    const isRemark = event.target.matches("[data-assembly-item-remark]");
    if (!isQuantity && !isRemark) return;
    if (locked || submitPending || !form._assemblySelectedIds?.includes(event.target.dataset.manualId)) return;
    const values = isQuantity ? form._assemblyOverrides : form._assemblyRemarks;
    values[event.target.dataset.manualId] = event.target.value;
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
