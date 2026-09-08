# 产品规格、发货和生产跟进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前可用版本上实现规格型号、灵活配件发货、历史缺价批量补齐、收货资料和送货单、客户分类生产跟进。

**Architecture:** 保留现有 Flask 路由、SQLite 业务记录、订单分配和库存事务。产品属性沿用 `manuals.supplier` 存储；通用增量结构及送货单操作分组放在新模块 `shipping_workflow.py`，业务校验与现有权限在 `app.py` 集成。各任务共享 `app.py`，必须顺序实施，不能并行修改同一文件。

**Tech Stack:** Python 3.12-compatible code, Flask, SQLite, Jinja, vanilla JavaScript, unittest, ReportLab, openpyxl；复用现有依赖，不新增服务。

**Spec:** `docs/superpowers/specs/2026-09-08-shipping-product-production-design.md`

## Global Constraints

- 产品层原“供应商”改称“规格型号”，原栏已有内容直接保留为规格型号。用户已确认。
- 不改动材料供应商、采购供应商以及财务中的供应商公司信息，它们仍然表示真实供应商。
- 价格仍只对管理员或已授权人员返回，不能先放入 HTML/JSON 再隐藏。
- 调整只影响本次发货，不修改产品组装关系。
- 普通送货单继续不含价格；含价对账单仍走原独立流程。
- 保留现有订单分配、库存扣减、缺单/缺库存警告和确认规则。
- 本次不新增客户公开送货单链接，不自动发送邮件或短信；原有对账链接、签字和邮件功能不扩大授权范围。
- 旧价格补齐不在 GET 或启动时执行；已保存价格（包括零）及有效开票/对账占用不改动。
- 当前主目录存在大量已验证但未提交的改动。不得丢弃、重置或把新功能直接覆盖到基于旧 HEAD 的代码上。
- 本次只本地实施；禁止部署 VPS/NAS、操作真实库存、发货或历史价格。另获发布指令后才发布。

## 工作区与基线

- [ ] 先取得隔离工作区同意。创建 `codex/shipping-product-production` 分支工作区，把当前程序、模板、静态资源、测试和文档复制为开发基线；排除 `.env*`、数据库、`manuals/`、`uploads/`、`.venv/`、`.git/`、`.worktrees/` 和 `.superpowers/`。不得改变原目录的工作区和暂存区。
- [ ] 在隔离区保存一次基线提交，记下 SHA，后续审查仅比较此基线之后的新功能；已有功能差异不得误计为本次改动。
- [ ] 复用本机已安装的依赖运行基线：`PYTHONWARNINGS=ignore /Users/derek/Documents/jiede-web/.venv/bin/python -m unittest discover -s tests`。预期 368 项、367 通过、1 跳过；若有差异先定位。
- [ ] 在计划专属进度台账记录每项测试命令、结果、提交和复核结论。任务之间持续执行，不再逐项要求用户确认。

## Task 1: 产品规格和客户收货基础资料

**Files:** Create `shipping_workflow.py`, `tests/test_shipping_workflow_fields.py`; modify `app.py`, `templates/admin.html`, `templates/edit.html`, `templates/detail.html`, `templates/index.html`, `templates/customers.html`, `templates/shipped_orders.html`, `templates/product_bom_import.html`。

**Interfaces:** `shipping_workflow.ensure_shipping_workflow_tables(conn)` 在 `init_db` 中现有表建好后调用。新增 `customers.recipient_name/recipient_phone TEXT NOT NULL DEFAULT ''`；`product_order_shipments.specification_snapshot`、`assembly_shipment_items.specification_snapshot` 为可空 TEXT（NULL 表示没有历史快照，空串表示发货时确实没填）。读取普通/组装发货时提供统一 `specification` 字段，使用快照优先、旧记录回退 `manuals.supplier`。客户读取接口输出两个收货字段，不读取价格。

- [ ] 建立实际行为测试，继承 `tests.test_assembly_shipping.AssemblyAppTestCase` 的隔离数据库和产品/订单辅助方法。核心断言：

```python
product = self.create_product()
with app.get_db() as conn:
    conn.execute("UPDATE manuals SET supplier='DZ-30型', model='legacy' WHERE id=?", (product,))
app.init_db()
with app.get_db() as conn:
    self.assertEqual(tuple(conn.execute('SELECT supplier,model FROM manuals WHERE id=?', (product,)).fetchone()), ('DZ-30型','legacy'))
html = self.client.get('/admin/products').get_data(as_text=True)
self.assertIn('规格型号', html)
self.assertIn('DZ-30型', html)
```

