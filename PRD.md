# Rekordbox Manager — Product Requirements

Status: product design; no implementation selected.

This document records the user's accepted direction from the design conversation. Command spellings and examples are illustrative. Open decisions are explicitly separated from requirements. The historical handoff is evidence of previous experiments, not an architectural specification.

## Product promise

Follow Spotify and SoundCloud playlists once. Acquire available tracks without repeatedly passing individual links, reuse audio across playlists, make the tracks available in Rekordbox, and explain what still needs attention. Recover the library and DJ preparation on another computer if the original computer is lost.

The primary user is a hobby DJ who curates online and prepares and plays music in Rekordbox. The product should reduce recurring maintenance rather than expose the internal download/import pipeline as chores. Both humans and agents must be able to use the same capabilities.

## Scope and ownership

| Concern | Owner |
| --- | --- |
| Followed playlist membership and order | Online playlist |
| Acquired audio and stable storage | CLI-managed library |
| Cues, beatgrids, ratings, and DJ preparation | Rekordbox |
| Independently created DJ playlists | User in Rekordbox |
| Library recovery and source relationships | Library backup workflow |

Folders are storage, not the user's primary organization interface. There is no requirement for folder-to-playlist mirroring or three-way reconciliation. Local files are another way to acquire tracks, not a second library model.

Initial workflows cover Spotify, SoundCloud, local files, and Rekordbox on the user's macOS environment. The provider model must permit future sources, including YouTube, without changing track identity or playlist membership rules. Additional operating systems and providers are not initial commitments.

Non-goals include concurrent editing across computers, writing local playlist edits back to online services, DRM circumvention, a required always-running helper, and storing the personal music library in the software's Git repository. A full-screen TUI is optional, not an initial requirement.

## Core workflows

### Set up and adopt an existing library

Setup checks and installs the underlying tools needed for enabled integrations, verifies that they execute successfully, and gives actionable remediation when they do not. Tool installation must not depend on undocumented shell workarounds. Account authentication is guided when needed; credentials are excluded from normal output and backups intended for source control.

The user's existing audio and Rekordbox preparation must be accommodated. Adoption reuses confident existing matches and preserves uncertain entries rather than rebuilding the library or discarding cues. Dependency selection, installation strategy, and the exact adoption procedure remain implementation questions.

### Follow and sync online playlists

The user registers each playlist once. A global or playlist-scoped sync discovers differences, acquires or reuses audio, and updates the corresponding Rekordbox playlist. Routine confident work proceeds without a confirmation for every track; an optional preview exposes planned changes.

Online additions add available tracks. Online removals remove only the mirrored membership, retaining audio, preparation, and other playlist memberships. Online reordering updates the mirror. Edits to membership or order inside a mirrored Rekordbox playlist are overwritten by the next successful reconciliation; this ownership must be visible when following a playlist.

Independently curated Rekordbox playlists are not reconciled against online sources. Track preparation remains intact when memberships change. Repeated syncs must not create duplicate files, collection entries, or playlist memberships.

Unresolved entries do not block other tracks; acquired entries retain their relative online order. Failed, inaccessible, or incomplete source enumeration must not be interpreted as an empty playlist and cause removals. Provider playlist IDs preserve associations through renames; detailed naming policy remains open.

If Rekordbox cannot be updated while open, audio acquisition can continue and import work remains pending. Closing Rekordbox and rerunning the command resumes that work without redownloading. Direct database writes and unattended analysis are not assumed capabilities until verified.

### Import external audio

The user can import a local file into managed storage and optionally an independently managed playlist. The original source folder has no ongoing synchronization role. Local directory intake may provide a convenient batch import; it does not imply folder mirroring.

A local file can also resolve a missing entry in a followed playlist. The resulting confirmed relationship is remembered on later syncs. Ambiguous candidates require a choice; importing unrelated audio must not change the membership of an online-owned playlist.

### Resolve failures

