-- 仓库库存管理第一阶段迁移。应用启动时 app.py 会自动执行等价迁移；
-- 本文件用于部署记录和人工核对。

ALTER TABLE manuals ADD COLUMN sku TEXT NOT NULL DEFAULT '';
ALTER TABLE manuals ADD COLUMN barcode TEXT NOT NULL DEFAULT '';
ALTER TABLE manuals ADD COLUMN qr_code TEXT NOT NULL DEFAULT '';
ALTER TABLE manuals ADD COLUMN default_location_id INTEGER;
ALTER TABLE manuals ADD COLUMN min_stock INTEGER NOT NULL DEFAULT 0;

ALTER TABLE product_orders ADD COLUMN inventory_received_quantity INTEGER NOT NULL DEFAULT 0;
ALTER TABLE product_orders ADD COLUMN inventory_status TEXT NOT NULL DEFAULT '未入库';

CREATE TABLE IF NOT EXISTS warehouse_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    remark TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(manual_id, location_id),
    FOREIGN KEY (manual_id) REFERENCES manuals(id) ON DELETE CASCADE,
    FOREIGN KEY (location_id) REFERENCES warehouse_locations(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_no TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    manual_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    from_location_id INTEGER,
    to_location_id INTEGER,
    related_order_type TEXT NOT NULL DEFAULT '',
    related_order_id TEXT NOT NULL DEFAULT '',
    related_order_no TEXT NOT NULL DEFAULT '',
    customer TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (manual_id) REFERENCES manuals(id) ON DELETE CASCADE,
    FOREIGN KEY (from_location_id) REFERENCES warehouse_locations(id) ON DELETE SET NULL,
    FOREIGN KEY (to_location_id) REFERENCES warehouse_locations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_manuals_sku ON manuals (sku);
CREATE INDEX IF NOT EXISTS idx_manuals_barcode ON manuals (barcode);
CREATE INDEX IF NOT EXISTS idx_manuals_qr_code ON manuals (qr_code);
CREATE INDEX IF NOT EXISTS idx_inventory_balances_manual ON inventory_balances (manual_id, location_id);
CREATE INDEX IF NOT EXISTS idx_inventory_transactions_manual ON inventory_transactions (manual_id, created_at DESC);
