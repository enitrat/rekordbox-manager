# Playlist reconciliation UX

Status: initial workflow implemented; interactive review and explicit local cleanup remain future work.

## Job and audience

A hobby DJ has an existing prepared Rekordbox playlist and wants it to follow a Spotify or SoundCloud playlist without losing cues, analysis, locally added tracks, or recognizable metadata. The same workflow must be inspectable and executable by an agent without hidden prompts.

Reconciliation is a one-time identity and ownership transition. Sync is the repeatable operation after that transition. `follow` must not quietly collapse those two jobs.

## Outcome and proof

The user can see which online entries correspond to existing Rekordbox collection entries, approve strong suggestions efficiently, resolve ambiguous entries individually, and apply a plan that preserves every unclaimed local membership. A successful apply records stable relationships so future syncs no longer need fuzzy matching.

The command must prove safety before mutation by showing counts and identities for:

- exact matches already known to the manager;
- suggested matches requiring approval;
- ambiguous entries requiring individual choice;
- online entries that need acquisition;
- existing Rekordbox memberships with no online match;
- confirmed online memberships that would be removed on a later sync.

## Interaction model

### 1. Follow

```text
rbx follow <playlist-url> --rekordbox-playlist-id <id>
```

If the selected Rekordbox playlist is empty, following may establish the mapping directly. If it contains entries, the command records a pending relationship and returns a reconciliation summary. It does not change playlist membership.

The summary ends with one primary next action:

```text
Review: rbx reconcile "L-Zwo-Goatec"
```

### 2. Review

```text
rbx reconcile "L-Zwo-Goatec"
rbx reconcile "L-Zwo-Goatec" --json --no-input
```

Review is read-only and refreshes neither side implicitly when `--offline` is requested. Human output leads with the decision, not downloader internals:

```text
L-Zwo-Goatec · reconciliation required

  14 exact matches                 will reuse cues and analysis
   6 suggested matches             approval required
   1 ambiguous online entry        choose a recording
   8 online-only entries           will acquire when applied
   4 local-only Rekordbox entries  will be kept

No Rekordbox entries will be removed by reconciliation.
Next: rbx reconcile "L-Zwo-Goatec" --apply --accept-suggestions
```

The default command is the read-only review. A later release may add a lightweight numbered prompt. Every choice has an explicit command representation. Agents receive stable source IDs, Rekordbox content IDs, confidence evidence, proposed actions, and blocking reasons.

### 3. Match decisions

Match confidence has three product levels:

| Level | Evidence | Default |
| --- | --- | --- |
| Exact | previously confirmed source relationship, acquisition identity, Rekordbox content ID, or identical file hash | selected automatically |
| Suggested | one unique normalized artist/title candidate with compatible duration and no version conflict | preselected for bulk approval |
| Ambiguous | multiple candidates, missing metadata, title-only evidence, duration conflict, or version markers such as remix/edit/live/radio/extended | unselected; individual decision required |

The review screen must distinguish “preselected” from “confirmed.” Suggestions can be approved in bulk, rejected, or opened individually. Applying a suggestion records a durable confirmed relationship; future syncs use that relationship rather than repeating fuzzy comparison.

An online entry may instead be marked for acquisition, linked to a supplied local file, ignored, or left unresolved. Unresolved entries do not block confident work.

### 4. Apply

```text
rbx reconcile "L-Zwo-Goatec" --apply
rbx reconcile "L-Zwo-Goatec" --apply --accept-suggestions --no-input --json
rbx reconcile "L-Zwo-Goatec" --apply --match 'soundcloud:SOURCE_ID=CONTENT_ID'
```

Apply performs only the reviewed plan. If either playlist changed since the plan was calculated, it stops with `reconciliation-stale` and asks for a new review. It creates a recoverable pre-change snapshot before changing Rekordbox.

The resulting playlist is ordered as:

1. confirmed and acquired online entries in online order;
2. unmatched local Rekordbox entries in their prior relative order.

Reusing an existing Rekordbox content ID preserves its cues, beatgrid, analysis, artwork, and other preparation. Adding a membership never creates a second collection entry when a confirmed one exists.

After apply, the playlist becomes managed and ordinary `rbx sync` can update confirmed online memberships without another reconciliation.

## Ongoing sync semantics

For a reconciled playlist:

- an online addition reuses a confirmed library track or acquires one, then adds its membership;
- an online reorder changes the relative order of managed entries;
- an online removal removes that managed playlist membership only;
- unmatched local memberships remain after managed entries until explicitly removed;
- failure to obtain a complete provider snapshot causes no removals;
- a newly ambiguous identity becomes an issue and causes no speculative replacement;
- collection entries, files, preparation, and memberships in other playlists are unaffected by a mirrored membership removal.

The phrase “online-owned playlist” therefore means ownership of confirmed online memberships, not authority to delete unknown local content.

## Explicit local cleanup

Reconciliation and normal sync never delete unmatched local memberships. Explicit local-membership cleanup is planned as a separate previewable operation; it is not part of the initial reconciliation command.

Removing a local playlist membership still does not delete its collection entry, audio, or preparation. Bulk removal, if later added, requires an explicit reviewed plan and must not be hidden behind a generic `--force` flag.

## Material states

- **No existing playlist:** follow creates a new managed mirror.
- **Empty existing playlist:** follow can safely bind it and sync.
- **Existing prepared playlist:** pending reconciliation; zero mutation before apply.
- **All exact:** one concise review and apply.
- **Suggestions present:** bulk approval is available but never implicit.
- **Ambiguous or metadata-poor:** confident work can apply; unresolved entries remain visible.
- **Provider incomplete or unavailable:** reconciliation/sync is non-destructive and reports the source problem.
- **Rekordbox open:** acquisition may continue; membership apply remains pending.
- **Plan stale:** no partial mutation; regenerate the plan.
- **Interrupted apply:** recover or resume from recorded operation state and pre-change snapshot.

## Scope and anti-goals

This workflow covers connecting one online playlist to one existing or new Rekordbox playlist, recording identity decisions, and preserving local extras. It does not write Rekordbox edits back to providers, infer recording identity from names alone, merge independently edited computers, delete orphaned audio, or promise that a downloaded alternate source is musically correct without review.

Final flag spelling may change during implementation, but the separation between follow, review, apply, and ongoing sync is a product invariant.
