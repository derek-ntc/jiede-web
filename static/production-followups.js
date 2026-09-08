(() => {
  const search = document.querySelector("input[data-auto-submit-search]");
  if (search) {
    const form = search.form;
    let timer = null;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => form.submit(), 300);
    });
  }

  document.querySelectorAll("[data-auto-submit-filter]").forEach((filter) => {
    filter.addEventListener("change", () => filter.form.submit());
  });
})();

(() => {
  const form = document.querySelector(".production-followup-form");
  if (!form) return;

  const customer = form.querySelector("[data-production-customer]");
  const search = form.querySelector("[data-production-product-search]");
  const results = form.querySelector("[data-production-product-results]");
  const manualId = form.querySelector("[data-production-manual-id]");
  const drawingNo = form.querySelector("[data-production-drawing-no]");
  const productName = form.querySelector("[data-production-product-name]");
  if (!customer || !search || !results || !manualId || !drawingNo || !productName) {
    return;
  }

  let timer = null;
  let controller = null;
  let products = new Map();

  const clearSelectedProduct = () => {
    manualId.value = "";
    results.selectedIndex = -1;
  };

  const renderProducts = (items) => {
    products = new Map(items.map((item) => [String(item.id), item]));
    results.replaceChildren();
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = items.length ? "选择产品…" : "未找到匹配产品";
    prompt.disabled = !items.length;
    prompt.selected = true;
    results.append(prompt);
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = String(item.id);
      option.textContent = [item.drawing_no, item.product_name, item.specification]
        .filter(Boolean)
        .join(" · ");
      results.append(option);
    });
    results.hidden = false;
  };

  const loadProducts = async () => {
    controller?.abort();
    clearSelectedProduct();
    if (!customer.value) {
      results.hidden = true;
      results.replaceChildren();
      return;
    }
    controller = new AbortController();
    const url = new URL(form.dataset.productsUrl, window.location.origin);
    url.searchParams.set("customer", customer.value);
    url.searchParams.set("q", search.value.trim());
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      renderProducts(Array.isArray(payload.products) ? payload.products : []);
    } catch (error) {
      if (error.name !== "AbortError") {
        results.hidden = true;
        results.replaceChildren();
      }
    }
  };

  const scheduleLoad = () => {
    controller?.abort();
    window.clearTimeout(timer);
    timer = window.setTimeout(loadProducts, 250);
  };

  customer.addEventListener("change", () => {
    clearSelectedProduct();
    scheduleLoad();
  });
  search.addEventListener("input", scheduleLoad);
  results.addEventListener("change", () => {
    const product = products.get(results.value);
    if (!product) {
      clearSelectedProduct();
      return;
    }
    manualId.value = String(product.id);
    drawingNo.value = product.drawing_no;
    productName.value = product.product_name;
  });
  drawingNo.addEventListener("input", clearSelectedProduct);
  productName.addEventListener("input", clearSelectedProduct);
})();

(() => {
  const input = document.querySelector("[data-production-drawing-input]");
  const panel = document.querySelector("[data-selected-production-drawings]");
  if (!input || !panel) return;

  const list = panel.querySelector("ul");
  const syncInputFiles = (files) => {
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
  };
  const renderSelectedFiles = () => {
    const files = Array.from(input.files || []);
    list.replaceChildren();
    panel.hidden = files.length === 0;
    files.forEach((file, index) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      const removeButton = document.createElement("button");
      name.textContent = file.name;
      removeButton.type = "button";
      removeButton.textContent = "删除";
      removeButton.addEventListener("click", () => {
        syncInputFiles(files.filter((_, fileIndex) => fileIndex !== index));
        renderSelectedFiles();
      });
      item.append(name, removeButton);
      list.append(item);
    });
  };
  input.addEventListener("change", renderSelectedFiles);
})();