Failures are persistent, addressable records associated with affected source entries and playlists. Show a concise reason and useful next actions: retry, provide a local file, select an alternative source, or ignore. Remember ignored entries and keep them discoverable without repeatedly demanding attention.

Distinguish protected audio, preview-only audio, unavailable sources, authentication or network failures, conversion failures, unsuitable or incomplete files, uncertain matches, pending import, and pending analysis. These are product categories, not a finalized error schema. Temporary failures can retry within limits; permanent failures must not loop indefinitely.

Track-level issues do not discard successful work. Detailed diagnostics are available separately from the human summary and must not expose authentication material.

### Back up and restore

The baseline is an explicit backup command and a restore command. The user can select a destination such as a folder synchronized by iCloud Drive or Google Drive. No provider-specific cloud implementation is selected.

A recoverable library includes audio, the Rekordbox database and associated preparation/analysis, playlists, and the CLI's source identities and confirmed relationships. Exact files and snapshot consistency procedures require investigation. A copy of `master.db` alone is not considered a proven backup.

Show the last successful backup and distinguish local snapshot completion from verified remote persistence. Do not claim cloud upload completion without evidence. Restore must account for destination file paths and verify that tracks and preparation are usable before reporting success.

One computer is active at a time. Recovery or handoff must detect divergent state before overwriting it; automatic merging of separately edited Rekordbox libraries is out of scope.

Automatic backup is desirable only if a reliable approach fits the workflow without requiring an always-running helper. Manual-only backup is an acceptable first release. In particular, cue edits made after a CLI command are not protected merely because that command created a backup. Retention policy, incremental storage, and automation triggers remain open.

## Track identity and acquisition provenance

A confirmed library track has one managed audio file and one Rekordbox collection entry, referenced by all relevant playlists. Removing its last followed membership does not delete it. Library deletion is a separate explicit operation.

Persist provider-qualified IDs and URLs for both the requested source entry and the actual acquisition source, along with the downloader, relevant acquisition settings, and downloaded file hash. One library track may have multiple confirmed source associations. Preserve these relationships in backups.

For example, a Spotify listing may lead to acquisition from another service. Retaining only the Spotify ID would not identify the audio actually obtained. Reacquisition tries the recorded acquisition source first; loss of that source produces an issue rather than a silent substitution. The same online ID is not a guarantee of identical bytes forever; provenance does not replace audio backup.

Matching rules:

| Evidence | Default behavior |
| --- | --- |
| Known provider-qualified entry with a confirmed local relationship | Reuse library track |
| Known exact acquisition source with a confirmed local relationship | Reuse library track |
| Identical file hash | Reuse existing audio |
| Similar artist/title but different source identities | Keep separate; optionally suggest a match |
| Previously user-confirmed relationship | Reuse on subsequent syncs |

Provider IDs are namespaced, never compared as bare IDs across services. Original, radio, extended, remix, live, and preview recordings must not be merged based on title similarity. Occasional unconfirmed duplicates are preferable to a false merge that damages preparation. Automatic fingerprint-based cross-provider matching is not an accepted requirement.

Replacing existing audio must not silently transfer cues and beatgrids to a different audio timeline. Version and quality replacement policy needs further design.

## Human and agent interface

The default human interface offers concise progress, playlist-level readiness, and actionable summaries. Lightweight menus may help choose playlists or resolve issues. Every interactive action must also be available through explicit command arguments.

Illustrative surface:

```text
rbx setup
rbx follow <playlist-url>
rbx status
rbx sync --playlist "Weekend digging"
rbx import /path/to/track.wav --playlist "Friday set"
rbx issues list
rbx backup
rbx restore <snapshot>
```

Agent requirements:

