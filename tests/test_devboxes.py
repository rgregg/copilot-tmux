import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import devbox_helpers as helpers
import setup_devboxes as setup


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devbox-helpers-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.shared = self.home / "shared with spaces"
        self.shared.mkdir()
        self.config = {"name": "local", "shared_dir": str(self.shared), "user": "test"}

    def test_repo_launches_in_correct_case_and_forwards_arguments(self):
        repo = self.home / "github/bic/LobsterClaw"
        repo.mkdir(parents=True)
        for inside in (False, True):
            with self.subTest(inside_tmux=inside), patch.object(Path, "home", return_value=self.home), \
                    patch.dict(os.environ, {"TMUX": "test"} if inside else {}, clear=True), \
                    patch.object(os, "execvp", side_effect=SystemExit) as execute, \
                    contextlib.chdir(self.home):
                with self.assertRaises(SystemExit):
                    helpers.run_repo("lobsterclaw", ["--resume", "a quoted id"])
                self.assertEqual(Path.cwd(), repo)
                expected = (["copilot", "--yolo"] if inside else
                            ["copilot-tmux", "start", "--yolo", "--"])
                execute.assert_called_once_with(expected[0], expected + ["--resume", "a quoted id"])

    def test_missing_checkout_cannot_launch_copilot_in_home(self):
        with patch.object(Path, "home", return_value=self.home), \
                patch.dict(os.environ, {}, clear=True), patch.object(os, "execvp") as execute:
            with self.assertRaisesRegex(ValueError, "LOBSTER_REPO"):
                helpers.run_repo("lobster", [])
        execute.assert_not_called()

    def test_remote_command_is_quoted_identity_selected_and_exit_propagated(self):
        (self.shared / "lobster3-key.txt").write_text("private-address\n")
        identity = self.home / "identity"
        identity.touch()
        self.config["ssh_identity"] = str(identity)
        with patch.object(helpers.shutil, "which", return_value="/tools/tailcat"), \
                patch.object(helpers.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 7),
                ]) as run, patch.object(helpers.sys.stdin, "isatty", return_value=False), \
                patch.object(helpers.sys.stdout, "isatty", return_value=False):
            with self.assertRaises(SystemExit) as error:
                helpers.connect("lobster3", ["printf", "%s", "$(touch BAD)"], self.config)
        self.assertEqual(error.exception.code, 7)
        command = run.call_args.args[0]
        self.assertIn(str(identity), command)
        self.assertIn("test@lobster3-tailcat", command)
        self.assertEqual(command[-1],
                         'export PATH="$HOME/.local/bin:$PATH"; exec printf %s \'$(touch BAD)\'')

    def test_own_box_does_not_connect_over_network(self):
        self.config["name"] = "lobster3"
        with patch.object(os, "execvp", side_effect=SystemExit) as execute, \
                patch.object(helpers.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                helpers.connect("lobster3", ["true"], self.config)
        execute.assert_called_once_with("true", ["true"])
        run.assert_not_called()

    def test_authenticated_hop_runs_destination_shortcut_with_literal_arguments(self):
        (self.shared / "rygregg-ubuntu-key.txt").write_text("private-address")
        self.config["via"] = {"lobster4": "rygregg-ubuntu"}
        with patch.object(helpers.shutil, "which", return_value="/tools/tailcat"), \
                patch.object(helpers.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
                ]) as run, patch.object(helpers.sys.stdin, "isatty", return_value=False), \
                patch.object(helpers.sys.stdout, "isatty", return_value=False):
            with self.assertRaises(SystemExit):
                helpers.connect("lobster4", ["printf", "a quoted value"], self.config)
        command = run.call_args.args[0]
        self.assertIn("test@rygregg-ubuntu-tailcat", command)
        self.assertEqual(command[-1],
                         'export PATH="$HOME/.local/bin:$PATH"; exec tail-lobster4 printf \'a quoted value\'')

    def test_remote_disconnect_resets_terminal_mouse_modes(self):
        (self.shared / "lobster3-key.txt").write_text("private-address")
        output = io.StringIO()
        with patch.object(helpers.shutil, "which", return_value="/tools/tailcat"), \
                patch.object(helpers.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 255),
                ]), patch.object(helpers.sys.stdin, "isatty", return_value=True), \
                patch.object(helpers.sys, "stdout", output), patch.object(output, "isatty", return_value=True):
            with self.assertRaises(SystemExit):
                helpers.connect("lobster3", [], self.config)
        self.assertEqual(output.getvalue(), helpers.MOUSE_RESET)

    def test_missing_or_invalid_address_is_explicit_and_never_prints_secret(self):
        with self.assertRaisesRegex(ValueError, "sync"):
            helpers.connect("lobster3", [], self.config)
        (self.shared / "lobster3-key.txt").write_text("private-address")
        with patch.object(helpers.shutil, "which", return_value="/tools/tailcat"), \
                patch.object(helpers.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(ValueError, "not a valid") as error:
                helpers.connect("lobster3", [], self.config)
        self.assertNotIn("private-address", str(error.exception))

    def test_install_is_repeatable_preserves_config_and_backs_up_original_helpers(self):
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "lobster").write_text("original lobster\n")
        (self.home / ".bashrc").write_text("original bashrc\n")
        (self.home / ".tmux.conf").write_text("set -g mouse on\n")
        identity = self.home / "identity"
        identity.touch(mode=0o600)
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            setup.install(self.home, self.shared, "lobster3", identity,
                          ["tail-lobster4=tail-ubuntu"])
            setup.install(self.home, self.shared)
        config = json.loads((self.home / ".config/devboxes/config.json").read_text())
        self.assertEqual(config["name"], "lobster3")
        self.assertEqual(config["ssh_identity"], str(identity))
        self.assertEqual(config["via"], {"lobster4": "rygregg-ubuntu"})
        self.assertEqual((bin_dir / "lobster.before-devboxes").read_text(), "original lobster\n")
        self.assertEqual((self.home / ".bashrc").read_text().count("# Shared devbox helpers"), 1)
        self.assertEqual((self.home / ".tmux.conf").read_text().count(
            "source-file ~/.config/tmux/devboxes.conf"), 1)
        self.assertEqual((bin_dir / "tail-lobster3").stat().st_mode & 0o777, 0o700)

    def test_install_retires_owned_legacy_wrappers_but_preserves_custom_helpers(self):
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        script = self.home / ".local/lib/copilot-tmux/devbox_helpers.py"
        interpreter_alias = self.home / "python-alias"
        interpreter_alias.symlink_to(sys.executable)
        for command in ("lobster3", "lobster4", "lobster-3", "lobster-4", "rygregg-ubuntu"):
            executable = str(interpreter_alias) if command == "lobster3" else sys.executable
            (bin_dir / command).write_text(
                "#!/bin/sh\nexport PATH=\"$HOME/.local/bin:$PATH\"\nexec " +
                shlex.join([executable, str(script), command]) + ' "$@"\n')
        (bin_dir / "ubuntu").write_text("custom ubuntu launcher\n")
        errors = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(errors):
            setup.install(self.home, self.shared, "lobster3", routes=["lobster4=ubuntu"])
        for command in ("lobster3", "lobster4", "lobster-3", "lobster-4", "rygregg-ubuntu"):
            self.assertFalse((bin_dir / command).exists())
        self.assertEqual((bin_dir / "ubuntu").read_text(), "custom ubuntu launcher\n")
        self.assertIn("Preserving customized legacy helper", errors.getvalue())
        config = json.loads((self.home / ".config/devboxes/config.json").read_text())
        self.assertEqual(config["via"], {"lobster4": "rygregg-ubuntu"})
        self.assertTrue((bin_dir / "tail-lobster4").exists())

    def test_renamed_command_dispatches_to_unchanged_box_identity(self):
        with patch.object(helpers.sys, "argv", ["devbox-helpers", "tail-lobster3", "hostname"]), \
                patch.object(helpers, "load_config", return_value=self.config), \
                patch.object(helpers, "connect") as connect:
            helpers.main()
        connect.assert_called_once_with("lobster3", ["hostname"], self.config)

    def test_install_rejects_windows_mounted_identity_permissions(self):
        identity = self.home / "identity"
        identity.touch(mode=0o644)
        identity.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            setup.install(self.home, self.shared, identity=identity)
