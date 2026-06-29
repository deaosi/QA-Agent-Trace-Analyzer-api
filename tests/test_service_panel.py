import os
import sys
import tempfile
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import service_panel


class ServicePanelHelperTests(unittest.TestCase):
    def test_load_env_file_reads_key_value_pairs_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, ".env")
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write("# comment\nQA_PORT=5010\nQA_SECRET_KEY=abc=def\nEMPTY=\n")

            self.assertEqual(
                service_panel.load_env_file(env_path),
                {"QA_PORT": "5010", "QA_SECRET_KEY": "abc=def", "EMPTY": ""},
            )

    def test_build_runtime_config_uses_project_root_defaults(self):
        cfg = service_panel.build_runtime_config({})

        self.assertEqual(cfg["QA_PORT"], "5000")
        self.assertEqual(cfg["QA_HOST"], "0.0.0.0")
        self.assertEqual(cfg["QA_DATA_DIR"], os.path.join(service_panel.PROJECT_DIR, "data"))

    def test_build_runtime_config_preserves_env_values(self):
        cfg = service_panel.build_runtime_config({
            "QA_PORT": "5011",
            "QA_HOST": "127.0.0.1",
            "QA_DATA_DIR": r"D:\\qa-data",
        })

        self.assertEqual(cfg["QA_PORT"], "5011")
        self.assertEqual(cfg["QA_HOST"], "127.0.0.1")
        self.assertEqual(cfg["QA_DATA_DIR"], r"D:\\qa-data")

    def test_build_waitress_command_targets_wsgi_entrypoint(self):
        command = service_panel.build_waitress_command("5012")

        self.assertEqual(command[0], service_panel.venv_python())
        self.assertIn("waitress", command)
        self.assertIn("--listen=0.0.0.0:5012", command)
        self.assertEqual(command[-1], "wsgi:app")

    def test_service_files_live_in_project_root(self):
        self.assertEqual(service_panel.PID_FILE, os.path.join(service_panel.PROJECT_DIR, "server.pid"))
        self.assertEqual(service_panel.LOG_FILE, os.path.join(service_panel.PROJECT_DIR, "server.log"))


if __name__ == "__main__":
    unittest.main()
