import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import shipping_workflow
from tests.test_assembly_shipping import AssemblyTask7TestCase


class CustomerRecipientDefaultsTests(unittest.TestCase):
    def test_contact_fields_are_used_only_when_both_dedicated_fields_are_blank(self):
        row = dict(
            contact="王工",
            phone="012345",
            recipient_name="",
            recipient_phone="",
            address="仓库",
        )

        actual = shipping_workflow.customer_recipient_defaults(row)

        self.assertEqual(
            actual,
            {
                "recipient_name": "王工",
                "recipient_phone": "012345",
                "address": "仓库",
                "recipient_source": "contact",
            },
        )

    def test_dedicated_pair_has_precedence_without_mixing_contact_fields(self):
        cases = [
            (
                dict(contact="联系人", phone="10086", recipient_name=" 收货人 ",
                     recipient_phone=" 0574-1 ", address=" 收货地址 "),
                ("收货人", "0574-1", "收货地址", "recipient"),
            ),
            (
                dict(contact="不应混入", phone="10010", recipient_name="专用姓名",
                     recipient_phone=" ", address=""),
                ("专用姓名", "", "", "recipient"),
            ),
            (
                dict(contact=" 备用联系人 ", phone=" 00123 ", recipient_name=" \t",
                     recipient_phone="\n", address=" 仓库 "),
                ("备用联系人", "00123", "仓库", "contact"),
            ),
            (None, ("", "", "", "missing")),
            (
                dict(contact=" ", phone="", recipient_name="", recipient_phone="", address=" "),
                ("", "", "", "missing"),
            ),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                actual = shipping_workflow.customer_recipient_defaults(row)
                self.assertEqual(
                    (
                        actual["recipient_name"],
                        actual["recipient_phone"],
                        actual["address"],
                        actual["recipient_source"],
                    ),
                    expected,
                )


class RecipientDefaultsIntegrationTests(AssemblyTask7TestCase):
    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        self.paths = patch.multiple(
            app,
            **{
                name: Path(self.files.name) / name.lower()
                for name in (
                    "DATA_DIR",
                    "MANUALS_DIR",
                    "SIGNATURES_DIR",
                    "RECONCILIATION_SIGNATURES_DIR",
                    "INSPECTION_REPORTS_DIR",
                    "PRODUCTION_DRAWINGS_DIR",
                )
            },
        )
        self.paths.start()
        super().setUp()
        self.product_a = self.create_product("PA", "客户A")
        self.order_a = self.create_order(self.product_a, "SO-A", 20, customer="客户A")
        self.stock_product(self.product_a, 20)
        self.configure_components([(self.product_a, 1)])
        self.product_b = self.create_product("PB", "客户B")
        self.order_b = self.create_order(self.product_b, "SO-B", 20, customer="客户B")
        self.stock_product(self.product_b, 20)
        self.product_missing = self.create_product("PC", "无档案客户")
        self.create_order(self.product_missing, "SO-C", 20, customer="无档案客户")
        with app.get_db() as conn:
            conn.execute(
                """INSERT INTO customers
                   (name,contact,phone,recipient_name,recipient_phone,address,created_at,updated_at)
                   VALUES ('客户A','王工','0012345','','',' A仓 ','','')"""
            )
            conn.execute(
                """INSERT INTO customers
                   (name,contact,phone,recipient_name,recipient_phone,address,created_at,updated_at)
                   VALUES ('客户B','不应混入','10086','李师傅','','B仓','','')"""
            )

    def tearDown(self):
        super().tearDown()
        self.paths.stop()
        self.files.cleanup()

    def _post_orders(self, order_ids, *, token, overrides=None):
        data = {
            "shipped_at": "2026-09-12",
            "order_id": [str(order_id) for order_id in order_ids],
            "shipped_quantity": ["1"] * len(order_ids),
            "operation_token": token,
        }
        if overrides is not None:
            data["recipient_overrides"] = json.dumps(overrides, ensure_ascii=False)
        return self.client.post("/admin/shipped-orders/new", data=data)

    def _notes_for_operation(self, location):
        operation_id = int(location.rsplit("/", 1)[-1])
        with app.get_db() as conn:
            return [
                shipping_workflow.load_delivery_note(conn, row["id"])
                for row in conn.execute(
                    "SELECT id FROM delivery_notes WHERE operation_id=? ORDER BY customer",
                    (operation_id,),
                )
            ]

    def test_shipping_page_resolves_each_customer_and_represents_absent_profiles(self):
        html = self.client.get("/admin/shipped-orders/create").get_data(as_text=True)
        payload = json.loads(re.search(r"data-recipient-defaults>(.*?)</script>", html, re.S)[1])

        self.assertEqual(
            payload["客户A"],
            {
                "recipient_name": "王工",
                "recipient_phone": "0012345",
                "address": "A仓",
                "recipient_source": "contact",
            },
        )
        self.assertEqual(payload["客户B"]["recipient_name"], "李师傅")
        self.assertEqual(payload["客户B"]["recipient_phone"], "")
        self.assertEqual(payload["客户B"]["recipient_source"], "recipient")
        self.assertEqual(payload["无档案客户"]["recipient_source"], "missing")

    def test_rendered_page_cache_key_tracks_both_recipient_scripts(self):
        html = self.client.get("/admin/shipped-orders/create").get_data(as_text=True)

        for filename in ("assembly_shipping.js", "order_shipping.js"):
            with self.subTest(filename=filename):
                match = re.search(rf"/static/{re.escape(filename)}\?v=(\d+)", html)
                self.assertIsNotNone(match)
                script_mtime = int((Path(app.BASE_DIR) / "static" / filename).stat().st_mtime)
                self.assertGreaterEqual(int(match.group(1)), script_mtime)

    def test_notes_use_per_customer_defaults_and_keep_customer_ids(self):
        response = self._post_orders([self.order_a, self.order_b], token="multi-defaults")
        self.assertEqual(response.status_code, 302)
        notes = {note["customer"]: note for note in self._notes_for_operation(response.location)}

        self.assertEqual(
            (notes["客户A"]["recipient_name"], notes["客户A"]["recipient_phone"], notes["客户A"]["address"]),
            ("王工", "0012345", "A仓"),
        )
        self.assertEqual(
            (notes["客户B"]["recipient_name"], notes["客户B"]["recipient_phone"]),
            ("李师傅", ""),
        )
        self.assertNotEqual(notes["客户A"]["customer_id"], notes["客户B"]["customer_id"])

    def test_explicit_empty_override_wins_over_defaults(self):
        response = self._post_orders(
            [self.order_a],
            token="empty-override",
            overrides={"客户A": {"recipient_name": "", "recipient_phone": "", "address": ""}},
        )
        self.assertEqual(response.status_code, 302)
        note = self._notes_for_operation(response.location)[0]
        self.assertEqual((note["recipient_name"], note["recipient_phone"], note["address"]), ("", "", ""))

    def test_assembly_note_uses_the_same_contact_defaults(self):
        preview = self.post_preview(customer="客户A", sets=1).get_json()
        response = self.client.post(
            "/admin/shipped-orders/assembly/new",
            data=self.save_data(
                preview,
                extra_data={"operation_token": "assembly-recipient-defaults"},
            ),
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        notes = self._notes_for_operation(response.get_json()["redirect_url"])
        self.assertEqual(
            (notes[0]["recipient_name"], notes[0]["recipient_phone"], notes[0]["address"]),
            ("王工", "0012345", "A仓"),
        )

    def test_saved_note_does_not_follow_later_customer_edits(self):
        response = self._post_orders([self.order_a], token="snapshot")
        note = self._notes_for_operation(response.location)[0]
        with app.get_db() as conn:
            conn.execute(
                "UPDATE customers SET contact='新联系人',phone='999',address='新地址' WHERE name='客户A'"
            )
            saved = shipping_workflow.load_delivery_note(conn, note["id"])
        self.assertEqual(
            (saved["recipient_name"], saved["recipient_phone"], saved["address"]),
            ("王工", "0012345", "A仓"),
        )

    def test_real_browser_recipient_controllers_preserve_manual_edits_during_preview(self):
        harness = r'''
        const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
        class Element {
          constructor(){this.value='';this.textContent='';this.children=[];this.listeners={};this.dataset={};this.hidden=false;}
          addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
          emit(type){for(const fn of this.listeners[type] || [])fn({target:this});}
          append(...items){this.children.push(...items);}
          replaceChildren(...items){this.children=items;}
          querySelector(selector){return this.map?.[selector] || null;}
          set innerHTML(_){throw Error('unsafe HTML');}
        }
        global.document={querySelectorAll:()=>[],createElement:()=>new Element()};
        global.window={setTimeout,clearTimeout,confirm:()=>true,location:{origin:'https://test.local'}};
        vm.runInThisContext(fs.readFileSync('static/order_shipping.js','utf8'));
        vm.runInThisContext(fs.readFileSync('static/assembly_shipping.js','utf8'));
        const defaults={
          '客户A':{recipient_name:'王工',recipient_phone:'0012345',address:'A仓',recipient_source:'contact'},
          '客户B':{recipient_name:'李师傅',recipient_phone:'',address:'B仓',recipient_source:'recipient'}
        };

        const defaultsNode=new Element(),groups=new Element(),overrides=new Element();
        defaultsNode.textContent=JSON.stringify(defaults);
        const orderForm=new Element();
        orderForm.map={'[data-recipient-defaults]':defaultsNode,'[data-recipient-groups]':groups,'[data-recipient-overrides]':overrides};
        let activeCustomer='客户A';
        const lines=()=>[{querySelector:()=>({value:'1',selectedOptions:[{dataset:{customer:activeCustomer}}]})}];
        const sync=createOrderRecipientController(orderForm,lines);
        sync();
        const flatten=node=>[node,...node.children.flatMap(flatten)];
        let fields=flatten(groups).filter(node=>node.dataset.recipientField);
        assert.deepEqual(fields.map(node=>node.value),['王工','0012345','A仓']);
        assert.match(flatten(groups).find(node=>node.dataset.recipientHint !== undefined).textContent,/来自客户联系人/);
        fields[0].value='本次手工收货人';fields[0].emit('input');
        sync();
        fields=flatten(groups).filter(node=>node.dataset.recipientField);
        assert.equal(fields[0].value,'本次手工收货人');
        activeCustomer='客户B';sync();
        assert.match(flatten(groups).find(node=>node.dataset.recipientHint !== undefined).textContent,/收货电话.*未填写/);

        const assemblyDefaults=new Element(),recipientFields=new Element(),hint=new Element();
        const preview=new Element(),warnings=new Element(),submit=new Element();
        assemblyDefaults.textContent=JSON.stringify(defaults);
        const name=new Element(),phone=new Element(),address=new Element();
        recipientFields.map={'[name="recipient_name"]':name,'[name="recipient_phone"]':phone,'[name="address"]':address,'[data-assembly-recipient-hint]':hint};
        const assemblyForm=new Element();
        assemblyForm.map={'[data-assembly-recipient-defaults]':assemblyDefaults,'[data-assembly-recipient-fields]':recipientFields,
          '[data-assembly-preview]':preview,'[data-assembly-warning-summary]':warnings,'[data-assembly-submit]':submit};
        applyAssemblyRecipientDefaults(assemblyForm,'客户A');
        assert.equal(name.value,'王工');assert.match(hint.textContent,/来自客户联系人/);
        name.value='组装手工收货人';
        renderAssemblyPreview(assemblyForm,{items:[],warnings:[],preview_token:'fresh'});
        assert.equal(name.value,'组装手工收货人');
        applyAssemblyRecipientDefaults(assemblyForm,'客户B');
        assert.equal(name.value,'李师傅');assert.equal(phone.value,'');
        assert.match(hint.textContent,/收货电话.*未填写/);
        '''
        result = subprocess.run(
            ["node", "-e", harness],
            cwd=app.BASE_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rendered_assembly_customer_change_and_preview_keep_all_recipient_fields(self):
        html = self.client.get("/admin/shipped-orders/create").get_data(as_text=True)
        defaults = re.search(
            r"data-assembly-recipient-defaults>(.*?)</script>", html, re.S
        )[1]
        harness = r'''
        const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
        const defaults=JSON.parse(fs.readFileSync(0,'utf8'));
        class Element {
          constructor(){this.value='';this.textContent='';this.children=[];this.listeners={};this.dataset={};this.hidden=false;this.disabled=false;this.classList={toggle(){},add(){}};}
          addEventListener(type,fn){(this.listeners[type] ||= []).push(fn);}
          async emit(type){for(const fn of this.listeners[type] || [])await fn({target:this,preventDefault(){}});}
          append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
          appendChild(item){this.append(item);return item;}
          replaceChildren(...items){this.children=[];this.append(...items);}
          querySelector(selector){return this.map?.[selector] || null;}
          querySelectorAll(){return [];}
          contains(){return false;}
          matches(){return false;}
          setAttribute(name,value){this[name]=value;}
          set innerHTML(_){throw Error('unsafe HTML');}
        }
        global.document={activeElement:null,querySelectorAll:()=>[],createElement:()=>new Element()};
        global.window={setTimeout,clearTimeout,confirm:()=>true,location:{origin:'https://test.local',assign(){}}};
        global.fetch=async url=>{
          assert.match(url,/assembly-options/);
          return {ok:true,json:async()=>({assembly_drawing_numbers:['ASM-100']})};
        };
        vm.runInThisContext(fs.readFileSync('static/assembly_shipping.js','utf8'));
        const defaultsNode=new Element(),recipientFields=new Element(),hint=new Element();
        defaultsNode.textContent=JSON.stringify(defaults);
        const name=new Element(),phone=new Element(),address=new Element();
        recipientFields.map={'[name="recipient_name"]':name,'[name="recipient_phone"]':phone,
          '[name="address"]':address,'[data-assembly-recipient-hint]':hint};
        const selectors=['customer','drawing','set-quantity','submit','status','preview','warning-summary',
          'component-search','component-results','recalculate'];
        const nodes=Object.fromEntries(selectors.map(key=>[key,new Element()]));
        const form=new Element();form.action='/admin/shipped-orders/assembly/new';
        form.map={
          '[data-assembly-recipient-defaults]':defaultsNode,'[data-assembly-recipient-fields]':recipientFields,
          ...Object.fromEntries(selectors.map(key=>[`[data-assembly-${key}]`,nodes[key]]))
        };
        globalThis.__assemblyShippingTestApi.initializeAssemblyShipmentForm(form);
        nodes.customer.value='客户A';await nodes.customer.emit('change');
        assert.deepEqual([name.value,phone.value,address.value],['王工','0012345','A仓']);
        name.value='手工收货人';phone.value='00999';address.value='手工地址';
        renderAssemblyPreview(form,{items:[],warnings:[],preview_token:'fresh'});
        assert.deepEqual([name.value,phone.value,address.value],['手工收货人','00999','手工地址']);
        nodes.customer.value='客户B';await nodes.customer.emit('change');
        assert.deepEqual([name.value,phone.value,address.value],['李师傅','','B仓']);
        '''
        result = subprocess.run(
            ["node", "-e", f"(async()=>{{{harness}}})().catch(error=>{{console.error(error);process.exitCode=1}})"],
            input=defaults,
            cwd=app.BASE_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
