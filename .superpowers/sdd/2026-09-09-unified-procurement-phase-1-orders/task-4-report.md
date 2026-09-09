# Task 4 实现报告

## 结果与范围

仅在 `/Users/derek/Documents/jiede-web/.worktrees/unified-procurement` 实现统一采购订单，未修改主工作区、未部署、未创建子代理、未产生采购库存。

实现提交：`0791a7f37851e55187e620f1198cd2d5889af574` — `feat: add unified purchase orders`。

完成四类分类页及共享新建、详情、编辑、取消页面；分类 URL 仅接受 raw-material/carton/outsourcing/other，存储 raw_material。类别由路由或已有订单决定；服务端生成单号、验证供应商与收货模板启用状态并保存快照。支持草稿→已下单、取消及已到齐/已取消不可修改；保留部分到货状态，禁止已下单退回草稿。

稳定 item ID 支持多行添加、删除、排序；拒绝其他订单 ID、重复 ID 与新单引用已有明细。服务器重算 CNY 分金额，界面标注 RMB／人民币；无采购价格权限时不渲染价格字段或金额，不信任伪造价格，保留既有行单价且按新数量重算，新行写 NULL。

按最终 Controller 澄清：仅非 voided 收货单计入数量下限及行删除保护，所有收货记录都已作废的行可移除。写入使用 SQLite BEGIN IMMEDIATE 与 SAVEPOINT，并沿用已有 HTTP 全应用写锁。真实 SQLite 测试使用触发器故障验证后续明细失败会回滚表头、已有行更新和新建行。

## TDD 与验证记录

1. RED：`.venv/bin/python -m unittest tests.test_purchase_orders -v`
   - 初始 20 个测试，8 failures、42 errors（包括参数化子测试）；预期原因是订单服务函数缺失与路由返回 404。
2. RED：`node --test tests/js/purchase-orders.test.js`
   - 4 个测试全部失败；预期原因是 purchase-orders.js 模块不存在、行编辑 API 缺失。
3. GREEN：`.venv/bin/python -m unittest tests.test_purchase_orders -v` 与 `node --test tests/js/purchase-orders.test.js`
   - 第一轮 Python 20/20、Node 4/4 通过。
4. 自审追加 RED：`.venv/bin/python -m unittest tests.test_purchase_orders.PurchaseOrderRouteTests.test_validation_error_retains_entered_rows_without_leaking_forged_prices -v`
   - 1 failure：校验失败页面未保留已输入的明细。修复后 Python 23/23、Node 4/4 通过；包括追加的分类不匹配、已到齐不可写、停用主档拒绝覆盖。
5. 全量 Python（仅运行一次）：`.venv/bin/python -m unittest discover -s tests`
   - `Ran 615 tests in 58.333s`；`OK (skipped=1)`，退出码 0。
   - 包含修正作废收货语义之前的 23 个订单测试。运行中收到 Controller 的 voided 语义澄清；随后按明确许可只追加受影响聚焦回归，不再次执行全量。
6. 作废语义 RED：`.venv/bin/python -m unittest tests.test_purchase_orders.PurchaseOrderDomainTests.test_voided_receipts_do_not_lock_quantity_or_line_removal -v`
   - 1 error：非作废到货 3、作废到货 9 时降至数量 3 被错误拒绝，定位到汇总 SQL 缺少 receipt.status 条件。
7. 最终 GREEN：`.venv/bin/python -m unittest tests.test_purchase_orders -v`
   - `Ran 24 tests in 1.210s`；`OK`，退出码 0。包含非作废累计下限、非作废历史禁止删除、全部作废后允许删除。
8. 最终 JS：`node --test tests/js/purchase-orders.test.js`
   - 4 tests / 4 pass / 0 fail，退出码 0。Node 内置测试，无新增依赖；覆盖值/ID 保留、添加/删除/排序、重编号与边界。
9. `git diff --check` 与 `git diff --cached --check`
   - 均无输出，退出码 0。

自审已完整检查所有生产代码、模板、静态资源与测试 diff，确认动态 SQL 列名来自内部常量、用户筛选值均参数绑定、模板采用 Jinja 自动转义、错误回显按可见字段白名单过滤价格、客户端仅管理行而不决定类别或金额。

## 变更文件

- `procurement.py`
- `app.py`
- `templates/purchase_tabs.html`
- `templates/purchase_orders.html`
- `templates/purchase_order_form.html`
- `templates/purchase_order_detail.html`
- `static/purchase-orders.js`
- `static/purchase-orders.css`
- `tests/test_purchase_orders.py`
- `tests/js/purchase-orders.test.js`
- 本报告 `.superpowers/sdd/2026-09-09-unified-procurement-phase-1-orders/task-4-report.md`

## 风险、边界与偏差

- 本任务未运行真实浏览器视觉回归；当前证据为 Flask 实际 HTML/请求测试与 Node 纯行状态逻辑测试。真实浏览器行操作和视觉验收留给集成 QA。
- 未改顶部导航、未实现导出或迁移，均属于后续阶段一任务；采购页已提供四类页内切换。
- 采购收货表在本阶段尚未创建；服务在表缺失时正常工作，存在时按未来接口关联 purchase_receipts/purchase_receipt_items。收货保护测试在真实 SQLite 创建该接口对应的表。
- 全量回归发生在最终 voided 修正之前；按 Controller 明确指示，最终修改只重新运行 24 个订单聚焦测试及 4 个 Node 测试，不第二次跑全量。
- Python 输出包含现有项目普遍使用 `datetime.utcnow()` 的 DeprecationWarning；新增路由延续相同时间表示，未跨范围调整时间基础设施。
- 写服务消费 `normalize_purchase_order_payload` 产生的已验证 payload；调用方必须由可信权限上下文传入 `can_view_prices`，不能将原始 HTTP 字典直接传给写服务。当前路由满足该约束。
- 新建/编辑限制最多 500 行与单字段 4000 字符，金额沿用现有 pricing.py 的 SQLite 安全上限。
