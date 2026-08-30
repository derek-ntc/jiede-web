# PDF Floating Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct PDF navigation in production follow-up and product-document pages with a shared floating PDF.js preview that supports 50%–300% zoom and printing without adding a download button.

**Architecture:** Existing Flask file routes remain the only PDF data sources. A single modal mounted by `base.html` loads same-origin PDF bytes through a focused `static/pdf-preview.js` controller and renders every page with locally vendored PDF.js canvases. Templates opt into the viewer through data attributes; non-PDF links remain unchanged.

**Tech Stack:** Flask 3.0.3, Jinja2, vanilla JavaScript ES modules, Mozilla PDF.js `pdfjs-dist@6.1.200` legacy browser build, CSS, Python `unittest`.

## Global Constraints

- PDF.js must be stored under `static/vendor/pdfjs/`; no runtime CDN dependency.
- Preview zoom range is exactly 50%–300%, changing in 25% steps.
- The preview toolbar contains zoom out, percentage, zoom in, `打印PDF`, and close; it contains no download action.
- Product PDF and production-follow-up PDF use existing same-origin routes and existing permissions.
- Non-PDF file behavior must remain unchanged.
- The workspace is not a Git repository, so commit steps are recorded as unavailable rather than executed.

---

### Task 1: Add regression tests for inline PDF responses and preview contracts

**Files:**
- Create: `tests/test_pdf_preview.py`
- Test: `tests/test_pdf_preview.py`

**Interfaces:**
- Consumes: Flask routes `preview_manual(filename)` and `production_followup_file(file_id)`.
- Produces: Executable expectations for inline response headers, modal controls, and template trigger attributes.

- [ ] **Step 1: Write failing response-header tests**

Create a temporary database and temporary PDF directories, insert one product PDF and one production-follow-up PDF, then assert:

```python
self.assertEqual(product_response.mimetype, "application/pdf")
self.assertIn("inline", product_response.headers.get("Content-Disposition", ""))
self.assertEqual(followup_response.mimetype, "application/pdf")
self.assertIn("inline", followup_response.headers.get("Content-Disposition", ""))
```

The logged-in test client session must contain `admin_logged_in=True`, `admin_username="admin"`, and `admin_role="admin"` before requesting the production-follow-up route.

- [ ] **Step 2: Write failing markup-contract tests**

Render production follow-up, product detail, and product preview pages and assert:

```python
self.assertIn('data-pdf-preview-url=', html)
self.assertIn('data-pdf-preview-name=', html)
self.assertIn('data-pdf-preview-dialog', html)
self.assertIn('data-pdf-preview-print', html)
self.assertNotIn('data-pdf-preview-download', html)
```

Add one `.dwg` attachment and assert its link does not contain `data-pdf-preview-url`.

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest tests.test_pdf_preview -v
```

Expected: failures for missing preview data attributes and missing shared modal markup. Header assertions may already pass; the suite must still be red because the preview feature is absent.

- [ ] **Step 4: Record commit unavailability**

Run `git rev-parse --is-inside-work-tree`; expected output is a fatal “not a git repository” message. Do not initialize a repository.

---

### Task 2: Vendor the pinned PDF.js runtime

**Files:**
- Create: `static/vendor/pdfjs/pdf.mjs`
- Create: `static/vendor/pdfjs/pdf.worker.mjs`
- Create: `static/vendor/pdfjs/LICENSE`

**Interfaces:**
- Consumes: Official `pdfjs-dist@6.1.200` npm package.
- Produces: `pdfjsLib.getDocument(...)` through `static/vendor/pdfjs/pdf.mjs`; worker URL `/static/vendor/pdfjs/pdf.worker.mjs`.

- [ ] **Step 1: Download the exact package to a temporary directory**

Run:

```bash
mkdir -p /tmp/jiede-pdfjs
cd /tmp/jiede-pdfjs
npm pack pdfjs-dist@6.1.200
```

Expected: one `pdfjs-dist-6.1.200.tgz` archive downloaded from npm.

- [ ] **Step 2: Extract only the required legacy browser files and license**

Extract the archive, then copy:

```text
package/legacy/build/pdf.mjs        -> static/vendor/pdfjs/pdf.mjs
package/legacy/build/pdf.worker.mjs -> static/vendor/pdfjs/pdf.worker.mjs
package/LICENSE                     -> static/vendor/pdfjs/LICENSE
```

Use `apply_patch` for any hand-authored files; copying these upstream-generated vendor assets is allowed as a mechanical operation.

- [ ] **Step 3: Verify asset identity and syntax**

Run:

```bash
test -s static/vendor/pdfjs/pdf.mjs
test -s static/vendor/pdfjs/pdf.worker.mjs
rg -n "6\.1\.200" static/vendor/pdfjs/pdf.mjs static/vendor/pdfjs/pdf.worker.mjs
```

Expected: both modules are non-empty and contain the pinned version marker.

- [ ] **Step 4: Record commit unavailability**

Do not create a commit because the workspace has no Git metadata.

---

### Task 3: Add the shared modal structure and styles

**Files:**
- Modify: `templates/base.html`
- Modify: `static/style.css`
- Test: `tests/test_pdf_preview.py`

**Interfaces:**
- Consumes: trigger attributes `data-pdf-preview-url` and `data-pdf-preview-name`.
- Produces: one `[data-pdf-preview-dialog]` element with controls and `[data-pdf-preview-pages]` render target.

- [ ] **Step 1: Add one shared modal after the main content in `base.html`**

The structure must expose these stable hooks:

```html
<div class="pdf-preview-dialog" data-pdf-preview-dialog hidden role="dialog" aria-modal="true" aria-labelledby="pdf-preview-title">
  <button class="pdf-preview-backdrop" type="button" data-pdf-preview-close aria-label="关闭PDF预览"></button>
  <section class="pdf-preview-window">
    <header class="pdf-preview-toolbar">
      <strong id="pdf-preview-title" data-pdf-preview-title>PDF预览</strong>
      <div class="pdf-preview-actions">
        <button type="button" data-pdf-preview-zoom-out>缩小</button>
        <output data-pdf-preview-zoom>100%</output>
        <button type="button" data-pdf-preview-zoom-in>放大</button>
        <button type="button" data-pdf-preview-print>打印PDF</button>
        <button type="button" data-pdf-preview-close>关闭</button>
      </div>
    </header>
    <div class="pdf-preview-status" data-pdf-preview-status>正在加载PDF…</div>
    <div class="pdf-preview-pages" data-pdf-preview-pages></div>
  </section>
