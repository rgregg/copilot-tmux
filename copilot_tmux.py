#!/usr/bin/env python3
"""Persist Copilot's tmux workspace independently of the tmux server."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import uuid


MARKER = "@copilot_recovery"
SCRIPT = str(Path(__file__).resolve())


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def process_start(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return None if fields[0] == "Z" else fields[19]


def find_copilot_process():
    pid = os.getppid()
    while pid > 1:
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            status = Path(f"/proc/{pid}/status").read_text().splitlines()
        except (FileNotFoundError, ProcessLookupError):
            break
        if any(
            Path(os.fsdecode(arg)).name in {"copilot", "copilot-cli"}
            or b"/@github/copilot/" in arg
            for arg in args[:3] if arg
        ):
            start = process_start(pid)
            if start is not None:
                return pid, start
        pid = int(next(line.split()[1] for line in status if line.startswith("PPid:")))
    raise RuntimeError("Could not identify the Copilot process for this hook.")


def socket_from_env():
    value = os.environ.get("TMUX")
    return value.rsplit(",", 2)[0] if value else None


class Tmux:
    def __init__(self, socket=None):
        self.socket = socket or socket_from_env()

    def run(self, *args, check=True):
        command = ["tmux"]
        if self.socket:
            command += ["-S", self.socket]
        result = subprocess.run(command + list(args), text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(f"tmux {args[0]}: {result.stderr.strip()}")
        return result

    def display(self, pane, field):
        return self.run("display-message", "-p", "-t", pane, "-F", f"#{{{field}}}").stdout.removesuffix("\n")

    def panes(self):
        return self.run("list-panes", "-a", "-F", "#{pane_id}").stdout.splitlines()

    def sessions(self):
        result = self.run("list-sessions", "-F", "#{session_name}", check=False)
        if result.returncode:
            if any(text in result.stderr for text in ("no server running", "No such file or directory")):
                return []
            raise RuntimeError(result.stderr.strip())
        return result.stdout.splitlines()

    def attach(self, name):
        command = ["tmux"]
        if self.socket:
            command += ["-S", self.socket]
        action = "switch-client" if socket_from_env() == self.socket and os.environ.get("TMUX") else "attach-session"
        result = subprocess.run(command + [action, "-t", "=" + name])
        if result.returncode:
            raise RuntimeError(f"tmux {action} exited with status {result.returncode}.")

    def marker(self, pane):
        result = self.run("show-options", "-p", "-v", "-t", pane, MARKER, check=False)
        if result.returncode:
            # An absent user option is expected; a vanished pane is not.
            self.display(pane, "pane_id")
            return None
        return json.loads(result.stdout)

    def mark(self, pane, record):
        self.run("set-option", "-p", "-t", pane, MARKER, json.dumps(record))

    def identity(self, pane):
        socket = self.display(pane, "socket_path")
        pid = int(self.display(pane, "pid"))
        start = process_start(pid)
        if start is None:
            raise RuntimeError("The tmux server exited while taking its snapshot.")
        boot = boot_id()
        key = hashlib.sha256(f"{boot}\0{socket}\0{pid}\0{start}".encode()).hexdigest()[:16]
        return {"key": key, "boot": boot, "socket": socket, "pid": pid, "start": start}


class Registry:
    def __init__(self, directory=None):
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        override = directory or os.environ.get("COPILOT_TMUX_STATE_DIR")
        self.directory = Path(override) if override else root / "copilot-tmux"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "workspaces.sqlite3"
        self.db = sqlite3.connect(self.path, timeout=15)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS workspaces (
                key TEXT PRIMARY KEY, updated TEXT NOT NULL,
                identity TEXT NOT NULL, snapshot TEXT NOT NULL,
                previous_snapshot TEXT
            )"""
        )
        self.db.commit()

    def save(self, identity, entries):
        if not entries:
            # Shutdown must not replace the last usable workspace with an empty one.
            return
        value = json.dumps(entries, sort_keys=True)
        with self.db:
            self.db.execute(
                """INSERT INTO workspaces VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(key) DO UPDATE SET
                    updated=excluded.updated,
                    previous_snapshot=CASE
                        WHEN snapshot != excluded.snapshot THEN snapshot
                        ELSE previous_snapshot END,
                    snapshot=excluded.snapshot""",
                (identity["key"], timestamp(), json.dumps(identity), value),
            )

    def workspaces(self):
        return [
            {"key": key, "updated": updated, "identity": json.loads(identity),
             "entries": json.loads(snapshot),
             "previous": json.loads(previous) if previous else None}
            for key, updated, identity, snapshot, previous in self.db.execute(
                "SELECT key, updated, identity, snapshot, previous_snapshot "
                "FROM workspaces ORDER BY updated DESC"
            )
        ]

    @contextmanager
    def lock(self, name):
        with (self.directory / (name + ".lock")).open("a") as handle:
            os.chmod(handle.name, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def server_alive(identity):
    return identity["boot"] == boot_id() and process_start(identity["pid"]) == identity["start"]


def record_alive(record):
    return record["boot"] == boot_id() and process_start(record["pid"]) == record["start"]


def system_stopping():
    if not Path("/run/systemd/system").is_dir():
        return False
    result = subprocess.run(
        ["systemctl", "is-system-running"], text=True, capture_output=True, timeout=3,
    )
    state = result.stdout.strip()
    if not state:
        raise RuntimeError("Could not check shutdown state: " + result.stderr.strip())
    return state == "stopping"


def snapshot(registry, tmux):
    with registry.lock("snapshot"):
        # Preserve the full workspace before shutdown starts killing its processes.
        if system_stopping():
            return
        capture_snapshot(registry, tmux)


def capture_snapshot(registry, tmux):
    panes = tmux.panes()
    if not panes:
        return
    identity = tmux.identity(panes[0])
    entries = []
    for pane in panes:
        record = tmux.marker(pane)
        if record is None or not record_alive(record):
            continue
        record.update({
            "pane": pane,
            "tmux_session": tmux.display(pane, "session_name"),
            "window_id": tmux.display(pane, "window_id"),
            "window_name": tmux.display(pane, "window_name"),
            "window_index": int(tmux.display(pane, "window_index")),
            "pane_index": int(tmux.display(pane, "pane_index")),
        })
        entries.append(record)
    registry.save(identity, entries)


def hook(registry):
    payload = json.load(sys.stdin)
    if not socket_from_env() or not os.environ.get("TMUX_PANE"):
        return
    session_id = str(uuid.UUID(payload["sessionId"]))
    cwd = payload["cwd"]
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("The Copilot hook must provide an absolute cwd.")
    pid, start = find_copilot_process()
    tmux = Tmux()
    tmux.mark(os.environ["TMUX_PANE"], {
        "session_id": session_id, "cwd": cwd,
        "copilot_home": str(Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot")).absolute()),
        "pid": pid, "start": start, "boot": boot_id(),
    })
    snapshot(registry, tmux)


def choose_source(registry, key=None, previous=False):
    workspaces = registry.workspaces()
    if key:
        matches = [item for item in workspaces if item["key"].startswith(key)]
        if len(matches) != 1:
            raise ValueError(f"Workspace {key!r} must match exactly one saved source.")
        source = matches[0]
    else:
        offline = [item for item in workspaces if not server_alive(item["identity"])]
        choices = offline or workspaces
        if not choices:
            raise ValueError("No workspace is saved yet. Start Copilot inside tmux to register it.")
        source = choices[0]
    if previous:
        if source["previous"] is None:
            raise ValueError("This workspace does not have a previous snapshot.")
        source = dict(source, entries=source["previous"])
    return source


def running_sessions(registry):
    result = set()
    for workspace in registry.workspaces():
        if not server_alive(workspace["identity"]):
            continue
        tmux = Tmux(workspace["identity"]["socket"])
        for pane in tmux.panes():
            record = tmux.marker(pane)
            if record and record_alive(record):
                result.add(record["session_id"])
    return result


def recovery_plan(source, selected, running):
    entries = source["entries"]
    if selected:
        missing = set(selected) - {entry["session_id"] for entry in entries}
        if missing:
            raise ValueError("Session IDs not present in this snapshot: " + ", ".join(sorted(missing)))
        entries = [entry for entry in entries if entry["session_id"] in selected]
    seen = set()
    plan = []
    for entry in sorted(entries, key=lambda e: (e["tmux_session"], e["window_index"], e["pane_index"])):
        sid = entry["session_id"]
        if sid in seen:
            continue
        seen.add(sid)
        if sid in running:
            status = "already running"
        elif not Path(entry["cwd"]).is_dir():
            status = "missing directory"
        elif not (Path(entry["copilot_home"]) / "session-state" / sid).is_dir():
            status = "missing Copilot history"
        else:
            status = "restore"
        plan.append((entry, status))
    return plan


def restore(registry, tmux, args):
    with registry.lock("restore"):
        source = choose_source(registry, args.source, args.previous)
        plan = recovery_plan(source, args.session, running_sessions(registry))
        print(f"Workspace {source['key']} saved {source['updated']}")
        for entry, status in plan:
            label = f"{entry['tmux_session']} / {entry['window_name']}"
            print(f"  [{status}] {label!r}\n    {entry['session_id']}  {entry['cwd']!r}")
        if not args.apply:
            print("Preview only. Add --apply to reopen the sessions marked [restore].")
            return
        blocked = [status for _, status in plan if status not in {"restore", "already running"}]
        if blocked:
            raise ValueError("Restore blocked by missing paths/history. Select valid sessions with --session UUID.")
        executable = shutil.which("copilot")
        if executable is None:
            raise RuntimeError("copilot is not on PATH.")
        windows = {}
        restored = 0
        for entry, status in plan:
            if status != "restore":
                continue
            name = entry["tmux_session"]
            group = (name, entry["window_id"])
            command = [
                sys.executable, SCRIPT, "--state-dir", str(registry.directory), "launch",
                "--session-id", entry["session_id"],
                "--cwd", entry["cwd"],
                "--copilot-home", entry["copilot_home"],
                "--executable", executable,
            ]
            if group in windows:
                result = tmux.run(
                    "split-window", "-d", "-P", "-F", "#{pane_id}", "-t", windows[group],
                    "-c", entry["cwd"], *command,
                )
                tmux.run("select-layout", "-t", windows[group], "tiled")
            else:
                # Enumerating names avoids tmux's prefix/pattern target matching.
                existing = tmux.sessions()
                if name in existing:
                    result = tmux.run(
                        "new-window", "-d", "-P", "-F", "#{pane_id}", "-t", f"={name}:",
                        "-n", entry["window_name"], "-c", entry["cwd"], *command,
                    )
                else:
                    result = tmux.run(
                        "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", name,
                        "-n", entry["window_name"], "-c", entry["cwd"], *command,
                    )
                windows[group] = result.stdout.strip()
            pane = result.stdout.strip()
            tmux.run("set-window-option", "-t", pane, "automatic-rename", "off")
            pid = int(tmux.display(pane, "pane_pid"))
            start = process_start(pid)
            if start is None or tmux.display(pane, "pane_dead") == "1":
                raise RuntimeError(f"Copilot exited while restoring {entry['session_id']}.")
            if tmux.marker(pane) is None:
                tmux.mark(pane, {
                    "session_id": entry["session_id"], "cwd": entry["cwd"],
                    "copilot_home": entry["copilot_home"],
                    "pid": pid, "start": start, "boot": boot_id(),
                })
            snapshot(registry, tmux)
            restored += 1
        print(f"Opened {restored} Copilot session(s).")
        names = sorted({entry["tmux_session"] for entry, status in plan if status == "restore"})
        for name in names:
            print("Attach: " + shlex.join(["tmux", *(
                ["-S", tmux.socket] if tmux.socket else []
            ), "attach-session", "-t", "=" + name]))


def launch(args, registry):
    # Register before exec so a second restore cannot open a duplicate during startup.
    tmux = Tmux()
    start = process_start(os.getpid())
    if start is None:
        raise RuntimeError("Could not identify the restored process.")
    record = {
        "session_id": str(uuid.UUID(args.session_id)), "cwd": args.cwd,
        "copilot_home": args.copilot_home, "pid": os.getpid(),
        "start": start, "boot": boot_id(),
    }
    tmux.mark(os.environ["TMUX_PANE"], record)
    snapshot(registry, tmux)
    os.chdir(args.cwd)
    os.environ["COPILOT_HOME"] = args.copilot_home
    os.environ["COPILOT_TMUX_STATE_DIR"] = str(registry.directory)
    os.environ.pop("COPILOT_AGENT_SESSION_ID", None)
    flag = "--session-id" if args.new else "--resume"
    os.execv(args.executable, [args.executable, flag, args.session_id])


def start_or_restore(registry, tmux):
    with registry.lock("startup"):
        existing = tmux.sessions()
        if existing:
            name = existing[0]
        elif registry.workspaces():
            source = choose_source(registry)
            restore(registry, tmux, argparse.Namespace(
                source=source["key"], previous=False, session=None, apply=True,
            ))
            name = source["entries"][0]["tmux_session"]
        else:
            executable = shutil.which("copilot")
            if executable is None:
                raise RuntimeError("copilot is not on PATH.")
            name = "copilot"
            command = [
                sys.executable, SCRIPT, "--state-dir", str(registry.directory),
                "launch", "--new", "--session-id", str(uuid.uuid4()),
                "--cwd", os.getcwd(),
                "--copilot-home", str(Path(os.environ.get(
                    "COPILOT_HOME", Path.home() / ".copilot")).absolute()),
                "--executable", executable,
            ]
            tmux.run("new-session", "-d", "-s", name, "-n", "copilot",
                     "-c", os.getcwd(), *command)
    tmux.attach(name)


def main():
    parser = argparse.ArgumentParser(prog="copilot-tmux", description=__doc__)
    parser.add_argument("--state-dir", help="Override the persistent registry directory.")
    parser.add_argument("--socket", help="Target a specific tmux socket.")
    parser.epilog = "With no command, attach to tmux, restore the latest workspace, or start Copilot."
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("hook", help="Register a Copilot hook (internal).")
    commands.add_parser("snapshot", help="Refresh names and paths of tracked tmux panes.")
    commands.add_parser("list", help="List saved workspace sources.")
    recovery = commands.add_parser("restore", help="Preview a saved workspace; --apply reopens it.")
    recovery.add_argument("--apply", action="store_true")
    recovery.add_argument("--source", help="Workspace source ID from list.")
    recovery.add_argument("--previous", action="store_true", help="Use the source's previous snapshot.")
    recovery.add_argument("--session", action="append", help="Restore only this session UUID; repeatable.")
    launcher = commands.add_parser("launch", help="Launch a restored session (internal).")
    launcher.add_argument("--new", action="store_true", help=argparse.SUPPRESS)
    for flag in ("session-id", "cwd", "copilot-home", "executable"):
        launcher.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    registry = Registry(args.state_dir)
    tmux = Tmux(args.socket)
    try:
        if args.command is None:
            start_or_restore(registry, tmux)
        elif args.command == "hook":
            hook(registry)
        elif args.command == "snapshot":
            snapshot(registry, tmux)
        elif args.command == "list":
            workspaces = registry.workspaces()
            if not workspaces:
                print("No saved workspaces yet. Start Copilot inside tmux to register it.")
            for item in workspaces:
                state = "live" if server_alive(item["identity"]) else "offline"
                print(f"{item['key']}  {state:7}  {item['updated']}  {len(item['entries'])} session(s)")
                for entry in item["entries"]:
                    print(f"  {entry['tmux_session']} / {entry['window_name']}  {entry['session_id']}")
        elif args.command == "restore":
            restore(registry, tmux, args)
        elif args.command == "launch":
            launch(args, registry)
    finally:
        registry.db.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(f"copilot-tmux: {error}", file=sys.stderr)
        sys.exit(1)
