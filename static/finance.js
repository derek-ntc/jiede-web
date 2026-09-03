(() => {
  document.querySelectorAll("[data-finance-source-form]").forEach((form) => {
    const sources = Array.from(form.querySelectorAll("[data-finance-source]"));
    const submit = form.querySelector("[data-finance-submit]");
    const summary = form.querySelector("[data-finance-selection-summary]");

    function formatMinor(totalMinor, currency) {
      const digits = currency === "JPY" ? 0 : 2;
      return `${(totalMinor / (10 ** digits)).toFixed(digits)} ${currency}`;
    }

    function update() {
      const selected = sources.filter((source) => source.checked);
      const currencies = new Set(selected.map((source) => source.dataset.currency));
      const currency = selected[0]?.dataset.currency || "";
      const totalMinor = selected.reduce(
        (sum, source) => sum + Number(source.dataset.totalMinor || 0),
        0,
      );
      sources.forEach((source) => {
        source.disabled = currencies.size === 1
          && !source.checked
          && source.dataset.currency !== currency;
      });
      if (submit) submit.disabled = selected.length === 0 || currencies.size > 1;
      if (summary) {
        summary.textContent = selected.length
          ? `已选 ${selected.length} 项，合计 ${formatMinor(totalMinor, currency)}`
          : "尚未选择发货明细";
      }
    }

    sources.forEach((source) => source.addEventListener("change", update));
    update();
  });
})();
