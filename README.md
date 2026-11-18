# copilot-tmux

Persistent tmux workspace recovery for GitHub Copilot CLI on Linux and WSL 2.
Restore your Copilot conversations and named tmux windows after a reboot,
WSL shutdown, or tmux server loss.

## Requirements

- Linux or WSL 2 (uses `/proc` and POSIX file locks).
- Python 3.9 or later; no third-party Python packages.
- tmux 3.6 (tested).
- GitHub Copilot CLI on `PATH`, authenticated and ready to use.

## Install

```sh
git clone https://github.com/rgregg/copilot-tmux.git
cd copilot-tmux
python3 install.py
```

Ensure `~/.local/bin` is on your `PATH`. The installer copies the tool there,
adds a separate global Copilot hook file, and appends a source line to
`~/.tmux.conf`. Existing configuration and other hooks are preserved.
Rerun the installer after pulling updates.

New tmux servers load the integration automatically. For a server already
running, run `tmux source-file ~/.tmux.conf` once. Start a new Copilot process
to load the Copilot hooks. Do not repeatedly source the configuration in a
running server: tmux appends these hooks.

## Usage

```sh
copilot-tmux
```

With no arguments:

1. If no tmux sessions exist, restore the latest saved workspace.
2. Select an existing live Copilot pane in the caller's directory, or start a
   new one there without replacing other work.
3. Attach, or switch clients when already inside that tmux server.

Use `copilot-tmux start --yolo` to enable all permissions for a newly started
session. Explicit Copilot arguments go after `--`, for example
`copilot-tmux start --yolo -- --resume SESSION_UUID`. Explicit arguments open a
new window so that an existing session cannot silently swallow them.

Explicit recovery commands:

```sh
copilot-tmux list
copilot-tmux restore                       # Preview; changes nothing in tmux
copilot-tmux restore --apply               # Restore and print attach commands
copilot-tmux restore --source SOURCE_ID --apply
copilot-tmux restore --source SOURCE_ID --previous --apply
copilot-tmux restore --session SESSION_UUID --apply
copilot-tmux --socket /path/to/tmux.sock
```

`--source` accepts an unambiguous prefix from `list`; `--session` can be repeated.
Missing directories or Copilot history block restoration explicitly. Select
valid sessions with `--session` rather than silently dropping unavailable work.

## What survives

The registry stores session UUIDs, working directories, Copilot home paths,
tmux session/window names, and pane grouping. Copilot hooks refresh records when
sessions start or resume, prompts are submitted, and tools complete. tmux hooks
track window/session renames and selection changes.

Each tmux server incarnation gets its own workspace record. A fresh server
cannot overwrite the old server's record. Empty snapshots and snapshots during
systemd shutdown are ignored; one previous changed snapshot is retained per
workspace. Already-running registered sessions are skipped during recovery.

By default, records live in
`~/.local/state/copilot-tmux/workspaces.sqlite3`. `XDG_STATE_HOME`,
`COPILOT_TMUX_STATE_DIR`, or `--state-dir` can relocate the registry.
The directory is private and the database is mode `0600`. These records contain
local paths and names: keep them private and out of Git.

## Limitations

- Resumes saved conversations, not killed commands, unsaved editor buffers,
  in-flight agent actions, or other in-memory state. Check interrupted work
  before continuing.
- Cannot reconstruct window mappings from before installation.
- Restored windows retain their names and pane grouping, not exact pane sizes
  or window indexes. Multiple recovered panes use a tiled layout.
- Restoration requires the original working directories and local Copilot
  history to remain available.
- The default command considers sessions on the target socket, not every tmux
  server on the machine. Use `--socket` for custom servers.
- The tmux integration assumes session changes are made through normal tmux
  commands. Use `copilot-tmux snapshot` for an explicit refresh.
- This does not prevent Windows updates or other tools from stopping WSL.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Integration tests use isolated tmux sockets, temporary state, and a fake
Copilot executable. They do not send prompts to an AI model or use credentials.

## Shared dev-box helpers

