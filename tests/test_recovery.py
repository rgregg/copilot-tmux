import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import copilot_tmux as recovery
import install as installer


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = recovery.Registry(self.root / "state")
        self.addCleanup(self.registry.db.close)
        self.identity = {
            "key": "old-source", "boot": "previous-boot",
            "socket": "/nonexistent", "pid": 1, "start": "0",
        }

    def entry(self):
        sid = str(uuid.uuid4())
        home = self.root / "copilot"
        (home / "session-state" / sid).mkdir(parents=True)
        return {
            "session_id": sid, "cwd": str(self.root), "copilot_home": str(home),
            "tmux_session": "work", "window_name": "issue-fix",
            "window_id": "@0", "window_index": 0, "pane_index": 0,
            "pid": 1, "start": "0", "boot": "previous-boot", "pane": "%0",
        }

    def test_empty_shutdown_and_new_server_preserve_old_workspace(self):
        entry = self.entry()
        self.registry.save(self.identity, [entry])
        changed = dict(entry, window_name="renamed")
        self.registry.save(self.identity, [changed])
        self.registry.save(self.identity, [])
        new_identity = dict(self.identity, key="new-source", boot=recovery.boot_id(),
                            pid=os.getpid(), start=recovery.process_start(os.getpid()))
        self.registry.save(new_identity, [self.entry()])
        source = recovery.choose_source(self.registry)
        self.assertEqual(source["key"], "old-source")
        self.assertEqual(source["entries"][0]["window_name"], "renamed")
        previous = recovery.choose_source(self.registry, "old", previous=True)
        self.assertEqual(previous["entries"][0]["window_name"], "issue-fix")
        self.assertEqual(self.registry.path.stat().st_mode & 0o777, 0o600)

    def test_plan_filters_duplicates_and_validates_recovery_paths(self):
        entry = self.entry()
        source = {"entries": [entry, dict(entry, pane="%1")]}
        self.assertEqual(len(recovery.recovery_plan(source, None, set())), 1)
        self.assertEqual(recovery.recovery_plan(source, None, {entry["session_id"]})[0][1],
                         "already running")
        with self.assertRaisesRegex(ValueError, "not present"):
            recovery.recovery_plan(source, ["unknown"], set())
        source["entries"] = [dict(entry, cwd=str(self.root / "missing"))]
        self.assertEqual(recovery.recovery_plan(source, None, set())[0][1], "missing directory")
        source["entries"] = [dict(entry, copilot_home=str(self.root / "missing"))]
        self.assertEqual(recovery.recovery_plan(source, None, set())[0][1], "missing Copilot history")

    def test_hook_outside_tmux_is_noop(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stdin", io.StringIO("{}")):
            recovery.hook(self.registry)
        self.assertEqual(self.registry.workspaces(), [])

    def test_shutdown_cannot_replace_full_workspace_with_surviving_subset(self):
        entries = [self.entry(), self.entry()]
        self.registry.save(self.identity, entries)
        with patch.object(recovery, "system_stopping", return_value=True):
            # No tmux object is needed: shutdown must not even enumerate dying panes.
            recovery.snapshot(self.registry, None)
        self.assertEqual(self.registry.workspaces()[0]["entries"], entries)

    def test_failed_apply_does_not_create_partial_workspace(self):
        entry = dict(self.entry(), cwd=str(self.root / "missing"))
        self.registry.save(self.identity, [entry])
        args = recovery.argparse.Namespace(source=None, previous=False, session=None, apply=True)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "blocked"):
            recovery.restore(self.registry, recovery.Tmux("/not-a-socket"), args)


class TmuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="copilot-tmux-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.home = self.root / "copilot-home"
        self.cwd = self.root / "work 'quoted' $(touch NOT_EXECUTED)"
        self.cwd.mkdir()
        self.fake_bin = Path(__file__).parent / "fake_bin"
        self.env = {
            **os.environ,
            "XDG_STATE_HOME": str(self.state),
            "COPILOT_HOME": str(self.home),
            "PATH": str(self.fake_bin) + os.pathsep + os.environ["PATH"],
        }
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_PANE", None)
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.sockets = [str(self.root / "old.sock"), str(self.root / "new.sock")]
        self.addCleanup(self.stop_servers)
        self.registry = recovery.Registry()
        self.addCleanup(self.registry.db.close)
        self.source = recovery.Tmux(self.sockets[0])
        self.target = recovery.Tmux(self.sockets[1])
        self.conf = self.root / "tmux.conf"
        self.conf.write_text("set -g mouse on\n")

    def stop_servers(self):
        for socket in self.sockets:
            subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)

    def wait_for(self, condition, message):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(0.05)
        self.fail(message)

    def add_session(self, sid):
        (self.home / "session-state" / sid).mkdir(parents=True)

    def invocations(self):
        log = self.state / "invocations.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_no_arguments_start_new_then_attach_without_duplicate(self):
        with patch.object(self.target, "attach") as attach:
            recovery.start_or_restore(self.registry, self.target)
            attach.assert_called_once_with("copilot")
        self.wait_for(lambda: len(self.invocations()) == 1, "Fresh Copilot did not start.")
        self.assertEqual(self.invocations()[0]["args"][0], "--session-id")
        self.wait_for(lambda: bool(self.registry.workspaces()), "Fresh session was not saved.")
        with patch.object(self.target, "attach") as attach:
            recovery.start_or_restore(self.registry, self.target)
            attach.assert_called_once_with("copilot")
        self.assertEqual(len(self.target.panes()), 1)

    def test_no_arguments_restore_saved_workspace_and_attach(self):
        sid = str(uuid.uuid4())
        self.add_session(sid)
        entry = {
            "session_id": sid, "cwd": str(self.cwd), "copilot_home": str(self.home),
            "tmux_session": "saved work", "window_name": "123-fix",
            "window_id": "@7", "window_index": 1, "pane_index": 0,
        }
        identity = {"key": "offline", "boot": "old-boot",
                    "socket": self.sockets[0], "pid": 1, "start": "0"}
        self.registry.save(identity, [entry])
        with patch.object(self.target, "attach") as attach, contextlib.redirect_stdout(io.StringIO()):
            recovery.start_or_restore(self.registry, self.target)
            attach.assert_called_once_with("saved work")
        self.wait_for(lambda: len(self.invocations()) == 1, "Saved Copilot did not resume.")
        self.assertEqual(self.invocations()[0]["args"], ["--resume", sid])

    def test_shutdown_restore_rename_split_and_duplicate_protection(self):
        sid1, sid2, sid3 = (str(uuid.uuid4()) for _ in range(3))
        self.add_session(sid1)
        self.add_session(sid2)
        self.add_session(sid3)
        fake = str(self.fake_bin / "copilot")
        pane = self.source.run(
            "-f", str(self.conf), "new-session", "-d", "-P", "-F", "#{pane_id}",
            "-s", "work space", "-n", "old-name", "-c", str(self.cwd),
            fake, "--resume", sid1,
        ).stdout.strip()
        hooks = self.root / "recovery.conf"
        hooks.write_text(installer.tmux_config(Path(recovery.SCRIPT)))
        self.source.run("source-file", str(hooks))
        self.wait_for(lambda: bool(self.registry.workspaces()), "Initial hook did not save workspace.")
        self.source.run("split-window", "-d", "-t", pane, "-c", str(self.cwd), fake, "--resume", sid2)
        self.wait_for(
            lambda: len(self.registry.workspaces()[0]["entries"]) == 2,
            "Second hook did not save both panes.",
        )
        self.source.run(
            "new-window", "-d", "-t", "=work space:", "-n", "other-task", "-c", str(self.cwd),
            fake, "--resume", sid3,
        )
        self.wait_for(
            lambda: len(self.registry.workspaces()[0]["entries"]) == 3,
            "Third hook did not save the second window.",
        )
        source_key = self.registry.workspaces()[0]["key"]
        renamed = "123-fix 'quote' $(touch NOT_EXECUTED)"
        self.source.run("rename-window", "-t", pane, renamed)
        self.wait_for(
            lambda: all(e["window_name"] == renamed for e in self.registry.workspaces()[0]["entries"]
                        if e["session_id"] in {sid1, sid2}),
            "Idle window rename hook did not update saved names.",
        )
        self.source.run("rename-session", "-t", "=work space", "new work")
        self.wait_for(
            lambda: all(e["tmux_session"] == "new work" for e in self.registry.workspaces()[0]["entries"]),
            "Session rename hook did not update saved names.",
        )
        self.source.run("kill-server")
        self.wait_for(
            lambda: not recovery.server_alive(self.registry.workspaces()[0]["identity"]),
            "Old tmux server did not stop.",
        )
        args = recovery.argparse.Namespace(source=None, previous=False, session=None, apply=False)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            recovery.restore(self.registry, self.target, args)
        self.assertIn("Preview only", output.getvalue())
        self.assertFalse(Path(self.sockets[1]).exists())
        self.assertEqual(recovery.choose_source(self.registry)["key"], source_key)
        args.apply = True
        with contextlib.redirect_stdout(io.StringIO()):
            recovery.restore(self.registry, self.target, args)
        self.wait_for(lambda: len(self.invocations()) == 6, "All restored processes did not start.")
        self.assertEqual(len(self.target.panes()), 3)
        self.assertEqual(len(self.target.run("list-windows", "-t", "=new work").stdout.splitlines()), 2)
        for new_pane in self.target.panes():
            sid = self.target.marker(new_pane)["session_id"]
            self.assertEqual(self.target.display(new_pane, "window_name"),
                             "other-task" if sid == sid3 else renamed)
            self.assertEqual(self.target.display(new_pane, "session_name"), "new work")
            self.assertEqual(self.target.display(new_pane, "pane_current_path"), str(self.cwd))
        self.assertFalse((self.cwd / "NOT_EXECUTED").exists())
        self.assertEqual({e["session_id"] for e in self.registry.workspaces()[-1]["entries"]},
                         {sid1, sid2, sid3})
        with contextlib.redirect_stdout(io.StringIO()) as output:
            recovery.restore(self.registry, self.target, args)
        self.assertIn("Opened 0 Copilot session(s)", output.getvalue())
        self.assertEqual(len(self.target.panes()), 3)
        self.assertEqual({tuple(item["args"]) for item in self.invocations()},
                         {("--resume", sid1), ("--resume", sid2), ("--resume", sid3)})


if __name__ == "__main__":
    unittest.main()
