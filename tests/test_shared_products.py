import json
import sqlite3
import unittest
from unittest.mock import patch

import app
from tests.test_product_bom_import import ProductBomDatabaseTestCase, bom_row, CUSTOMER, NOW


class SharedProductTests(ProductBomDatabaseTestCase):
    def client(self):
        with app.get_db() as conn:
            app.seed_user(conn, 'shared-admin', 'test-password', 'admin', NOW)
        client = app.app.test_client()
        with client.session_transaction() as session:
            session.update(admin_logged_in=True, admin_username='shared-admin', admin_role='admin')
        return client

    def row(self, assembly='DZ-30', quantity=2, **extra):
        return dict(bom_row(3, assembly, 'SKU-1', '共用支架', 'YS-001', 'PCS', quantity), **extra)

    def test_cross_customer_import_reuses_product_and_isolates_bom(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            app.apply_product_bom_import(conn, CUSTOMER, [self.row()], 'admin', NOW)
            result = app.apply_product_bom_import(conn, '客户乙', [self.row(quantity=4)], 'admin', NOW)
            self.assertEqual(result['new_product_count'], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manuals').fetchone()[0], 1)
            a = app.get_assembly_definition(conn, CUSTOMER, 'DZ-30')[0]
            b = app.get_assembly_definition(conn, '客户乙', 'DZ-30')[0]
            self.assertEqual((a['manual_id'], a['quantity_per_set']), (b['manual_id'], 2))
            self.assertEqual(b['quantity_per_set'], 4)
            self.assertEqual(len(app.inventory_product_rows(conn, selected_customer='客户乙')), 1)

    def test_same_drawing_different_specification_is_not_reused(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            app.apply_product_bom_import(conn, CUSTOMER, [self.row(specification='厚2')], 'admin', NOW)
            app.apply_product_bom_import(conn, '客户乙', [self.row(specification='厚3')], 'admin', NOW)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manuals').fetchone()[0], 2)

    def test_exact_identity_wins_over_older_single_customer_drawing_match(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            app.apply_product_bom_import(conn, CUSTOMER, [self.row(specification='旧规格')], 'admin', NOW)
            app.apply_product_bom_import(conn, '客户乙', [self.row(specification='新规格')], 'admin', NOW)
            target = conn.execute("SELECT id FROM manuals WHERE supplier='新规格'").fetchone()[0]
            app.apply_product_bom_import(conn, CUSTOMER, [self.row(specification='新规格')], 'admin', NOW)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM manuals WHERE supplier='新规格'").fetchone()[0], 1)
            self.assertTrue(app.has_customer(conn, target, CUSTOMER))

    def test_linked_customer_is_searchable_and_visible_in_product_detail(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            for name in (CUSTOMER, '客户乙'):
                app.apply_product_bom_import(conn, name, [self.row()], 'admin', NOW)
            product = conn.execute('SELECT id FROM manuals').fetchone()[0]
        client = self.client()
        self.assertIn('共用支架', client.get('/admin/products?q=客户乙').get_data(as_text=True))
        self.assertIn('客户乙', client.get(f'/manual/{product}').get_data(as_text=True))

    def test_second_customer_import_preserves_canonical_code_and_unit(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            app.apply_product_bom_import(conn, CUSTOMER, [self.row()], 'admin', NOW)
            incoming = self.row()
            incoming.update(sku='SECOND-SKU', unit='')
            app.apply_product_bom_import(conn, '客户乙', [incoming], 'admin', NOW)
            record = conn.execute('SELECT id,sku,unit FROM manuals').fetchone()
            self.assertEqual((record['sku'],record['unit']), ('SKU-1','PCS'))
            for code in ('SKU-1','SECOND-SKU'):
                self.assertEqual(app.fetch_inventory_product_by_code(conn,code)['id'],record['id'])

    def test_second_customer_order_dispatch_and_edit_keep_customer_and_shared_stock(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            app.apply_product_bom_import(conn, CUSTOMER, [self.row()], 'admin', NOW)
            app.apply_product_bom_import(conn, '客户乙', [self.row(quantity=4)], 'admin', NOW)
            product = conn.execute('SELECT id FROM manuals').fetchone()[0]
            loc = app.get_or_create_default_location(conn)['id']
            conn.execute('INSERT INTO inventory_balances(manual_id,location_id,quantity,updated_at) VALUES (?,?,20,?)', (product,loc,NOW))
        client = self.client()
        self.assertIn('客户乙', client.get('/admin/shipped-orders/create').get_data(as_text=True))
        options = client.get('/admin/shipped-orders/component-options', query_string={'customer':'客户乙'}).get_json()['items']
        self.assertEqual([row['manual_id'] for row in options], [product])
        response = client.post('/admin/orders/new', data=dict(order_no='SECOND-ORDER', ordered_at='2026-09-12',customer='客户乙',manual_id=str(product),quantity='5',planned_ship_at='2026-09-20'))
        self.assertTrue(response.location.endswith('/admin/orders'))
        with app.get_db() as conn:
            order = conn.execute('SELECT * FROM product_orders').fetchone()
            self.assertEqual(order['customer'],'客户乙')
        response = client.post('/admin/shipped-orders/new', data=dict(order_id=str(order['id']),shipped_quantity='5',shipped_at='2026-09-12'))
        self.assertIn('/admin/delivery-notes/operations/',response.location)
        with app.get_db() as conn:
            self.assertEqual(app.inventory_total_for_manual(conn, product),15)
            self.assertEqual(conn.execute('SELECT customer FROM delivery_notes').fetchone()[0],'客户乙')
        edit = dict(drawing_no='YS-001',product_name='共用支架',supplier='YS-001',customer=CUSTOMER,additional_customer='客户丙',unit='PCS',sku='SKU-1',
                    assembly_drawing_no=['DZ-30','DZ-30'],assembly_quantity_per_set=['2','4'],assembly_customer=[CUSTOMER,'客户乙'])
        response = client.post(f'/admin/{product}/edit', data=edit)
        self.assertEqual(response.status_code,302)
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT customer FROM product_orders').fetchone()[0],'客户乙')
            self.assertTrue(app.has_customer(conn,product,'客户丙'))
            self.assertEqual(app.get_assembly_definition(conn,'客户乙','DZ-30')[0]['quantity_per_set'],4)
            self.assertEqual(app.get_component_open_orders(conn,product,CUSTOMER),[])
        edit['assembly_quantity_per_set']=['2','-1']
        page=client.post(f'/admin/{product}/edit',data=edit).get_data(as_text=True)
        self.assertIn('name="assembly_customer" list="customer-options" value="客户乙"',page)

    def test_order_import_requires_explicit_customer_for_shared_product(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            for name in (CUSTOMER,'客户乙'):
                app.apply_product_bom_import(conn,name,[self.row()], 'admin', NOW)
            rows=[dict(row_number=2,drawing_no='YS-001',quantity=2)]
            self.assertTrue(app.match_order_import_items(conn,rows)[1])
            items,errors=app.match_order_import_items(conn,rows,'客户乙')
            self.assertEqual(errors,[])
            self.assertEqual(items[0]['manual']['customer'],'客户乙')

    def test_migration_is_atomic_when_reference_update_fails(self):
        with app.get_db() as conn:
            app.apply_product_bom_import(conn,CUSTOMER,[self.row()], 'admin', NOW)
            source=dict(conn.execute('SELECT * FROM manuals').fetchone())
            del source['id']
            source['customer']='客户乙'
            duplicate=conn.execute(f"INSERT INTO manuals({','.join(source)}) VALUES ({','.join('?' for _ in source)})",list(source.values())).lastrowid
            conn.execute("INSERT INTO product_orders(manual_id,order_no,ordered_at,quantity,customer,planned_ship_at,created_at,updated_at) VALUES (?,'X','',3,'客户乙','','','')",(duplicate,))
            conn.execute("CREATE TRIGGER stop_move BEFORE UPDATE OF manual_id ON product_orders BEGIN SELECT RAISE(ABORT,'test merge rollback'); END")
            conn.execute('DELETE FROM shared_product_migrations')
        with self.assertRaises(sqlite3.IntegrityError):
            app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manuals').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT manual_id FROM product_orders').fetchone()[0],duplicate)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_merge_archive').fetchone()[0],0)

    def test_migration_combines_stock_preserves_order_customer_and_old_codes(self):
        with app.get_db() as conn:
            app.apply_product_bom_import(conn, CUSTOMER, [self.row()], 'admin', NOW)
            keep = conn.execute('SELECT id FROM manuals').fetchone()[0]
            duplicate = conn.execute("""INSERT INTO manuals
                (drawing_no,product_name,supplier,customer,sku,model,category,version,filename,original_filename,created_at,updated_at)
                VALUES ('YS-001','共用支架','YS-001','客户乙','OLD-SKU','','','','','','', '')""").lastrowid
            conn.execute("INSERT INTO product_assembly_components(manual_id,assembly_drawing_no,quantity_per_set,sort_order,created_at,updated_at) VALUES (?,'DZ-31',4,0,'','')", (duplicate,))
            conn.execute("UPDATE manuals SET filename='drawing-b.pdf',original_filename='drawing-b.pdf',file_type='pdf' WHERE id=?",(duplicate,))
            loc = app.get_or_create_default_location(conn)['id']
            for product, quantity in ((keep, 20), (duplicate, 7)):
                conn.execute('INSERT INTO inventory_balances(manual_id,location_id,quantity,updated_at) VALUES (?,?,?,?)', (product, loc, quantity, NOW))
            conn.execute("INSERT INTO product_orders(manual_id,order_no,ordered_at,quantity,customer,planned_ship_at,created_at,updated_at) VALUES (?,'B-ORDER','2026-09-12',5,'','','','')", (duplicate,))
            for product, filename in ((keep, 'drawing-a.pdf'), (duplicate, 'drawing-b.pdf')):
                conn.execute("INSERT INTO manual_files(manual_id,filename,original_filename,file_type,created_at) VALUES (?,?,?,'pdf',?)", (product,filename,filename,NOW))
            conn.execute('DELETE FROM shared_product_migrations')
        app.init_db()
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manuals').fetchone()[0], 1)
            self.assertEqual(app.inventory_total_for_manual(conn, keep), 27)
            order = conn.execute('SELECT manual_id,customer FROM product_orders').fetchone()
            self.assertEqual(tuple(order), (keep, '客户乙'))
            self.assertEqual(app.get_assembly_definition(conn, '客户乙', 'DZ-31')[0]['manual_id'], keep)
            self.assertEqual(app.fetch_inventory_product_by_code(conn, 'OLD-SKU')['id'], keep)
            self.assertEqual([r['filename'] for r in app.get_manual_files(conn,keep)], ['drawing-a.pdf','drawing-b.pdf'])
            self.assertEqual(conn.execute('SELECT file_type FROM manuals WHERE id=?',(keep,)).fetchone()[0],'pdf')
            archive=json.loads(conn.execute('SELECT original_json FROM product_merge_archive').fetchone()[0])
            self.assertEqual(archive['product']['id'],duplicate)
            self.assertEqual(archive['related']['manual_files'][0]['filename'],'drawing-b.pdf')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_conflicting_unit_keeps_both_products_and_records_warning(self):
        with app.get_db() as conn:
            app.apply_product_bom_import(conn,CUSTOMER,[self.row()], 'admin', NOW)
            record=dict(conn.execute('SELECT * FROM manuals').fetchone())
            del record['id']
            record.update(customer='客户乙',unit='箱')
            conn.execute(f"INSERT INTO manuals({','.join(record)}) VALUES ({','.join('?' for _ in record)})",list(record.values()))
            conn.execute('DELETE FROM shared_product_migrations')
        app.init_db()
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM manuals').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT reason FROM product_merge_conflicts').fetchone()[0],'单位不一致')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_merge_archive').fetchone()[0],0)

    def test_product_delete_cleans_customer_links_and_code_aliases(self):
        with app.get_db() as conn:
            app.apply_product_bom_import(conn,CUSTOMER,[self.row()], 'admin', NOW)
            product=conn.execute('SELECT id FROM manuals').fetchone()[0]
            conn.execute("INSERT INTO product_code_aliases VALUES ('OLD-CODE',?)",(product,))
        app.delete_manuals([product])
        with app.get_db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_customer_names').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_code_aliases').fetchone()[0],0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_migration_deduplicates_same_physical_attachment(self):
        with app.get_db() as conn:
            app.apply_product_bom_import(conn,CUSTOMER,[self.row()], 'admin', NOW)
            conn.execute("UPDATE manuals SET filename='shared.pdf',original_filename='共用图纸.pdf',file_type='pdf'")
            record=dict(conn.execute('SELECT * FROM manuals').fetchone())
            keep=record.pop('id')
            record['customer']='客户乙'
            source=conn.execute(f"INSERT INTO manuals({','.join(record)}) VALUES ({','.join('?' for _ in record)})",list(record.values())).lastrowid
            conn.execute('DELETE FROM shared_product_migrations')
        app.init_db()
        with app.get_db() as conn:
            files=app.get_manual_files(conn,keep)
            self.assertEqual(len(files),1)
            self.assertEqual(files[0]['filename'],'shared.pdf')
            file_id=files[0]['id']
            archive=json.loads(conn.execute('SELECT original_json FROM product_merge_archive WHERE source_id=?',(source,)).fetchone()[0])
            self.assertEqual(archive['related']['manual_files'][0]['filename'],'shared.pdf')
        # A duplicate must not fool the existing "keep one file" guard into
        # deleting the one physical drawing while leaving a dangling row.
        with patch('app.delete_upload_file') as remove:
            response=self.client().post(f'/admin/{keep}/files/{file_id}/delete')
            self.assertEqual(response.status_code,302)
            remove.assert_not_called()

    def test_shared_customer_can_order_and_customer_filter_does_not_leak_other_orders(self):
        with app.get_db() as conn:
            app.ensure_customer_exists(conn, '客户乙', NOW)
            app.apply_product_bom_import(conn, CUSTOMER, [self.row()], 'admin', NOW)
            app.apply_product_bom_import(conn, '客户乙', [self.row()], 'admin', NOW)
            product = conn.execute('SELECT id FROM manuals ORDER BY id').fetchone()[0]
            self.assertEqual(app.validated_production_followup_product(conn, product, '客户乙', 'YS-001', '共用支架'), ('客户乙', product))
            for customer in (CUSTOMER, '客户乙'):
                conn.execute("INSERT INTO product_orders(manual_id,order_no,ordered_at,quantity,customer,planned_ship_at,created_at,updated_at) VALUES (?,?,?,?,?,'','','')", (product, customer, '2026-09-12', 5, customer))
            orders = app.get_component_open_orders(conn, product, '客户乙')
            self.assertEqual([r['order_no'] for r in orders], ['客户乙'])