- [ ] 运行 `.../.venv/bin/python -m unittest tests.test_shipping_workflow_fields -v`，确认客户新增/保存收货字段与发货规格快照测试在旧代码失败。
- [ ] 用 `PRAGMA table_info` 控制幂等 ALTER；仅修改产品层标签/搜索/排序，保留 `supplier` 提交参数兼容。普通发货保存时复制产品规格，组装发货新增/编辑按原快照保护方式处理。客户新增/编辑 trim 收货人、电话，长度分别限制 100/100；原业务联系人字段保持不变。导入说明明确旧“规格型号→产品图号”约定，解析不改。

```python
specification = row['specification_snapshot']
if specification is None:
    specification = row['supplier'] or ''
```

- [ ] 验证原供应商值、空规格、旧 `model`、客户联系人互不覆盖；新发货后产品规格改动不影响已保存快照；旧进口模板解析仍通过。运行本任务、`tests.test_product_bom_import`、`tests.test_customer_billing`、`tests.test_product_pricing`。
- [ ] 在隔离分支提交本任务，进行任务级规格和质量复核。

## Task 2: 生产跟进客户分类与产品选择

**Files:** Modify `shipping_workflow.py`, `app.py`, `templates/production_followups.html`; create `static/production-followups.js`, `tests/test_production_followup_customers.py`。

**Interfaces:** 增量字段 `production_followups.customer TEXT NOT NULL DEFAULT ''`、`manual_id INTEGER`；`fetch_production_followups(conn, query='', customer='')` 保持原 query 参数兼容。`customer='__unassigned__'` 仅作为 UI 筛选哨兵，不作为客户实际值存储。新增有权限保护的 `/admin/production-followups/products?customer=...&q=...`，只返回产品 ID、图号、名称、规格，不返回价格。新增 POST `/admin/production-followups/<id>/customer` 维护旧记录归类。

- [ ] 写筛选、关联校验和权限测试：客户 A 与客户 B 相同图号互不串行；关键词和客户使用 AND 组合；未归类单独筛选；改进度后的 Location 保留筛选。关键例：

```python
response = self.client.get('/admin/production-followups', query_string={'customer':'客户A','q':'P1'})
self.assertEqual(response.status_code, 200)
self.assertIn('P1', response.get_data(as_text=True))
self.assertNotIn('客户B专用产品', response.get_data(as_text=True))
```

- [ ] 跑 `.../.venv/bin/python -m unittest tests.test_production_followup_customers -v`，记录预期失败。
- [ ] 按原页面工序逻辑增加客户字段及 API；服务端校验选中产品当前客户与提交客户相同，产品 ID 与文字矛盾时拒绝；没选产品 ID 的手填模式保持可用。旧记录不猜测迁移客户。新增写入口使用现有会话 CSRF 模式，权限沿用生产跟进管理权限。搜索自动提交使用防抖/取消旧请求，不改变工序完成和撤销顺序。

```python
conditions, params = [], []
if customer == '__unassigned__':
    conditions.append("TRIM(production_followups.customer) = ''")
elif customer:
    conditions.append('TRIM(production_followups.customer) = ?')
    params.append(customer.strip())
# Existing keyword OR conditions must be parenthesized before joining with AND.
```

- [ ] 测试旧跟进和附件保留、非法产品和无权限请求不写入、筛选不丢失。提交本任务并完成任务复核。

## Task 3: 组装发货配件删减与临时追加

**Files:** Modify `app.py`, `assembly_shipping.py`, `shipping_workflow.py`, `static/assembly_shipping.js`, `templates/assembly_shipping_form.html`, `templates/assembly_shipment_edit.html`, `templates/shipped_orders.html`; create `tests/test_flexible_assembly_shipping.py`。

**Interfaces:** `build_assembly_shipment_preview(..., selected_manual_ids=None)` 新增尾部可选参数；None 仍表示初始 BOM 展开，显式空列表表示空明细并拒绝保存，不能回退成全套。`overrides` 保持产品 ID→实际数量映射。新增 `assembly_shipment_items.source_kind TEXT NOT NULL DEFAULT 'bom'`，`bom/extra` 标识来源；extra 每套用量和计算数量可用 0 表示不适用，实际数量始终正整数。产品候选接口 `/admin/shipped-orders/component-options` 按客户、图号/名称过滤，只返回被授权可见字段。

- [ ] 基于真实数据库写测试：BOM 有 P1/P2，选 P1/P3（P3 同客户非 BOM），仅保存两行，删除的 P2 不分订单、不扣库存，P3 保存 extra。跨客户、重复 ID、空明细、非法数量拒绝。编辑回显不复原已删行。关键域断言：

```python
preview = app.build_assembly_shipment_preview(conn, '客户A', 'ASM-100', 10,
    overrides={p1:20, p3:7}, selected_manual_ids=[p1,p3])
self.assertEqual([row['manual_id'] for row in preview['items']], [p1,p3])
self.assertEqual(preview['items'][1]['source_kind'], 'extra')
self.assertEqual(preview['items'][1]['shipped_quantity'], 7)
```

