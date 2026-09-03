import unittest
from unittest.mock import patch

import app


class ShippedPdfCompanyTests(unittest.TestCase):
    @staticmethod
    def collect_story_text(story):
        texts = []

        def collect_text(value):
            if hasattr(value, "getPlainText"):
                texts.append(value.getPlainText())
            if hasattr(value, "_cellvalues"):
                collect_text(value._cellvalues)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect_text(item)

        collect_text(story)
        return "\n".join(texts)

    def test_delivery_note_footer_uses_jiede_company_name(self):
        captured_story = []

        def capture_story(_document, story):
            captured_story.extend(story)

        with patch.object(app.SimpleDocTemplate, "build", capture_story):
            app.build_shipped_orders_pdf(
                [],
                query="",
                selected_customer="",
                shipped_at="",
                document_no="DN-TEST",
            )

        rendered_text = self.collect_story_text(captured_story)
        self.assertIn("发货公司：宁波市杰德机械科技有限公司", rendered_text)
        self.assertNotIn("宁波市江北利万管件有限公司", rendered_text)

    def test_delivery_note_workbook_and_pdf_ignore_price_and_billing_fields(self):
        shipment = {
            "shipped_at": "2026-09-03",
            "product_name": "普通产品",
            "shipped_quantity": 2,
            "order_no": "SO-SAFE",
            "order_customer": "安全客户",
            "product_customer": "安全客户",
            "supplier": "安全供应商",
            "drawing_no": "SAFE-100",
            "ordered_at": "2026-09-01",
            "planned_ship_at": "2026-09-10",
            "signature_status": "未签收",
            "signature_image": "",
            "unit_price_minor": 987654,
            "currency": "CNY",
            "line_total_minor": 1975308,
            "tax_id": "SECRET-TAX-ID",
            "bank_account": "SECRET-BANK-ACCOUNT",
        }

        workbook = app.build_shipped_orders_workbook(
            [shipment],
            query="",
            selected_customer="安全客户",
            shipped_at="2026-09-03",
        )
        workbook_text = "\n".join(
            str(cell.value)
            for row in workbook.active.iter_rows()
            for cell in row
            if cell.value is not None
        )

        captured_story = []

        def capture_story(_document, story):
            captured_story.extend(story)

        with patch.object(app.SimpleDocTemplate, "build", capture_story):
            app.build_shipped_orders_pdf(
                [shipment],
                query="",
                selected_customer="安全客户",
                shipped_at="2026-09-03",
                document_no="DN-SAFE",
            )
        pdf_story_text = self.collect_story_text(captured_story)

        for output_text in (workbook_text, pdf_story_text):
            with self.subTest(output=output_text[:20]):
                self.assertNotIn("单价", output_text)
                self.assertNotIn("总价", output_text)
                self.assertNotIn("9876.54", output_text)
                self.assertNotIn("19753.08", output_text)
                self.assertNotIn("SECRET-TAX-ID", output_text)
                self.assertNotIn("SECRET-BANK-ACCOUNT", output_text)


if __name__ == "__main__":
    unittest.main()
