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

## Adopting an existing Rekordbox collection

`rbx adopt` is the backsync path for tracks you already imported and prepared in Rekordbox:

```sh
rbx adopt --dry-run --json   # inspect what will be adopted
rbx adopt                    # copy recognized local audio into managed storage
rbx status
rbx backup --destination /path/to/iCloud-or-Google-Drive-folder
```

Adoption hashes each local Rekordbox track, creates one managed copy, and records its existing Rekordbox content ID. It does not move or delete the original file, and it does not rewrite the Rekordbox database, so cues, beatgrids, ratings, and analysis remain attached to the same Rekordbox record. Identical files represented by multiple Rekordbox records are reported for review instead of merged automatically.

Adoption does not guess that a local track is a particular Spotify or SoundCloud entry. If you follow a playlist afterward, spotDL/yt-dlp may perform one acquisition; the downloaded result is then hash-deduplicated against the adopted file. Future syncs reuse the recorded source relationship.

The current adoption command imports collection tracks, not existing Rekordbox playlist memberships into the CLI's followed-playlist database. Those playlists are still preserved in the Rekordbox database and in complete snapshots. If you want a playlist to become online-owned and mirrored, use `rbx follow <playlist-url> --rekordbox-playlist-id <id>` after adoption; otherwise leave it as an independently curated Rekordbox playlist.

To find the Rekordbox playlist ID for that explicit mapping:

```sh
rbx rekordbox playlists --json
```

Close Rekordbox before `sync` applies changes or before `backup`; read-only adoption and inventory can run while it is open.

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