</div>
<script type="module" src="{{ url_for('static', filename='pdf-preview.js', v=static_version) }}"></script>
```

Do not add a download control or `download` attribute.

- [ ] **Step 2: Add focused desktop and mobile CSS**

Implement fixed full-viewport overlay, 92vw/92vh desktop window, wrapped mobile toolbar, scrollable pages, white canvas sheets, loading/error state, and `body.pdf-preview-open { overflow: hidden; }`. Ensure the backdrop sits below the window and the modal z-index is above the top navigation.

- [ ] **Step 3: Run markup tests**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest tests.test_pdf_preview -v
```

Expected: modal-control assertions pass; trigger assertions remain failing until Task 5.

- [ ] **Step 4: Record commit unavailability**

Do not create a commit because the workspace has no Git metadata.

---

### Task 4: Implement PDF loading, rendering, zoom, printing, and cleanup

**Files:**
- Create: `static/pdf-preview.js`
- Test: `tests/test_pdf_preview.py`

**Interfaces:**
- Consumes: `pdfjsLib.getDocument(url)`, modal hooks from Task 3, and same-origin PDF URL/name trigger attributes.
- Produces: delegated click handling plus `openPreview(url, name)`, `renderDocument()`, `setZoom(nextZoom)`, `printPdf()`, and `closePreview()` behavior.

- [ ] **Step 1: Write static contract assertions before the module**

Extend `tests/test_pdf_preview.py` to read `static/pdf-preview.js` and assert it contains:

```python
self.assertIn('pdf.worker.mjs', source)
self.assertIn('data-pdf-preview-url', source)
self.assertIn('window.print()', source)
self.assertIn('MIN_ZOOM = 0.5', source)
self.assertIn('MAX_ZOOM = 3', source)
self.assertIn('ZOOM_STEP = 0.25', source)
```

- [ ] **Step 2: Run the static contract test and verify RED**

Run the specific new test. Expected: error or failure because `static/pdf-preview.js` does not exist.

- [ ] **Step 3: Implement the minimal ES module**

Import `/static/vendor/pdfjs/pdf.mjs`, set `GlobalWorkerOptions.workerSrc`, and implement:

```javascript
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.25;
```

Use one monotonically increasing render token so stale page renders stop after closing or opening another file. Render pages sequentially into canvases using `page.getViewport({ scale })`. On initial load, compute a fit-width scale capped at `1`. Re-render all canvases after zoom changes and disable zoom buttons at the exact limits.

Use delegated document clicks so dynamically rendered template links work. Close on both close buttons, backdrop click, and Escape. Restore body scrolling and clear canvases on close.

- [ ] **Step 4: Implement printing without a download fallback**

Create a same-origin hidden iframe whose `src` is the current raw PDF URL, wait for its load event, then call:

```javascript
printFrame.contentWindow.focus();
printFrame.contentWindow.print();
```

