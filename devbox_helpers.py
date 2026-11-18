#!/usr/bin/env python3
"""Consistent repository and remote-box launchers for Linux and WSL."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


BOXES = {"lobster3": "lobster3", "lobster4": "lobster4", "ubuntu": "rygregg-ubuntu",
         "rygregg-ubuntu": "rygregg-ubuntu", "lobster-3": "lobster3", "lobster-4": "lobster4"}
MOUSE_RESET = "\033[?9l\033[?1000l\033[?1001l\033[?1002l\033[?1003l\033[?1005l\033[?1006l\033[?1015l\033[?1016l"


def load_config():
    path = Path.home() / ".config/devboxes/config.json"
    if not path.is_file():
        raise ValueError("Run setup_devboxes.py --shared-dir PATH first.")
    return json.loads(path.read_text())


def repository(name):
    variable = "LOBSTERCLAW_REPO" if name == "lobsterclaw" else "LOBSTER_REPO"
    override = os.environ.get(variable)
    candidates = [Path(override).expanduser()] if override else [
        Path.home() / "github/bic" / item
        for item in (("lobsterclaw", "LobsterClaw") if name == "lobsterclaw" else ("lobster",))
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError(f"{name} checkout not found; set {variable} to its directory.")


def run_repo(name, args):
    os.chdir(repository(name))
    if os.environ.get("TMUX"):
        os.execvp("copilot", ["copilot", "--yolo", *args])
    os.execvp("copilot-tmux", ["copilot-tmux", "start", "--yolo", "--", *args])


def connect(name, args, config):
    if config["name"] == name:
        if args:
            os.execvp(args[0], args)
        os.chdir(Path.home())
        os.execvp("copilot-tmux", ["copilot-tmux"])
    via = config.get("via", {}).get(name)
    if via:
        if via not in set(BOXES.values()) or via in {name, config["name"]}:
            raise ValueError(f"Invalid SSH hop for {name}.")
        args = [name, *args]
        name = via
    address_file = Path(config["shared_dir"]) / f"{name}-key.txt"
    if not address_file.is_file():
        raise ValueError(f"Missing {address_file}; sync the devboxes folder first.")
    address = address_file.read_text().strip()
    if not address or any(char.isspace() for char in address):
        raise ValueError(f"{address_file} must contain one tailcat address.")
    tailcat = shutil.which("tailcat")
    if tailcat is None:
        raise ValueError("tailcat is not on PATH.")
    # Validate without echoing a secret address in diagnostics or shell text.
    result = subprocess.run([tailcat, "parse", address], capture_output=True)
    if result.returncode:
        raise ValueError(f"{address_file} is not a valid tailcat address.")
    command = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=20"]
    identity = config.get("ssh_identity")
    if identity:
        if not Path(identity).is_file():
            raise ValueError(f"SSH identity not found: {identity}")
        command += ["-o", "IdentitiesOnly=yes", "-i", identity]
    if sys.stdin.isatty():
        command.append("-t")
    proxy = shlex.join([tailcat, address, "22"]).replace("%", "%%")
    command += ["-o", "ProxyCommand=" + proxy, f"{config['user']}@{name}-tailcat"]
    remote = shlex.join(args) if args else 'copilot-tmux'
    command.append('export PATH="$HOME/.local/bin:$PATH"; exec ' + remote)
    try:
        raise SystemExit(subprocess.run(command).returncode)
    finally:
        if sys.stdout.isatty():
            sys.stdout.write(MOUSE_RESET)
            sys.stdout.flush()


def doctor(config):
    missing = []
    print(f"Box: {config['name']}")
    print(f"Shared folder: {config['shared_dir']}")
    for destination, via in config.get("via", {}).items():
        print(f"Route: {destination} via {via}")
    for tool in ("python3", "tmux", "copilot", "copilot-tmux", "tailcat", "ssh"):
        path = shutil.which(tool)
        print(f"{tool}: {path or 'MISSING'}")
        if path is None:
            missing.append(tool)
    for name in ("lobster", "lobsterclaw"):
        try:
            print(f"{name}: {repository(name)}")
        except ValueError as error:
            print(error)
            missing.append(name)
    for name in ("lobster3", "lobster4", "rygregg-ubuntu"):
        present = (Path(config["shared_dir"]) / f"{name}-key.txt").is_file()
        print(f"{name} address: {'present' if present else 'MISSING'}")
        if not present:
            missing.append(name + " address")
    if missing:
        raise ValueError("Missing requirements: " + ", ".join(missing))


def main():
    if len(sys.argv) < 2:
        raise ValueError("Usage: devbox-helpers doctor | lobster | lobsterclaw | lobster3 | lobster4 | ubuntu | update")
    name, *args = sys.argv[1:]
    if name in {"lobster", "lobsterclaw"}:
        run_repo(name, args)
        return
    config = load_config()
    if name in BOXES:
        connect(BOXES[name], args, config)
    elif name == "doctor":
        doctor(config)
    elif name == "update":
        script = Path(config["shared_dir"]) / "tools/setup_devboxes.py"
        if not script.is_file():
            raise ValueError(f"Shared installer missing: {script}")
        os.execv(sys.executable, [sys.executable, str(script), "--shared-dir",
                                 config["shared_dir"], *args])
    else:
        raise ValueError(f"Unknown helper: {name}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f"devbox-helpers: {error}", file=sys.stderr)
        sys.exit(1)
