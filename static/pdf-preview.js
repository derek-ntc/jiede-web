import * as pdfjsLib from "/static/vendor/pdfjs/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.mjs";

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;

const dialog = document.querySelector("[data-pdf-preview-dialog]");

if (dialog) {
  const title = dialog.querySelector("[data-pdf-preview-title]");
  const status = dialog.querySelector("[data-pdf-preview-status]");
  const pages = dialog.querySelector("[data-pdf-preview-pages]");
  const zoomOutput = dialog.querySelector("[data-pdf-preview-zoom]");
  const zoomOut = dialog.querySelector("[data-pdf-preview-zoom-out]");
  const zoomIn = dialog.querySelector("[data-pdf-preview-zoom-in]");
  const printButton = dialog.querySelector("[data-pdf-preview-print]");
  const closeButtons = dialog.querySelectorAll("[data-pdf-preview-close]");

  let pdfDocument = null;
  let loadingTask = null;
  let renderTasks = [];
  let currentUrl = "";
  let zoom = 1;
  let renderVersion = 0;
  let previousFocus = null;

  function clampZoom(value) {
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
  }

  function updateToolbar() {
    zoomOutput.value = `${Math.round(zoom * 100)}%`;
    zoomOutput.textContent = zoomOutput.value;
    zoomOut.disabled = zoom <= MIN_ZOOM;
    zoomIn.disabled = zoom >= MAX_ZOOM;
    printButton.disabled = !pdfDocument;
  }

  function showStatus(message) {
    status.textContent = message;
    status.hidden = false;
  }

  function cancelRendering() {
    renderVersion += 1;
    for (const task of renderTasks) {
      task.cancel();
    }
    renderTasks = [];
  }

  async function renderPdf() {
    if (!pdfDocument) return;

    cancelRendering();
    const version = renderVersion;
    pages.replaceChildren();
    showStatus("正在生成预览…");
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);

    try {
      for (let pageNumber = 1; pageNumber <= pdfDocument.numPages; pageNumber += 1) {
        if (version !== renderVersion) return;

        const page = await pdfDocument.getPage(pageNumber);
        const viewport = page.getViewport({ scale: zoom });
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        canvas.setAttribute("aria-label", `第 ${pageNumber} 页`);
        pages.append(canvas);

        const renderTask = page.render({
          canvasContext: context,
          viewport,
          transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        renderTasks.push(renderTask);
        await renderTask.promise;
        renderTasks = renderTasks.filter((task) => task !== renderTask);
      }

      if (version === renderVersion) status.hidden = true;
    } catch (error) {
      if (error?.name !== "RenderingCancelledException" && version === renderVersion) {
        showStatus("PDF预览生成失败，请关闭后重试。");
        console.error("PDF render failed", error);
      }
    }
  }

  async function openPdf(url, name) {
    previousFocus = document.activeElement;
    currentUrl = url;
    title.textContent = name || "PDF预览";
    dialog.hidden = false;
    document.body.classList.add("pdf-preview-open");
    pdfDocument = null;
    pages.replaceChildren();
    showStatus("正在加载PDF…");
    updateToolbar();
    dialog.querySelector("[data-pdf-preview-close]").focus();

    if (loadingTask) {
      await loadingTask.destroy().catch(() => {});
    }

    try {
      loadingTask = pdfjsLib.getDocument({ url, withCredentials: true });
      const loadedDocument = await loadingTask.promise;
      if (dialog.hidden || url !== currentUrl) {
        await loadedDocument.destroy();
        return;
      }

      pdfDocument = loadedDocument;
      const firstPage = await pdfDocument.getPage(1);
      const naturalViewport = firstPage.getViewport({ scale: 1 });
      const availableWidth = Math.max(1, pages.parentElement.clientWidth - 36);
      zoom = clampZoom(Math.min(1, availableWidth / naturalViewport.width));
      updateToolbar();
      await renderPdf();
    } catch (error) {
      if (!dialog.hidden && url === currentUrl) {
        showStatus("PDF加载失败，请检查文件是否完整或稍后重试。");
        console.error("PDF load failed", error);
      }
    }
  }

  async function closePdf() {
    dialog.hidden = true;
    document.body.classList.remove("pdf-preview-open");
    currentUrl = "";
    cancelRendering();
    pages.replaceChildren();
    if (loadingTask) {
      await loadingTask.destroy().catch(() => {});
      loadingTask = null;
    }
    pdfDocument = null;
    updateToolbar();
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  function changeZoom(delta) {
    if (!pdfDocument) return;
    const nextZoom = clampZoom(zoom + delta);
    if (nextZoom === zoom) return;
    zoom = nextZoom;
    updateToolbar();
    renderPdf();
  }

  function printPdf() {
    if (!currentUrl || !pdfDocument) return;
    document.body.classList.add("pdf-preview-printing");
    try {
      window.print();
    } finally {
      document.body.classList.remove("pdf-preview-printing");
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-pdf-preview-url]");
    if (!trigger) return;
    event.preventDefault();
    openPdf(trigger.dataset.pdfPreviewUrl, trigger.dataset.pdfPreviewName);
  });

  for (const button of closeButtons) button.addEventListener("click", closePdf);
  zoomOut.addEventListener("click", () => changeZoom(-ZOOM_STEP));
  zoomIn.addEventListener("click", () => changeZoom(ZOOM_STEP));
  printButton.addEventListener("click", printPdf);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dialog.hidden) closePdf();
  });

  updateToolbar();
}