- [ ] 跑 `.../.venv/bin/python -m unittest tests.test_flexible_assembly_shipping -v`，先确认失败。
- [ ] 服务端从当前客户产品和已保存批次快照构造可信明细，不能信任浏览器声明 source_kind/产品名称/规格/计算数。初始未选择列表从 BOM 展开；编辑从历史行开始，不重新用当前 BOM 覆盖历史行。预览凭证纳入最终选择、来源、实际数量、订单分配、库存和规格；保存重新计算凭证。保留 warning 确认和受锁定记录的现有只读行为。
- [ ] 前端增加删除、搜索追加、空列表状态；更换客户/组装图号时明确重置本次调整；仅改套数时保留选择和手动数量，按需明确按钮重新计算 BOM 行，extra 不自动乘套数。取消旧查询响应以防客户切换后插入旧候选。用 DOM textContent，不拼接未经转义的产品 HTML。
- [ ] 保存/替换复用既有库存反转、订单汇总和价格快照逻辑；extra 的 0 仅在服务端确认来源后允许。测试部分缺库存、无订单警告、图片失败回滚、删增编辑后库存正确、已定价旧行保留和启动防重复。运行本任务及 `tests.test_assembly_shipping`、`tests.test_startup_shipment_backfill`、`tests.test_product_pricing`；提交并复核。

## Task 4: 历史发货按现价批量补齐

**Files:** Create `shipment_price_backfill.py`, `tests/test_shipment_price_bulk.py`, `templates/shipment_price_backfill.html`; modify `app.py`, `shipping_workflow.py`, `templates/shipped_orders.html`。

**Interfaces:** POST `/admin/shipped-orders/prices/bulk/preview` 接受当前客户、关键词、日期筛选；POST `/admin/shipped-orders/prices/bulk/apply` 接受签名预览 token 和 CSRF。`shipment_price_backfill.build_plan(conn, sources, operator)` 返回 eligible/skipped 项及 digest；`apply_plan(conn, plan, operator, assert_mutable)` 只在调用方原子事务内执行，不自己 commit。来源使用既有 `('ordinary', id)` / `('assembly_item', id)`，日期/客户筛选与当前列表一致，不静默截断。

- [ ] 写实际财务占用、对账占用、零价格、产品缺价/删除、币种、并发及重复测试。缺价行补 1234、已有 0 保留 0，锁定行保持 NULL，重试不改审计时间：

```python
self.assertEqual(updated['unit_price_minor'], 1234)
self.assertEqual(existing_zero['unit_price_minor'], 0)
self.assertIsNone(claimed['unit_price_minor'])
self.assertEqual(second_run['updated_count'], 0)
```

- [ ] 跑 `.../.venv/bin/python -m unittest tests.test_shipment_price_bulk -v`，确认失败。
- [ ] 复用既有价格解析、财务及对账可变性校验；权限要求 `shipped_manage` 且 `user_can_view_prices()`。token 限当前操作人、筛选与明确来源集合，比较价目/状态后再批量更新。审计保存原来源、现价、币种、操作者、时间与“按产品现价补齐”来源；NULL 谓词必须保留。

```sql
UPDATE product_order_shipments
SET unit_price_minor=?, currency=?, price_recorded_by=?, price_recorded_at=?
WHERE id=? AND unit_price_minor IS NULL
```

- [ ] 前端显示可补、跳过原因和金额/币种，明确当前价不等于历史成交价；执行后回到相同列表筛选。产品未定价不填零，所有锁定条件在事务内复查。不得在 GET 或 init_db 调用 apply。
- [ ] 运行本任务、`tests.test_product_pricing`、`tests.test_finance`、`tests.test_reconciliation`；提交并复核。

## Task 5: 发货结果、收货快照与送货单

**Files:** Extend `shipping_workflow.py`; modify `app.py`, `templates/shipment_operations.html`, `templates/assembly_shipping_form.html`, `static/assembly_shipping.js`, `templates/shipped_orders.html`; create `templates/delivery_note_result.html`, `tests/test_delivery_note_workflow.py`。

**Interfaces:** 模块新增 `delivery_operations`（请求摘要、用户、唯一 token、创建时间）、`delivery_notes`（单号、操作 ID、客户/收货资料快照、更新时间/失效标记）及 `delivery_note_sources`（单号 ID、来源类型、来源 ID）表。普通来源指发货 ID，组装来源指批次 ID，避免编辑组装行更换 item ID 后断链。`create_delivery_notes(conn, operation_id, source_groups, recipient_overrides, operator)` 按客户生成单号，不自行提交事务。`load_delivery_note(conn, note_id)` 返回权威来源和收货快照。GET `/admin/delivery-notes/operations/<id>` 为结果页、GET `/admin/delivery-notes/<id>.pdf` 为受发货查看权限保护的 PDF。

