document.querySelectorAll("[data-editor]").forEach((shell) => {
  const editor = shell.querySelector(".rich-editor");
  const hidden = shell.querySelector("textarea[name='description_html']");
  const imageInput = shell.querySelector("[data-editor-image-input]");
  const form = shell.closest("form");
  const maxUploadMb = Number(shell.dataset.maxUploadMb || 1024);
  const maxUploadBytes = Number(shell.dataset.maxUploadBytes || maxUploadMb * 1024 * 1024);
  const maxFormMb = Number(shell.dataset.maxFormMb || 100);
  const maxFormBytes = Number(shell.dataset.maxFormBytes || maxFormMb * 1024 * 1024);
  const maxImageSide = 2400;
  const imageQuality = 0.88;
  let lastEditorRange = null;
  let pendingImageRange = null;

  const sync = () => {
    hidden.value = editor.innerHTML.trim();
  };

  const formatBytes = (bytes) => {
    if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${bytes}B`;
  };

  const byteLength = (text) => new Blob([text || ""]).size;

  const editorRange = () => {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0);
    return editor.contains(range.commonAncestorContainer) ? range.cloneRange() : null;
  };

  const setEditorRange = (range) => {
    const selection = window.getSelection();
    if (!selection || !range) return;
    selection.removeAllRanges();
    selection.addRange(range);
  };

  const rememberEditorRange = () => {
    lastEditorRange = editorRange() || lastEditorRange;
  };

  const insertImages = (images, savedRange = null) => {
    if (!images.length) return;
    editor.focus();
    const fragment = document.createDocumentFragment();
    images.forEach((imageData) => {
      const paragraph = document.createElement("p");
      const image = document.createElement("img");
      image.src = imageData.url;
      image.alt = imageData.original_filename || "";
      image.loading = "lazy";
      paragraph.appendChild(image);
      fragment.appendChild(paragraph);
    });
    const spacer = document.createElement("p");
    spacer.appendChild(document.createElement("br"));
    fragment.appendChild(spacer);

    const selection = window.getSelection();
    const range = savedRange || editorRange();
    if (selection && range) {
      setEditorRange(range);
      range.deleteContents();
      const lastNode = fragment.lastChild;
      range.insertNode(fragment);
      range.setStartAfter(lastNode);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    } else {
      editor.appendChild(fragment);
    }
    sync();
  };

  const loadImage = (file) => new Promise((resolve, reject) => {
    const image = new Image();
    const objectUrl = URL.createObjectURL(file);
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("图片读取失败"));
    };
    image.src = objectUrl;
  });

  const canvasToBlob = (canvas, type, quality) => new Promise((resolve) => {
    canvas.toBlob(resolve, type, quality);
  });

  const compressImageFile = async (file) => {
    const compressibleTypes = ["image/jpeg", "image/png", "image/webp"];
    if (!compressibleTypes.includes(file.type)) return file;

    const image = await loadImage(file);
    const scale = Math.min(1, maxImageSide / Math.max(image.naturalWidth, image.naturalHeight));
    if (scale === 1 && file.size <= 2 * 1024 * 1024) return file;

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const outputType = file.type === "image/png" ? "image/png" : "image/jpeg";
    const blob = await canvasToBlob(canvas, outputType, imageQuality);
    if (!blob || blob.size >= file.size) return file;

    const extension = outputType === "image/png" ? ".png" : ".jpg";
    const filename = file.name.replace(/\.[^.]+$/, "") + extension;
    return new File([blob], filename, { type: outputType, lastModified: Date.now() });
  };

  const prepareEditorImages = async (files) => {
    const images = Array.from(files || []).filter((file) => file.type.startsWith("image/"));
    const prepared = [];
    for (const image of images) {
      prepared.push(await compressImageFile(image));
    }
    return prepared;
  };

  const uploadEditorImages = async (files, savedRange = null) => {
    const originalImages = Array.from(files || []).filter((file) => file.type.startsWith("image/"));
    const images = await prepareEditorImages(files);
    if (!images.length) return;
    const originalBytes = originalImages.reduce((total, file) => total + file.size, 0);
    const totalBytes = images.reduce((total, file) => total + file.size, 0);
    if (totalBytes > maxUploadBytes) {
      window.alert(
        `触发原因：图片上传请求体超过单次上传限制。\n` +
        `原始图片大小：${formatBytes(originalBytes)}。\n` +
        `压缩后准备上传：${formatBytes(totalBytes)}。\n` +
        `系统单次上传限制：${formatBytes(maxUploadBytes)}。`
      );
      return;
    }

    const formData = new FormData();
    images.forEach((file) => formData.append("images", file, file.name || "editor-image.png"));

    let response;
    let payload = {};
    try {
      response = await fetch("/admin/editor-images", {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });
      const contentType = response.headers.get("content-type") || "";
      payload = contentType.includes("application/json")
        ? await response.json()
        : { error: await response.text() };
    } catch (error) {
      window.alert(`图片上传失败：${error.message}`);
      return;
    }
    if (!response.ok) {
      window.alert(
        `${payload.error || "图片上传失败"}\n` +
        `原始图片大小：${formatBytes(originalBytes)}。\n` +
        `压缩后上传大小：${formatBytes(totalBytes)}。`
      );
      return;
    }
    insertImages(payload.images || [], savedRange);
  };

  shell.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => {
      editor.focus();
      document.execCommand(button.dataset.command, false, null);
      sync();
    });
  });

  shell.querySelector("[data-action='heading']").addEventListener("click", () => {
    editor.focus();
    document.execCommand("formatBlock", false, "h2");
    sync();
  });

  shell.querySelector("[data-action='paragraph']").addEventListener("click", () => {
    editor.focus();
    document.execCommand("formatBlock", false, "p");
    sync();
  });

  shell.querySelector("[data-action='link']").addEventListener("click", () => {
    const url = window.prompt("请输入链接地址");
    if (!url) return;
    editor.focus();
    document.execCommand("createLink", false, url);
    sync();
  });

  shell.querySelector("[data-action='image']").addEventListener("click", () => {
    pendingImageRange = lastEditorRange || editorRange();
    imageInput.click();
  });

  if (imageInput) {
    imageInput.addEventListener("change", async () => {
      await uploadEditorImages(imageInput.files, pendingImageRange);
      pendingImageRange = null;
      imageInput.value = "";
    });
  }

  editor.addEventListener("paste", async (event) => {
    const files = Array.from(event.clipboardData && event.clipboardData.files || []);
    if (!files.some((file) => file.type.startsWith("image/"))) return;
    const range = editorRange();
    event.preventDefault();
    await uploadEditorImages(files, range);
  });

  editor.addEventListener("dragover", (event) => {
    event.preventDefault();
  });

  editor.addEventListener("drop", async (event) => {
    const files = Array.from(event.dataTransfer && event.dataTransfer.files || []);
    if (!files.some((file) => file.type.startsWith("image/"))) return;
    const range = editorRange();
    event.preventDefault();
    await uploadEditorImages(files, range);
  });

  editor.addEventListener("input", sync);
  editor.addEventListener("keyup", rememberEditorRange);
  editor.addEventListener("mouseup", rememberEditorRange);
  editor.addEventListener("focus", rememberEditorRange);
  form.addEventListener("submit", (event) => {
    sync();
    const editorBytes = byteLength(hidden.value);
    if (editorBytes > maxFormBytes) {
      event.preventDefault();
      window.alert(
        `触发原因：作业指导书编辑内容超过表单字段限制。\n` +
        `当前编辑内容大小：${formatBytes(editorBytes)}。\n` +
        `编辑内容限制：${formatBytes(maxFormBytes)}。\n` +
        `普通附件上传限制：${formatBytes(maxUploadBytes)}。`
      );
    }
  });
  sync();
});

document.querySelectorAll("[data-dropzone]").forEach((dropzone) => {
  const input = dropzone.querySelector("input[type='file']");
  const fileList = dropzone.querySelector("[data-file-list]");
  if (!input) return;
  let selectedFiles = Array.from(input.files || []);

  const syncInputFiles = () => {
    if (typeof DataTransfer === "undefined") return;
    const transfer = new DataTransfer();
    selectedFiles.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
  };

  const appendFiles = (files) => {
    const nextFiles = Array.from(files || []);
    if (!nextFiles.length) return;
    selectedFiles = selectedFiles.concat(nextFiles);
    syncInputFiles();
    updateFileList();
  };

  const moveFile = (fromIndex, toIndex) => {
    if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) return;
    const nextFiles = [...selectedFiles];
    const [file] = nextFiles.splice(fromIndex, 1);
    nextFiles.splice(toIndex, 0, file);
    selectedFiles = nextFiles;
    syncInputFiles();
    updateFileList();
  };

  const updateFileList = () => {
    if (!fileList) return;
    if (!selectedFiles.length) {
      fileList.textContent = fileList.dataset.emptyText || "未选择文件";
      return;
    }
    fileList.innerHTML = "";
    selectedFiles.forEach((file, index) => {
      const item = document.createElement("div");
      item.className = "dropzone-file";
      item.draggable = true;
      item.dataset.index = String(index);
      item.textContent = `${index + 1}. ${file.name}`;

      item.addEventListener("click", (event) => {
        event.stopPropagation();
      });

      item.addEventListener("dragstart", (event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(index));
        item.classList.add("is-moving");
      });

      item.addEventListener("dragend", () => {
        item.classList.remove("is-moving");
      });

      item.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      });

      item.addEventListener("drop", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const fromIndex = Number(event.dataTransfer.getData("text/plain"));
        moveFile(fromIndex, index);
      });

      fileList.appendChild(item);
    });
  };

  dropzone.addEventListener("click", () => {
    input.click();
  });

  input.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  input.addEventListener("change", () => {
    appendFiles(input.files);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropzone.classList.remove("is-dragging");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    appendFiles(files);
  });

  updateFileList();
});