If printing cannot start, set the modal status to `浏览器阻止了打印窗口，请允许弹窗后重试` and keep the preview open. Never navigate to the raw PDF and never create a download link.

- [ ] **Step 5: Run the module contract tests**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest tests.test_pdf_preview -v
```

Expected: module contract and modal tests pass; template trigger tests remain red.

- [ ] **Step 6: Record commit unavailability**

Do not create a commit because the workspace has no Git metadata.

---

### Task 5: Wire PDF triggers into production follow-up and product pages

**Files:**
- Modify: `templates/production_followups.html`
- Modify: `templates/detail.html`
- Modify: `templates/preview.html`
- Test: `tests/test_pdf_preview.py`

**Interfaces:**
- Consumes: shared delegated trigger contract from Task 4.
- Produces: PDF-only anchors containing both preview attributes; non-PDF anchors unchanged.

- [ ] **Step 1: Add PDF-only triggers to production follow-up**

Within the file loop, branch on `file.filename.lower().endswith('.pdf')`. PDF anchors use `href` as a no-JavaScript fallback and add:

```html
data-pdf-preview-url="{{ url_for('production_followup_file', file_id=file.id) }}"
data-pdf-preview-name="{{ file.original_filename }}"
```

Non-PDF anchors keep their current `target="_blank"` behavior and receive no preview attributes.

- [ ] **Step 2: Add PDF-only triggers to product detail and preview pages**

For PDF files, change the visible “打开文件”/“单独打开” actions to `预览PDF` and attach:

```html
data-pdf-preview-url="{{ url_for('preview_manual', filename=file.filename) }}"
data-pdf-preview-name="{{ file.original_filename }}"
```

Remove embedded PDF iframes from these pages so the file is rendered only inside the shared floating viewer. Replace each former iframe location with a compact `预览PDF` trigger. Image and other file branches remain unchanged.

- [ ] **Step 3: Handle the primary product file button**

In `detail.html`, if `manual.filename` ends in `.pdf`, label the top action `预览PDF` and attach the same preview attributes using the product name as fallback title. Otherwise preserve the existing `打开文件` link.

- [ ] **Step 4: Run preview regression tests and verify GREEN**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest tests.test_pdf_preview -v
```

Expected: all PDF preview tests pass, including non-PDF isolation.

- [ ] **Step 5: Run the full local suite**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest discover -s tests -v
```

Expected: all existing and new tests pass with zero failures and errors.

- [ ] **Step 6: Record commit unavailability**

Do not create a commit because the workspace has no Git metadata.

---

### Task 6: Browser verification and VPS deployment

**Files:**
- Verify: `templates/base.html`
- Verify: `templates/production_followups.html`
- Verify: `templates/detail.html`
- Verify: `templates/preview.html`
- Verify: `static/pdf-preview.js`
- Verify: `static/style.css`
- Verify: `static/vendor/pdfjs/`

**Interfaces:**
- Consumes: completed local implementation and authenticated application pages.
- Produces: verified local behavior and a restarted `jiede-web.service` on `/home/ubuntu/jiede-web`.

- [ ] **Step 1: Verify through the in-app browser when an authenticated session is available**

Check product detail and production follow-up PDF triggers. Confirm modal open, multi-page rendering, zoom boundaries, Escape/close, printing, and mobile toolbar wrapping. If authentication is unavailable, record that visual verification was blocked and rely on route/markup tests without inspecting credentials.

- [ ] **Step 2: Back up affected VPS files**

Create `/home/ubuntu/jiede-web/backups/deploy-20260721-pdf-preview/` containing the existing templates, stylesheet, and any same-named static module/vendor files that already exist. Do not copy or overwrite `.env`, `data/`, `manuals/`, or `uploads/`.

- [ ] **Step 3: Sync only affected code and static assets**

Use `rsync -avR` over the existing `ubuntu@jiede.nbliwan.com` key connection for the files listed in this plan.

- [ ] **Step 4: Run the complete suite on the VPS**

Run:

```bash
cd /home/ubuntu/jiede-web
PYTHONWARNINGS=ignore .venv/bin/python -m unittest discover -s tests -v
```

Expected: zero failures and errors.

- [ ] **Step 5: Restart and verify service health**

Run:

```bash
sudo systemctl restart jiede-web.service
sudo systemctl is-active jiede-web.service
curl --retry 5 --retry-connrefused --retry-delay 1 -I http://127.0.0.1:8006/admin/production-followups
```

Expected: service state `active` and HTTP redirect to login or authenticated success response, proving Gunicorn is serving the new build.

- [ ] **Step 6: Verify public endpoint headers**

Request a known product PDF from `https://jiede.nbliwan.com/manuals/<filename>` and confirm HTTP 200, `Content-Type: application/pdf`, and `Content-Disposition: inline`.
