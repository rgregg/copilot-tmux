#!/usr/bin/env python3
"""Install the shared dev-box helpers while keeping credentials and history local."""

import argparse
import getpass
import json
from pathlib import Path
import shlex
import shutil
import socket
import sys

import install as recovery_installer
from devbox_helpers import BOXES


def backup(path):
    if path.exists():
        saved = path.with_name(path.name + ".before-devboxes")
        if not saved.exists():
            shutil.copy2(path, saved)


def install(home, shared_dir, name=None, identity=None, routes=None):
    if not shared_dir.is_dir():
        raise ValueError(f"Shared folder not found: {shared_dir}")
    config_path = home / ".config/devboxes/config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config.update(shared_dir=str(shared_dir), name=name or config.get("name", socket.gethostname()),
                  user=config.get("user", getpass.getuser()))
    for route in routes or []:
        destination, separator, via = route.partition("=")
        if not separator or destination not in BOXES or (via != "direct" and via not in BOXES):
            raise ValueError("--via must be BOX=HOP or BOX=direct, using known shortcut names.")
        destination = BOXES[destination]
        if via == "direct":
            config.setdefault("via", {}).pop(destination, None)
        else:
            via = BOXES[via]
            if via in {destination, config["name"]}:
                raise ValueError("An SSH hop must be a different remote box.")
            config.setdefault("via", {})[destination] = via
    if identity is not None:
        if not identity.is_file():
            raise ValueError(f"SSH identity not found: {identity}")
        if identity.stat().st_mode & 0o077:
            raise ValueError("SSH private key must be owner-only (chmod 600); do not use a Windows-mounted key.")
        config["ssh_identity"] = str(identity)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup(config_path)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    config_path.chmod(0o600)
    for relative in (".local/bin/copilot-tmux", ".local/lib/copilot-tmux/copilot_tmux.py",
                     ".copilot/hooks/tmux-recovery.json", ".config/tmux/copilot-recovery.conf",
                     ".tmux.conf"):
        backup(home / relative)
    recovery_installer.install(home)
    script = home / ".local/lib/copilot-tmux/devbox_helpers.py"
    shutil.copy2(Path(__file__).with_name("devbox_helpers.py"), script)
    for command in ("lobster", "lobsterclaw", *BOXES, "devbox-helpers", "devbox-update"):
        path = home / ".local/bin" / command
        backup(path)
        args = [] if command == "devbox-helpers" else ["update" if command == "devbox-update" else command]
        path.write_text("#!/bin/sh\nexport PATH=\"$HOME/.local/bin:$PATH\"\nexec " +
                        shlex.join([sys.executable, str(script), *args]) + ' "$@"\n')
        path.chmod(0o700)
    for filename in (".bashrc", ".profile"):
        path = home / filename
        existing = path.read_text() if path.exists() else ""
        marker = "# Shared devbox helpers"
        if marker not in existing:
            backup(path)
            with path.open("a") as output:
                output.write('\n' + marker + '\nexport PATH="$HOME/.local/bin:$PATH"\n')
    config_tmux = home / ".config/tmux/devboxes.conf"
    config_tmux.write_text("set -g mouse on\nset -s set-clipboard on\n")
    tmuxrc = home / ".tmux.conf"
    line = "source-file ~/.config/tmux/devboxes.conf"
    if line not in tmuxrc.read_text().splitlines():
        with tmuxrc.open("a") as output:
            output.write(line + "\n")
    print(f"Installed devbox helpers for {config['name']}. Run devbox-helpers doctor.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--shared-dir", type=Path, required=True)
    parser.add_argument("--name", help="This box's shortcut name, e.g. lobster3 or lobster4.")
    parser.add_argument("--ssh-identity", type=Path, help="Local owner-only private key; never put it in OneDrive.")
    parser.add_argument("--via", action="append", help="Route BOX through HOP: BOX=HOP; use BOX=direct to clear.")
    args = parser.parse_args()
    try:
        install(args.home.expanduser().resolve(), args.shared_dir.expanduser().resolve(),
                args.name, args.ssh_identity.expanduser().resolve() if args.ssh_identity else None, args.via)
    except (OSError, ValueError) as error:
        print(f"Devbox installation failed: {error}", file=sys.stderr)
        sys.exit(1)
