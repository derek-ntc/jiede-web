import unittest
from unittest.mock import patch

import app


class ShippedPdfCompanyTests(unittest.TestCase):
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

        texts = []

        def collect_text(value):
            if hasattr(value, "getPlainText"):
                texts.append(value.getPlainText())
            if hasattr(value, "_cellvalues"):
                collect_text(value._cellvalues)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect_text(item)

        collect_text(captured_story)
        rendered_text = "\n".join(texts)
        self.assertIn("发货公司：宁波市杰德机械科技有限公司", rendered_text)
        self.assertNotIn("宁波市江北利万管件有限公司", rendered_text)


if __name__ == "__main__":
    unittest.main()
