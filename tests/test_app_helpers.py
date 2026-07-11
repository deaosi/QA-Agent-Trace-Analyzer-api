import os
import sys
import tempfile
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import app as qa_app


class AppHelperTests(unittest.TestCase):
    def setUp(self):
        self._users_file = qa_app.USERS_FILE
        self._ai_config_file = qa_app.AI_CONFIG_FILE
        self._admin_username = qa_app.ADMIN_USERNAME
        self._admin_password = qa_app.ADMIN_PASSWORD
        self._testing = qa_app.app.config.get("TESTING")

    def tearDown(self):
        qa_app.USERS_FILE = self._users_file
        qa_app.AI_CONFIG_FILE = self._ai_config_file
        qa_app.ADMIN_USERNAME = self._admin_username
        qa_app.ADMIN_PASSWORD = self._admin_password
        qa_app.app.config["TESTING"] = self._testing

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

    def test_public_user_exposes_remark_and_lock_flags(self):
        user = qa_app.public_user({
            "username": "shuxing666",
            "role": "admin",
            "active": True,
            "expiresAt": "",
            "remark": "系统默认管理员",
            "systemLocked": True,
            "passwordHash": "hash",
        })

        self.assertTrue(user["systemLocked"])
        self.assertEqual(user["remark"], "系统默认管理员")
        self.assertTrue(user["hasPassword"])

    def test_cookie_files_are_distinct_per_user(self):
        alice = qa_app.cookie_file_for_user("alice")
        bob = qa_app.cookie_file_for_user("bob")

        self.assertNotEqual(alice, bob)
        self.assertTrue(alice.startswith(os.path.abspath(qa_app.DATA_DIR)))
        self.assertTrue(bob.startswith(os.path.abspath(qa_app.DATA_DIR)))

    def test_load_users_locks_default_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa_app.USERS_FILE = os.path.join(tmp, ".users.json")
            qa_app.ADMIN_USERNAME = "shuxing666"
            qa_app.save_users({
                "shuxing666": {
                    "username": "shuxing666",
                    "passwordHash": qa_app.generate_password_hash("secret123"),
                    "role": "user",
                    "active": False,
                    "expiresAt": "",
                    "createdAt": "",
                    "updatedAt": "",
                    "lastLoginAt": "",
                }
            })

            user = qa_app.load_users()["shuxing666"]

            self.assertEqual(user["role"], "admin")
            self.assertTrue(user["active"])
            self.assertTrue(user["systemLocked"])
            self.assertEqual(user["remark"], "系统默认管理员")

    def test_other_admin_cannot_modify_locked_default_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa_app.USERS_FILE = os.path.join(tmp, ".users.json")
            qa_app.ADMIN_USERNAME = "shuxing666"
            qa_app.app.config["TESTING"] = True
            qa_app.save_users({
                "shuxing666": {
                    "username": "shuxing666",
                    "passwordHash": qa_app.generate_password_hash("secret123"),
                    "role": "admin",
                    "active": True,
                    "expiresAt": "",
                    "remark": "系统默认管理员",
                    "systemLocked": True,
                    "createdAt": "",
                    "updatedAt": "",
                    "lastLoginAt": "",
                },
                "qingshan": {
                    "username": "qingshan",
                    "passwordHash": qa_app.generate_password_hash("secret123"),
                    "role": "admin",
                    "active": True,
                    "expiresAt": "",
                    "remark": "",
                    "systemLocked": False,
                    "createdAt": "",
                    "updatedAt": "",
                    "lastLoginAt": "",
                },
            })

            client = qa_app.app.test_client()
            with client.session_transaction() as sess:
                sess["username"] = "qingshan"

            cases = [
                ("/api/admin/users/update", {"username": "shuxing666", "role": "user"}),
                ("/api/admin/users/update", {"username": "shuxing666", "active": False}),
                ("/api/admin/users/update", {"username": "shuxing666", "remark": "changed"}),
                ("/api/admin/users/update", {"username": "shuxing666", "password": "newpass123"}),
                ("/api/admin/users/delete", {"username": "shuxing666"}),
            ]

            for url, payload in cases:
                with self.subTest(url=url, payload=payload):
                    response = client.post(url, json=payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.get_json()["success"])

            user = qa_app.load_users()["shuxing666"]
            self.assertEqual(user["role"], "admin")
            self.assertTrue(user["active"])
            self.assertEqual(user["remark"], "系统默认管理员")

    def test_ai_config_is_scoped_per_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa_app.USERS_FILE = os.path.join(tmp, ".users.json")
            qa_app.AI_CONFIG_FILE = os.path.join(tmp, ".ai_config.json")
            qa_app.save_users({
                "alice": {"username": "alice", "role": "admin", "active": True},
                "bob": {"username": "bob", "role": "admin", "active": True},
            })
            qa_app.save_json(qa_app.AI_CONFIG_FILE, {
                "baseUrl": "https://legacy.example/v1",
                "model": "legacy-model",
                "apiKey": "legacy-key",
                "temperature": 0.2,
                "maxTokens": 2048,
                "timeoutSeconds": 300,
            })

            qa_app.save_ai_config({
                "baseUrl": "https://alice.example/v1",
                "model": "alice-model",
                "apiKey": "alice-key",
                "temperature": 0.4,
                "maxTokens": 4096,
                "timeoutSeconds": 600,
            }, username="alice")

            alice = qa_app.load_ai_config("alice")
            bob = qa_app.load_ai_config("bob")

            self.assertEqual(alice["baseUrl"], "https://alice.example/v1")
            self.assertEqual(alice["model"], "alice-model")
            self.assertEqual(alice["apiKey"], "alice-key")
            self.assertEqual(bob["baseUrl"], "https://legacy.example/v1")
            self.assertEqual(bob["model"], "legacy-model")
            self.assertEqual(bob["apiKey"], "legacy-key")

if __name__ == "__main__":
    unittest.main()
