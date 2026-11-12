#!/usr/bin/env python3
"""Install copilot-tmux and its hooks without replacing existing configuration."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys


HOOKS = (
    "after-rename-window", "after-rename-session", "window-linked",
    "after-select-pane", "after-select-window",
)


def tmux_config(script):
    def quote(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'

    command = shlex.join([sys.executable, str(script), "snapshot"])
    return "".join(
        f"set-hook -ag {hook} {quote('run-shell -b ' + quote(command))}\n"
        for hook in HOOKS
    )


def install(home):
    source = Path(__file__).resolve().parent / "copilot_tmux.py"
    script = home / ".local/lib/copilot-tmux/copilot_tmux.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    if source != script:
        shutil.copy2(source, script)
    command = home / ".local/bin/copilot-tmux"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(script)]) + ' "$@"\n')
    command.chmod(0o700)

    copilot_home = Path(os.environ.get("COPILOT_HOME", home / ".copilot"))
    hook_path = copilot_home / "hooks/tmux-recovery.json"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook = {
        "type": "command", "exec": sys.executable,
        "args": [str(script), "hook"], "timeoutSec": 15,
    }
    hook_path.write_text(json.dumps({
        "version": 1,
        "hooks": {event: [hook] for event in (
            "sessionStart", "userPromptSubmitted", "postToolUse",
        )},
    }, indent=2) + "\n")
    hook_path.chmod(0o600)

    config = home / ".config/tmux/copilot-recovery.conf"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(tmux_config(script))
    tmuxrc = home / ".tmux.conf"
    line = "source-file ~/.config/tmux/copilot-recovery.conf"
    existing = tmuxrc.read_text() if tmuxrc.exists() else ""
    if line not in existing.splitlines():
        with tmuxrc.open("a") as output:
            if existing and not existing.endswith("\n"):
                output.write("\n")
            output.write(line + "\n")
    print(f"Installed {command}")
    print("New tmux servers will load recovery hooks automatically.")
    print("For an existing server, run: tmux source-file ~/.tmux.conf")
    print("Start a new Copilot process to load its new hooks.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="Override the installation home directory.")
    args = parser.parse_args()
    try:
        install(args.home.expanduser().resolve())
    except OSError as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        sys.exit(1)
