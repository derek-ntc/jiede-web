(() => {
  let scanner = null;

  async function lookupProduct(code) {
    const trimmed = (code || "").trim();
    if (!trimmed) return null;
    const response = await fetch(`/admin/inventory/api/product?code=${encodeURIComponent(trimmed)}`);
    if (!response.ok) {
      alert("未找到对应产品");
      return null;
    }
    const data = await response.json();
    return data.product;
  }

  function renderResult(product) {
    const box = document.querySelector("[data-inventory-result]");
    if (!box || !product) return;
    box.hidden = false;
    box.querySelector("[data-result-name]").textContent = product.product_name || "-";
    box.querySelector("[data-result-code]").textContent = `SKU/库存编码：${product.inventory_code || product.sku || "-"}`;
    box.querySelector("[data-result-spec]").textContent = `规格：${product.spec || product.drawing_no || "-"}`;
    box.querySelector("[data-result-stock]").textContent = product.total_stock || 0;
    box.querySelector("[data-result-min-stock]").textContent = product.min_stock || 0;

    const image = box.querySelector("[data-result-image]");
    if (image) {
      image.src = product.image_url || "";
      image.hidden = !product.image_url;
    }

    const locations = box.querySelector("[data-result-locations]");
    locations.innerHTML = "";
    if (product.locations && product.locations.length) {
      product.locations.forEach((item) => {
        const span = document.createElement("span");
        span.className = "inventory-location-pill";
        span.textContent = `${item.name}(${item.code})：${item.quantity}`;
        locations.appendChild(span);
      });
    } else {
      locations.textContent = "暂无库位库存";
    }

    box.querySelector("[data-result-inbound]").href = `/admin/inventory/inbound?manual_id=${product.id}`;
    box.querySelector("[data-result-outbound]").href = `/admin/inventory/outbound?manual_id=${product.id}`;
    box.querySelector("[data-result-flow]").href = `/admin/inventory/transactions?q=${encodeURIComponent(product.inventory_code || product.product_name || "")}`;

    const transactions = box.querySelector("[data-result-transactions]");
    transactions.innerHTML = "";
    (product.recent_transactions || []).forEach((item) => {
      const row = document.createElement("span");
      row.textContent = `${item.created_at} / ${item.type} / ${item.quantity} / ${item.remark || ""}`;
      transactions.appendChild(row);
    });
    if (!transactions.children.length) {
      transactions.textContent = "暂无库存流水";
    }
  }

  async function selectProductByCode(code) {
    const product = await lookupProduct(code);
    if (!product) return;
    const select = document.querySelector("[data-inventory-product-select]");
    if (select) {
      select.value = String(product.id);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    renderResult(product);
  }

  document.querySelectorAll("[data-inventory-lookup-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const product = await lookupProduct(new FormData(form).get("code"));
      renderResult(product);
    });
  });

  document.querySelectorAll("[data-inventory-select-form]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await selectProductByCode(new FormData(form).get("code"));
    });
  });

  async function startScan() {
    if (typeof Html5Qrcode === "undefined") {
      alert("扫码组件还没有加载完成，请稍后再试");
      return;
    }
    if (scanner) return;
    scanner = new Html5Qrcode("inventory-reader");
    await scanner.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 240, height: 240 } },
      async (decodedText) => {
        await selectProductByCode(decodedText);
        if (scanner) {
          await scanner.stop();
          scanner = null;
        }
      }
    );
  }

  async function stopScan() {
    if (!scanner) return;
    await scanner.stop();
    scanner = null;
  }

  document.querySelector("[data-start-scan]")?.addEventListener("click", () => {
    startScan().catch(() => alert("无法打开摄像头，请确认使用 HTTPS 并允许摄像头权限"));
  });
  document.querySelector("[data-stop-scan]")?.addEventListener("click", () => {
    stopScan().catch(() => {});
  });
})();
