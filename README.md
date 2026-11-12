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

1. Attach to an existing session on the target tmux server, without creating
   duplicate Copilot processes. Inside that server, switch instead of nesting.
2. If no tmux sessions exist, restore the latest saved workspace and attach.
3. If no workspace is saved, start a fresh Copilot session inside tmux.

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
