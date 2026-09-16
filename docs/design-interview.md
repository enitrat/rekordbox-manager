# Design interview

The accepted decisions are recorded in the PRD and ADRs. Continue the grill-with-docs workflow one decision at a time: explain a concrete scenario, recommend an answer, wait for the user's choice, and update the relevant documents. Implementation choices remain open until resolved; writing these agreed requirements does not authorize building the software.

## Next question: selecting an alternate audio source

Scenario: a newly followed Spotify entry is absent locally, and acquiring audio requires selecting a recording on another service. Retaining the selected ID makes future acquisition repeatable but does not prove the first selection was correct.

Should the CLI acquire a confidently matched alternate source automatically, or require approval for every first-time alternate-source selection?

Recommendation: automatically acquire candidates that meet an explicit confidence policy for recording/version and duration, and queue uncertain candidates for review. This preserves the low-friction sync goal while making uncertainty visible. The confidence policy itself needs validation; downloaded audio must not be deduplicated with an existing library track merely on artist/title similarity.

Status: unanswered. No confidence threshold, provider, or automatic fallback chain has been selected.

## Later branches

- Prove import and preparation preservation, including behavior while Rekordbox is open.
- Define acceptable audio and replacement behavior.
- Prove a complete snapshot restore, then choose storage and optional automation.
- Define unfollow behavior and the lifecycle of preserved local-only memberships after the initial reconciliation model in [reconciliation-ux.md](reconciliation-ux.md).

The historical handoff at `/tmp/rekordbox-management-handoff.md` informed this discussion but is not a normative specification or a durable dependency of these docs.
