import os
import sys
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import app as qa_app


class AppHelperTests(unittest.TestCase):
    def test_data_file_for_valid_shop_id_stays_inside_data_dir(self):
        path = qa_app.data_file("shop_123-abc")
        root = os.path.abspath(qa_app.DATA_DIR)
        self.assertTrue(os.path.abspath(path).startswith(root + os.sep))
        self.assertTrue(path.endswith("traces_shop_123-abc.json"))

    def test_analysis_file_for_valid_shop_id_stays_inside_data_dir(self):
        path = qa_app.analysis_file("shop.123")
        root = os.path.abspath(qa_app.DATA_DIR)
        self.assertTrue(os.path.abspath(path).startswith(root + os.sep))
        self.assertTrue(path.endswith("analysis_shop.123.json"))

    def test_data_file_rejects_path_traversal_shop_id(self):
        with self.assertRaises(qa_app.InvalidShopId):
            qa_app.data_file("../secret")

    def test_data_file_rejects_path_separator_shop_id(self):
        with self.assertRaises(qa_app.InvalidShopId):
            qa_app.data_file("shop/123")

    def test_resolve_secret_key_prefers_environment_value(self):
        self.assertEqual(qa_app.resolve_secret_key({"QA_SECRET_KEY": "secret-value"}), "secret-value")

    def test_resolve_secret_key_falls_back_to_default_for_local_dev(self):
        self.assertEqual(qa_app.resolve_secret_key({}), qa_app.DEFAULT_SECRET_KEY)

if __name__ == "__main__":
    unittest.main()