- [ ] 写新发货自动分组、重复提交、同 token 不同内容冲突、客户收货快照和跨客户分单测试；附件/图片或入库失败不留半张送货单。PDF 渲染/下载失败重试只读。关键回归：

```python
self.assertEqual(retry_note_ids, first_note_ids)
self.assertEqual(stock_after_retry, stock_after_first)
self.assertEqual(note['recipient_name'], '张师傅')
# Changing customers.recipient_name to '李师傅' does not change this note.
self.assertEqual(reloaded_note['recipient_name'], '张师傅')
```

- [ ] 跑 `.../.venv/bin/python -m unittest tests.test_delivery_note_workflow -v`，确认失败。
- [ ] 两个发货入口在同一数据库事务中保存操作幂等记录、原发货、收货资料与单号分组；保存成功普通入口跳转结果页，组装 JSON 返回结果 URL。最终保存使用 token 绑定当前用户和明确请求摘要（包含附件内容摘要），不能只依赖 disabled 按钮防重。失败回滚后允许重试；已成功同请求返回原结果，不再保存文件/扣库存。
- [ ] 客户选择时填默认收货资料，单次覆盖存送货单快照而不改客户。普通入口若兼容多客户，按各客户拆分收货表单和送货单，单客户不显示无关字段。未填资料可保存但提示。
- [ ] 统一投影普通/组装来源到送货单行：产品图号、名称、规格、单位、数量；普通 PDF 不含价。`build_shipped_orders_pdf` 增加可选收货元数据参数，保留旧调用兼容。旧导出保留缺信息提示；原供应商作为规格的值不得遗漏。
- [ ] 来源合法修改后同单号读取当前有效明细并显示更新时间；删除来源时联动失效，下载返回明确状态，不能导出仍像有效的已删发货。有效财务/对账锁定仍由原入口阻止修改。
- [ ] 使用 pdf skill 实际生成长规格、多行、跨页普通和组装送货单，渲染检查 A4 边界和收货信息。运行本任务、全部发货/价格/财务/对账测试；提交并复核。

## Task 6: 集成验收与本地交付

**Files:** Update `docs/shipping-product-production-usage.md` (new)、本计划与设计的完成状态；按验证发现仅修复覆盖范围内问题。

- [ ] 执行 `PYTHONWARNINGS=ignore .../.venv/bin/python -m unittest discover -s tests` 和 `git diff --check`，记录实际通过/跳过数。不得用旧测试结果宣布通过。
- [ ] 用临时数据库启动仅本机监听的预览服务，独立 cookie 名与密钥。浏览器实际操作：规格保留→客户收货资料→组装删一行加一行→缺库存确认→发货一次→送货单预览/下载→重复提交验证→历史补价→生产按客户搜索并更新工序。隔离数据不得写入主目录真实数据库。
- [ ] 验证无价格权限的页面、JSON、Excel/PDF 没有价格泄漏；未登录 API 与直接 PDF 地址受保护。对所有新 POST 检查 CSRF、输入长度和来源作用域。
- [ ] 用升级前数据库副本检查新增结构幂等、原产品/发货/库存/财务表原字段内容保持不变；历史补价单独执行和单独审计，不混在结构升级中。
- [ ] 汇总全部任务差异做最终独立代码复核；修复重要发现后再跑覆盖测试及完整回归。
- [ ] 交付本地可预览版本、使用说明、测试与 PDF 检查结果。先保留隔离分支和原主目录；未获合并/发布授权前不合并、不推送、不部署。

## 计划自查

- 规格第 1 节由 Task 1 覆盖；配件增删第 2 节由 Task 3 覆盖；单价第 3 节由 Task 4 覆盖；收货/送货单第 4 节由 Task 1/5 覆盖；生产第 5 节由 Task 2 覆盖；安全和验收由各任务及 Task 6 覆盖。
- Task 1/2/3/4/5 共用 `shipping_workflow.ensure_shipping_workflow_tables` 和 `app.py`，顺序修改；Task 5 消费 Task 1 的规格/收货字段与 Task 3 的灵活组装来源，并保留 Task 4 的价格保护。
- 接口兼容：旧 `supplier` 参数、旧导入图号映射、旧 preview 未给 selected_manual_ids 时的默认 BOM、旧 fetch_production_followups 第二位置参数、旧 PDF 构建调用均保留。
- 当前阶段：实施计划已编写，等待确认隔离工作区/执行方式；尚未开始 Task 1。
