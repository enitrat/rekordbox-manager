# Rekordbox Manager

A CLI for hobby DJs: follow online playlists, acquire available audio once, mirror playlists into Rekordbox, and back up the library for recovery on another computer.

The first implementation is macOS-oriented and uses isolated spotDL/yt-dlp and pyrekordbox adapters. `rbx` is the command name.

## Quick start

```sh
uv tool install .
rbx setup --install
rbx adopt --dry-run
rbx follow https://open.spotify.com/playlist/…
rbx sync
rbx status
rbx backup --destination "$HOME/Library/Mobile Documents/com~apple~CloudDocs/rbx-backups"
```

Use `rbx --json` (or put `--json` after a command) for versioned agent output. `rbx doctor` checks all dependencies. Close Rekordbox before sync applies database changes; downloaded audio remains resumable if Rekordbox is open.

The library state defaults to `~/Music/RekordboxManager`; set `RBX_LIBRARY` or pass `--library`. The working library and backups are deliberately outside this source repository.

The project is an early implementation. Run the test suite with `uv run pytest -q`. The encrypted-database integration test is opt-in:

```sh
RBX_TEST_DB=/path/to/disposable/master.db \
RBX_TEST_PYTHON=/path/to/pyrekordbox-python \
uv run pytest tests/test_rekordbox_integration.py -q
```

- [Product requirements](PRD.md): workflows, requirements, acceptance scenarios, and open questions.
- [Domain glossary](CONTEXT.md): shared language.
- [Decisions](docs/adr/): accepted choices and their tradeoffs.
- [Design interview](docs/design-interview.md): the next unresolved decision to discuss.

Music, credentials, personal library databases, and backups belong outside this source repository.
