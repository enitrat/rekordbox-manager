# Product

<!-- impeccable:product-schema 1 -->

## Platform

adaptive

The initial product is a macOS command-line tool used alongside Rekordbox. Its human-readable terminal interface and versioned machine-readable interface are two views of the same operations.

## Users

The primary user is a hobby DJ who discovers and curates music in Spotify and SoundCloud, prepares it in Rekordbox, and wants the library to remain recoverable without adopting Rekordbox Cloud. The tool must also be dependable when operated noninteractively by an agent.

## Product Purpose

Rekordbox Manager connects online curation, locally acquired audio, and Rekordbox preparation without making the user manage individual download links or duplicate files. Success means that a playlist can be followed once, incrementally synchronized, reviewed when identity is uncertain, and restored on another computer with its audio, playlists, cues, beatgrids, and source relationships intact.

## Positioning

The product treats a recording, its online listings, its acquired audio, its playlist memberships, and its Rekordbox preparation as related but distinct records. This permits one audio file and one Rekordbox collection entry to serve multiple playlists while preserving exact source provenance and refusing unsafe identity guesses.

## Operating Context

- Online playlists are the main place where music is curated.
- Rekordbox is the place where tracks are analyzed and prepared for DJ use.
- The managed library stores shared audio and acquisition provenance; Finder folders are not a parallel playlist system.
- Spotify or SoundCloud entries may require audio acquisition from a different identified source. Local files are also valid acquisition sources.
- Only one computer is active at a time. Backups may live in an iCloud Drive or Google Drive synchronized folder, but remote upload must not be claimed without evidence.
- Rekordbox may need to be closed while database changes or consistent backups are applied. Download work should remain resumable when it is open.

## Capabilities and Constraints

- Follow Spotify and SoundCloud playlists and support additional providers later without changing the library identity model.
- Acquire each confirmed recording once and reference it from every relevant Rekordbox playlist.
- Preserve provider-qualified identities for both the requested entry and the actual audio acquisition source.
- Adopt existing Rekordbox audio and preparation instead of rebuilding it.
- Connecting an online playlist to an existing Rekordbox playlist begins with reconciliation, not replacement.
- Exact recorded relationships are automatic matches. A unique normalized artist, title, and close-duration match may be preselected for bulk approval, but is never silently confirmed. Ambiguous versions, edits, remixes, and title-only similarities require individual resolution.
- Existing Rekordbox memberships that cannot be linked to online entries remain in the playlist until the user explicitly removes them.
- After reconciliation, online services own confirmed online memberships: removing an online entry removes only that Rekordbox playlist membership. Audio, preparation, collection identity, other playlist memberships, and unmatched local entries remain.
- Confirmed online entries follow online order. Preserved unmatched local entries follow them in their existing relative order.
- Provider failure, incomplete enumeration, or ambiguous identity must never be interpreted as permission to remove playlist memberships.
- No DRM circumvention, concurrent multi-computer merge, required background helper, or storage of personal music in the source-code repository.
- Every interactive action has explicit noninteractive arguments and versioned structured output. Required choices never become implicit approval in noninteractive mode.
- Backup and restore cover audio, source relationships, Rekordbox data, playlists, and preparation. A copy of `master.db` alone is not considered proven recovery.

## Evidence on Hand

- [PRD.md](PRD.md) records accepted workflows, safety requirements, and acceptance scenarios.
- [CONTEXT.md](CONTEXT.md) defines the domain language.
- [docs/adr](docs/adr) records architectural decisions and their tradeoffs.
- [docs/reconciliation-ux.md](docs/reconciliation-ux.md) specifies the approved first-link reconciliation experience.
- The September 2026 incident demonstrated that replacing playlist membership before establishing source-to-Rekordbox identity can hide prepared tracks and produce unusable numeric metadata. The original 24-entry prepared playlist was recovered, proving that membership must be changed independently from collection records and preparation.
- No validated claim currently exists that every Rekordbox version, provider response, cloud destination, or alternate-source match can be handled automatically.

## Product Principles

1. Establish identity before changing ownership.
2. Automate proven relationships; expose uncertain ones for review.
3. Playlist removal never implies deletion of audio or preparation.
4. Routine success should be quiet; partial success and next actions should be obvious.
5. Recovery claims require verification, not optimistic wording.

## Accessibility & Inclusion

Human workflows must remain usable without color, animation, or a full-screen terminal. Status and decisions use text labels and stable identifiers. Machine-readable operation must not depend on prompts.
