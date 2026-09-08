# Task 1 实施报告：产品规格和客户收货基础资料

## 状态

DONE。仅在隔离 worktree `/Users/derek/Documents/jiede-web/.worktrees/shipping-product-production` 实施，未部署、未连接或修改真实业务数据。

## 实施内容

- 新增 `shipping_workflow.ensure_shipping_workflow_tables(conn)`，由 `init_db()` 在客户、普通发货和组装发货表已建立后调用；迁移通过 `PRAGMA table_info` 幂等添加：
  - `customers.recipient_name TEXT NOT NULL DEFAULT ''`
  - `customers.recipient_phone TEXT NOT NULL DEFAULT ''`
  - `product_order_shipments.specification_snapshot TEXT NULL`
  - `assembly_shipment_items.specification_snapshot TEXT NULL`
- 保留 `manuals.supplier` 存储和 `supplier` 表单/筛选参数兼容，对产品页面的对外标签、查找提示、筛选及排序文案统一为“规格型号”；未改动独立 `model` 字段。
- 普通发货（直接发货及计划审核发货）在写入时复制当前产品规格；普通发货读取统一输出 `specification`，快照为 `NULL` 时回退当前 `manuals.supplier`，空字符串快照不回退。
- 组装发货新增时复制当前产品规格；编辑时随既有价格快照映射保留原规格快照，包括旧记录的 `NULL`。组装读取统一输出 `specification` 并按相同规则兼容旧记录。
- 已发货普通及组装列表展示规格型号，查询可匹配快照/旧记录回退值；Excel 导出列从“供应商”改为“规格型号”并读取统一字段，同时兼容现有直接构造的旧 `supplier` 字典调用者。
- 客户新增、编辑、搜索及页面展示加入独立收货人/联系方式；服务端 trim，分别限制 100 字符，验证失败不发生部分写入；业务联系人和业务电话保持独立。
- 产品 BOM 导入解析未改，页面明确旧模板“规格型号 → 产品图号”的历史约定不会写入新的产品属性含义。
- 未修改材料、采购、喷塑、纸箱、到货或财务供应商语义，也未扩大发货价格读取权限。

## TDD 证据

### RED

命令：

```text
/Users/derek/Documents/jiede-web/.venv/bin/python -m unittest tests.test_shipping_workflow_fields -v
```

实现前结果：`Ran 7 tests ... FAILED (failures=2, errors=5)`。

关键预期失败：

- `no such column: specification_snapshot`（普通和组装发货快照字段尚不存在）
- `KeyError: 'recipient_name'` / 客户行没有收货字段
- 发货读取没有统一 `specification` 字段
- 产品/发货页面仍缺少“规格型号”标签
- 导入说明缺少“旧模板”兼容说明

这些失败均直接对应待实现行为，不是测试夹具或语法错误。

### GREEN

命令：

```text
/Users/derek/Documents/jiede-web/.venv/bin/python -m unittest tests.test_shipping_workflow_fields -v
```

结果：`Ran 7 tests in 0.625s`，`OK`。

## 测试结果

- Task 1 + 指定相邻回归：

```text
/Users/derek/Documents/jiede-web/.venv/bin/python -m unittest tests.test_shipping_workflow_fields tests.test_product_bom_import tests.test_customer_billing tests.test_product_pricing -q
Ran 70 tests in 4.351s
OK
```

- 全量回归（修复兼容问题后的新鲜运行）：

```text
/Users/derek/Documents/jiede-web/.venv/bin/python -m unittest discover -s tests -q
Ran 375 tests in 33.459s
OK (skipped=1)
```

测试输出含基线已有的 `datetime.utcnow()` DeprecationWarning、少量资源警告和故障注入测试日志；无失败或新增未处理异常。

## 修改文件

- `shipping_workflow.py`
- `app.py`
- `templates/admin.html`
- `templates/edit.html`
- `templates/detail.html`
- `templates/index.html`
- `templates/customers.html`
- `templates/shipped_orders.html`
- `templates/product_bom_import.html`
- `tests/test_shipping_workflow_fields.py`

## 自查

- 完整性：逐项核对 Task 1 brief；迁移、标签、客户字段、普通/组装快照、NULL/空串区别、列表/Excel、旧导入说明均覆盖。
- 兼容性：`supplier` 表单和 URL 参数不变；`model` 原值不变；旧发货快照保持 `NULL` 并动态回退；价格列仍按原权限条件拼接，普通读取没有新增价格字段。
- 数据安全：迁移只增列，不回填、不重建、不改写产品/发货历史；所有测试使用临时隔离数据库。
- 测试质量：测试继承真实 `AssemblyAppTestCase`，走真实 SQLite、Flask 路由、组装预览/保存/编辑和 Excel 生成，无 mock 行为断言。
- 差异检查：`git diff --check` 通过；修改限定在 Task 1 文件和报告。
- 全量套件首次发现现有 workbook 单元测试直接传入旧 `supplier` 字典；根因是新 Excel 投影硬依赖 `specification`。增加仅针对缺键调用者的兼容回退后，该失败测试及全量套件均通过；数据库读取仍始终提供统一 `specification`。

## 关注事项

- 无阻塞问题。最终送货单 PDF 的规格列、收货资料和版式按计划留给 Task 5；本任务没有提前改造 PDF 排版。
- 基线测试警告仍存在，本任务未扩大范围处理。