The optional helpers standardize Linux, WSL, and PowerShell launchers. Keep a
private `devboxes` folder in OneDrive (or another shared/local folder) with
`lobster3-key.txt`, `lobster4-key.txt`, and `rygregg-ubuntu-key.txt`. Each contains
only that box's tailcat address. These addresses are secrets: never commit them.
Keep SSH private keys, Copilot history, and recovery databases local to each box.

Copy the source files into `devboxes/tools` without `.git`, tests, or any local
state, then install on each Linux/WSL box:

```sh
python3 /path/to/devboxes/tools/setup_devboxes.py \
  --shared-dir /path/to/devboxes --name lobster4 \
  --ssh-identity ~/.ssh/devboxes_ed25519
```

Use `lobster3` or `rygregg-ubuntu` for the other box names. `--ssh-identity` is
optional when the SSH agent/default key already works. Explicit identity files
must be owner-only (`chmod 600`), not permissively mounted Windows files.
Authorize each client's public key on the destination's existing tailcat SSH
service; tailcat loads authorized keys at startup, so reload that service after
changing its key file. The installer does not change server authentication.
Do not restart a tailcat service while tmux belongs to its systemd control
group: the default `KillMode=control-group` can kill the entire tmux workspace.
Use an already-authenticated hop instead when a restart is unsafe:

```sh
devbox-update --via tail-lobster4=tail-ubuntu
```

This routes `tail-lobster4` through Ubuntu's installed shortcut, using Ubuntu's own
local key; it does not forward or copy a private key. Ubuntu must be available.
Use `devbox-update --via tail-lobster4=direct` once direct authentication is ready.

From PowerShell 7 on Windows, run:

```powershell
& "$env:OneDriveCommercial\devboxes\tools\Install-Devboxes.ps1" -Name lobster4
```

This installs into the default WSL distribution and adds one dot-source block
to the current user's all-hosts profile, preserving everything else. When that
profile is OneDrive-synced, its functions load on both Windows boxes; each WSL
distribution still needs its own installation. Open a new shell afterward.

| Command | Behavior |
| --- | --- |
| `lobster`, `lobsterclaw` | Enter the local checkout; outside tmux select/start a directory-specific Copilot pane, inside tmux start Copilot directly. New sessions use `--yolo`; arguments are forwarded. |
| `copilot-tmux` | Select/start in the caller's directory, with persistent recovery. |
| `tail-lobster3`, `tail-lobster4`, `tail-ubuntu` | Connect through tailcat and open the destination's Copilot workspace. A shortcut to the current box stays local. |
| `tail-lobster3 hostname` | Run an explicit remote command; arguments are shell-quoted. |
| `devbox-helpers doctor` | Report paths, tools, checkouts, and address-file presence, without printing tokens. PowerShell: `devbox-doctor`. |
| `devbox-update` | Reinstall the current bundle from the configured shared folder, preserving the box name and SSH identity. |

Checkouts default to `~/github/bic/lobster` and either
`~/github/bic/lobsterclaw` or `~/github/bic/LobsterClaw`. Override with
`LOBSTER_REPO` and `LOBSTERCLAW_REPO`. Windows functions always route into WSL,
not the old `Q:` checkouts. Mouse and OSC 52 clipboard support are enabled for
tmux. Remote shortcuts reset terminal mouse modes after disconnecting.
The installer removes its old `lobster3`, `lobster4`, `ubuntu`, `lobster-3`,
`lobster-4`, and `rygregg-ubuntu` wrappers, but preserves customized files.
Box identities, address filenames, SSH host aliases, and saved routes are
unchanged; only the commands use the `tail-` prefix.

Existing managed files receive a one-time `.before-devboxes` backup. The
installer preserves other Copilot hooks, tmux configuration, shell profiles,
and workspace history. Existing tmux servers must load the new configuration
once, as described above; do not repeatedly append recovery hooks.

On Linux without a mounted OneDrive folder, deploy a private local copy of
`devboxes` instead. `devbox-update` reads that local copy; it does not implement
OneDrive synchronization. Refresh the copy when distributing a new bundle.

PowerShell launcher/installer tests require no Pester modules:

```powershell
pwsh -NoProfile -File tests/test_devboxes.ps1
```
