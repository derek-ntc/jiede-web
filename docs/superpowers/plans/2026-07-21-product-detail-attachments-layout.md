# Product Detail Attachments Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the inventory QR card from the product detail page and place the existing attachments card in its right-column position while retaining the print-label action.

**Architecture:** Keep the existing responsive `.detail-grid` and attachment rendering behavior. Change only the product detail template structure and its regression assertions; inventory data, label printing, PDF preview, and other pages remain untouched.

**Tech Stack:** Flask, Jinja2 templates, Python `unittest`, CSS Grid

## Global Constraints

- Desktop order must be product information first and attachments second.
- Mobile layout must continue to collapse to one column.
- Remove the “库存二维码” card and its `data-qr-code` element.
- Keep inventory text fields in product information and keep the “打印标签” button.
- Keep all existing attachment behavior unchanged.
- This workspace has no Git repository, so commit steps are not available.

---

### Task 1: Product Detail Layout Regression

**Files:**
- Modify: `tests/test_inventory.py:102-105`
- Modify: `templates/detail.html:102-150`

**Interfaces:**
- Consumes: Flask route `GET /manual/<manual_id>` and its existing Jinja context.
- Produces: Detail HTML with one `附件` card as the second direct child of `.detail-grid`, no `data-qr-code`, no `库存二维码`, and an unchanged `打印标签` link.

- [ ] **Step 1: Write the failing test**

Replace the old QR assertions with layout assertions:

```python
detail_html = self.client.get(f"/manual/{manual_id}").get_data(as_text=True)
self.assertNotIn("库存二维码", detail_html)
self.assertNotIn("data-qr-code=", detail_html)
self.assertIn("打印标签", detail_html)
self.assertIn("<h2>附件</h2>", detail_html)
self.assertLess(detail_html.index("<h2>产品信息</h2>"), detail_html.index("<h2>附件</h2>"))
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest tests.test_inventory.InventoryTests.test_inventory_tables_codes_scan_in_out_and_order_status -v
```

Expected: FAIL because `库存二维码` and `data-qr-code` are still rendered.

- [ ] **Step 3: Implement the minimal template change**

Delete the complete conditional QR panel from `templates/detail.html`:

```jinja2
{% if can_manage_warehouse_inventory %}
  <div class="panel inventory-qr-panel">
    <h2>库存二维码</h2>
    <div class="inventory-qr-box" data-qr-code="{{ inventory_code }}"></div>
    <p class="empty-text">{{ inventory_code }}</p>
    {% if inventory_locations %}
      <div class="mini-list">
        {% for item in inventory_locations %}
          <span>{{ item.name }}({{ item.code }})：{{ item.quantity }}</span>
        {% endfor %}
      </div>
    {% else %}
      <p class="empty-text">暂无库位库存</p>
    {% endif %}
  </div>
{% endif %}
```

Leave the existing attachment `<div class="panel">` immediately after the product information panel so it becomes the second `.detail-grid` item. Do not change attachment markup or the top print-label condition.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the focused command from Step 2.

Expected: one test passes with zero failures.

- [ ] **Step 5: Run PDF and full regression suites**

Run:

```bash
PYTHONWARNINGS=ignore .venv/bin/python -m unittest tests.test_pdf_preview -v
PYTHONWARNINGS=ignore .venv/bin/python -m unittest discover -s tests -v
```

Expected: PDF preview tests pass and all 27 tests pass.

### Task 2: VPS Deployment and Verification

**Files:**
- Deploy: `templates/detail.html`
- Deploy: `tests/test_inventory.py`

**Interfaces:**
- Consumes: SSH access as `ubuntu` using `/Users/derek/.ssh/id_ed25519`.
- Produces: Active `jiede-web.service` serving the revised detail layout.

- [ ] **Step 1: Back up affected remote files**

Create a timestamped directory under `/home/ubuntu/jiede-web/backups/` and copy the two existing files into it with their relative paths preserved.

- [ ] **Step 2: Sync the two changed files**

Use `rsync -avR` over the configured SSH key to `/home/ubuntu/jiede-web/`.

- [ ] **Step 3: Run the full remote suite**

Run:

```bash
cd /home/ubuntu/jiede-web
PYTHONWARNINGS=ignore .venv/bin/python -m unittest discover -s tests -v
```

Expected: all 27 tests pass.

- [ ] **Step 4: Restart and probe the service**

Restart `jiede-web.service`, verify it is `active`, and request `/manual/3` from localhost expecting HTTP 200.

- [ ] **Step 5: Verify the live HTML contract**

Confirm the live detail page contains `附件` and `打印标签`, and does not contain `库存二维码` or `data-qr-code` for an authenticated inventory user; automated route coverage supplies the authenticated assertion when browser authentication is unavailable.
