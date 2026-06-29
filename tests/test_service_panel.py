import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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
        self.assertEqual(cfg["QA_PUBLIC_URL"], "https://diamondruby.xyz")
        self.assertEqual(cfg["QA_TUNNEL_ENABLED"], "0")

    def test_build_runtime_config_preserves_env_values(self):
        cfg = service_panel.build_runtime_config({
            "QA_PORT": "5011",
            "QA_HOST": "127.0.0.1",
            "QA_DATA_DIR": r"D:\\qa-data",
        })

        self.assertEqual(cfg["QA_PORT"], "5011")
        self.assertEqual(cfg["QA_HOST"], "127.0.0.1")
        self.assertEqual(cfg["QA_DATA_DIR"], r"D:\\qa-data")

    def test_tunnel_enabled_accepts_truthy_values(self):
        self.assertTrue(service_panel.tunnel_enabled({"QA_TUNNEL_ENABLED": "1"}))
        self.assertTrue(service_panel.tunnel_enabled({"QA_TUNNEL_ENABLED": "true"}))
        self.assertFalse(service_panel.tunnel_enabled({"QA_TUNNEL_ENABLED": "0"}))

    def test_build_cloudflared_command_prefers_token(self):
        command = service_panel.build_cloudflared_command({
            "QA_TUNNEL_TOKEN": "token-value",
            "QA_TUNNEL_NAME": "myweb",
        })

        self.assertEqual(command[0], service_panel.CLOUDFLARED_EXE)
        self.assertIn("--edge-ip-version", command)
        self.assertIn("4", command)
        self.assertIn("--protocol", command)
        self.assertIn("http2", command)
        self.assertIn("--dns-resolver-addrs", command)
        self.assertIn("1.1.1.1:53", command)
        self.assertIn("--token", command)
        self.assertIn("token-value", command)
        self.assertNotEqual(command[-1], "myweb")

    def test_build_cloudflared_command_uses_tunnel_name_without_token(self):
        command = service_panel.build_cloudflared_command({
            "QA_TUNNEL_TOKEN": "",
            "QA_TUNNEL_NAME": "myweb",
        })

        self.assertEqual(command[-1], "myweb")

    def test_build_waitress_command_targets_wsgi_entrypoint(self):
        command = service_panel.build_waitress_command("5012")

        self.assertEqual(command[0], service_panel.venv_python())
        self.assertIn("waitress", command)
        self.assertIn("--listen=0.0.0.0:5012", command)
        self.assertEqual(command[-1], "wsgi:app")

    def test_parse_netstat_listening_pids_filters_by_port_and_state(self):
        output = """
  TCP    0.0.0.0:5000           0.0.0.0:0              LISTENING       1234
  TCP    127.0.0.1:5000         127.0.0.1:55000        ESTABLISHED     5678
  TCP    0.0.0.0:5001           0.0.0.0:0              LISTENING       9012
  TCP    [::]:5000              [::]:0                 LISTENING       1234
"""

        self.assertEqual(service_panel.parse_netstat_listening_pids(output, "5000"), [1234])

    def test_start_service_does_not_spawn_when_port_is_already_listening(self):
        panel = object.__new__(service_panel.ServicePanel)
        panel.running = False
        panel.config = {"QA_PORT": "5000"}
        panel.refresh_config = Mock()
        panel.update_status = Mock()
        panel.start_tunnel = Mock()

        with (
            patch.object(service_panel, "listening_pids_on_port", return_value=[4321]),
            patch.object(service_panel.subprocess, "Popen") as popen,
            patch.object(service_panel.messagebox, "showinfo") as showinfo,
        ):
            service_panel.ServicePanel.start_service(panel)

        popen.assert_not_called()
        panel.update_status.assert_called_once_with(True, "运行中 (PID: 4321)")
        panel.start_tunnel.assert_called_once()
        showinfo.assert_called_once()

    def test_check_status_starts_tunnel_when_service_port_is_running(self):
        panel = object.__new__(service_panel.ServicePanel)
        panel.config = {"QA_PORT": "5000"}
        panel.update_status = Mock()
        panel.start_tunnel = Mock()

        with (
            patch.object(service_panel, "read_pid", return_value=None),
            patch.object(service_panel, "listening_pids_on_port", return_value=[1234]),
        ):
            service_panel.ServicePanel.check_status(panel)

        panel.update_status.assert_called_once_with(True, "运行中 (PID: 1234)")
        panel.start_tunnel.assert_called_once()

    def test_service_files_live_in_project_root(self):
        self.assertEqual(service_panel.PID_FILE, os.path.join(service_panel.PROJECT_DIR, "server.pid"))
        self.assertEqual(service_panel.LOG_FILE, os.path.join(service_panel.PROJECT_DIR, "server.log"))


if __name__ == "__main__":
    unittest.main()
