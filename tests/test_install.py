import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import install as installer


class InstallTests(unittest.TestCase):
    def test_install_preserves_configuration_and_is_repeatable(self):
        with tempfile.TemporaryDirectory(prefix="copilot-tmux-install-") as directory:
            home = Path(directory) / "home with spaces"
            home.mkdir()
            tmuxrc = home / ".tmux.conf"
            tmuxrc.write_text("set -g mouse on")
            hooks = home / ".copilot/hooks"
            hooks.mkdir(parents=True)
            existing = hooks / "existing.json"
            existing.write_text('{"keep": true}\n')
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
                installer.install(home)
                installer.install(home)
            self.assertEqual(existing.read_text(), '{"keep": true}\n')
            self.assertEqual(tmuxrc.read_text(),
                             "set -g mouse on\nsource-file ~/.config/tmux/copilot-recovery.conf\n")
            hook = json.loads((hooks / "tmux-recovery.json").read_text())
            script = home / ".local/lib/copilot-tmux/copilot_tmux.py"
            self.assertEqual(hook["hooks"]["sessionStart"][0]["args"], [str(script), "hook"])
            command = home / ".local/bin/copilot-tmux"
            result = subprocess.run([str(command), "--help"], check=True, capture_output=True, text=True)
            self.assertIn("restore", result.stdout)
            base = ["tmux", "-S", str(Path(directory) / "test.sock")]
            try:
                subprocess.run(base + ["-f", "/dev/null", "new-session", "-d",
                                       "-s", "install-test", "sleep", "30"],
                               check=True, capture_output=True)
                subprocess.run(base + ["source-file", str(home / ".config/tmux/copilot-recovery.conf")],
                               check=True, capture_output=True)
            finally:
                subprocess.run(base + ["kill-server"], capture_output=True)