- Versioned structured JSON with stable identifiers, status codes, and explicit pending work.
- Explicit noninteractive execution, with no hidden prompt or menu dependency.
- Choices that are genuinely required return actionable structured results; noninteractive mode does not imply blanket approval.
- Machine results on stdout; progress and diagnostics on stderr; no color or animations in machine results.
- Documented exit semantics distinguishing success, partial completion, and failure.
- Resumable, repeatable operations that do not duplicate previous successful work.
- Preview support for inspecting intended changes.
- Discoverable help and examples, with humans and agents using the same underlying operations.

Example, not final syntax:

```text
rbx sync --playlist "Weekend digging" --no-input --json
rbx issues resolve <issue-id> --file /path/to/track.wav --no-input --json
```

## Readiness and preservation

Report acquisition, Rekordbox import, and analysis/preparation state separately. Do not label a playlist ready merely because its downloads succeeded. Detectable previews, corruption, truncation, and mismatched versions must be surfaced; the software cannot guarantee musical correctness without listening. Encoding at a larger bitrate must not be described as improving source fidelity.

Interrupted runs retain enough state to resume. A failed Rekordbox update must not orphan moved files or lose existing preparation; the integration must provide recovery and validation appropriate to its mutation strategy. Destructive cleanup is never an incidental consequence of sync.

Illustrative human summary:

```text
Weekend digging — 42 source entries
  38 present in Rekordbox
   2 downloaded · import pending; close Rekordbox
   1 unavailable · supply a file or ignore
   1 ignored · preview only

Backup: last successful snapshot 3 days ago
```

Being present in Rekordbox does not, by itself, establish that analysis is complete.

## Acceptance scenarios

1. Follow two playlists containing the same identified track. Sync acquires it once and creates one Rekordbox collection entry referenced by both playlists. Running again adds no duplicates.
2. Remove that track from one online playlist. Only that mirrored membership disappears; the other membership, audio, and cues survive. Removing its final followed membership still retains the library track.
3. Reorder the online playlist. Acquired entries follow that order without recreating their collection records. A failed or partial playlist fetch causes no inferred removals.
4. Two services list similarly named recordings without a confirmed relationship. They are not silently merged. A confirmed match is remembered for future runs.
5. One entry fails acquisition. Other entries complete, the issue persists with useful actions, and a later local import can resolve it without changing the online playlist.
6. Rekordbox is open or an operation is interrupted. Completed audio acquisition is retained and a later sync finishes pending work without duplicate downloads or lost preparation.
7. An agent runs noninteractively. Output is parseable JSON, no prompt blocks execution, and unresolved choices and partial completion are explicit.
8. Restore a validated snapshot to a different local path on a replacement computer. Audio, playlists, source relationships, cues, and associated preparation remain accessible. A divergent destination is not silently overwritten.
9. Setup finds a broken dependency. It identifies the failing component and offers an actionable remedy rather than failing later with an opaque conversion error.

## Open decisions and feasibility work

Resolve these through focused discussion and experiments; historical scripts do not settle them.

- Acquisition policy: for metadata-only sources such as Spotify, which alternate audio sources may be selected automatically, and what evidence makes the match acceptable?
- Rekordbox integration: supported import/export versus direct database access; version compatibility, closing requirements, preservation, and automatic analysis capabilities.
- Audio policy: minimum acceptable quality, preview detection evidence, and safe file replacement.
- Setup: supported tool versions, isolated installation, authentication, and upgrade behavior.
- Existing-library adoption: confidence rules and whether existing audio is moved, copied, or registered in place.
- Backup: complete snapshot contents, consistency, restore compatibility, retention, storage cost, cloud completion visibility, and optional automation.
- Playlist lifecycle: unfollow behavior, naming conflicts, source renames, and duplicate occurrences of one source entry. Proposed unfollow default is to retain an ordinary editable Rekordbox playlist; this is not yet a separately confirmed decision.
- Final command names, JSON schema, exit codes, and any interactive dashboard layout.

The next design question is tracked in [the interview notes](docs/design-interview.md). Build feasibility should be demonstrated with a small complete sync and restore exercise before expanding provider or UI scope.
