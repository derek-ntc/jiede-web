(() => {
  "use strict";

  const canvas = document.querySelector(".reconciliation-signature-canvas");
  const confirmationForm = document.querySelector(".reconciliation-confirm-form");
  if (!canvas || !confirmationForm) return;

  const context = canvas.getContext("2d");
  let drawing = false;
  let hasInk = false;
  let lastPoint = null;

  function resizeCanvas() {
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const bounds = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(bounds.width * ratio));
    canvas.height = Math.max(1, Math.round(bounds.height * ratio));
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.lineWidth = 2;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.strokeStyle = "#111";
    hasInk = false;
    lastPoint = null;
  }

  function point(event) {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }

  canvas.addEventListener("pointerdown", (event) => {
    drawing = true;
    canvas.setPointerCapture(event.pointerId);
    const current = point(event);
    context.beginPath();
    context.moveTo(current.x, current.y);
    lastPoint = current;
    event.preventDefault();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drawing) return;
    const current = point(event);
    if (lastPoint && current.x === lastPoint.x && current.y === lastPoint.y) {
      event.preventDefault();
      return;
    }
    context.lineTo(current.x, current.y);
    context.stroke();
    hasInk = true;
    lastPoint = current;
    event.preventDefault();
  });
  for (const eventName of ["pointerup", "pointercancel"]) {
    canvas.addEventListener(eventName, () => {
      drawing = false;
      lastPoint = null;
    });
  }

  document.querySelector("[data-clear-signature]").addEventListener("click", () => {
    context.clearRect(0, 0, canvas.width, canvas.height);
    hasInk = false;
    lastPoint = null;
    confirmationForm.elements.signature_data.value = "";
  });

  confirmationForm.addEventListener("submit", (event) => {
    if (!hasInk) {
      event.preventDefault();
      window.alert("请先手写签名");
      return;
    }
    if (!window.confirm(confirmationForm.dataset.confirmMessage)) {
      event.preventDefault();
      return;
    }
    confirmationForm.elements.signature_data.value = canvas.toDataURL("image/png");
  });

  const disputeForm = document.querySelector(".reconciliation-dispute-form");
  if (disputeForm) {
    disputeForm.addEventListener("submit", (event) => {
      if (!window.confirm(disputeForm.dataset.confirmMessage)) event.preventDefault();
    });
  }

  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
})();
