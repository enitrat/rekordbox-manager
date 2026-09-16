# Reconcile existing playlists before online ownership

This decision refines [ADR 0001](0001-online-playlists-own-mirrors.md).

Connecting an online playlist to a non-empty Rekordbox playlist begins with a read-only reconciliation. Existing collection entries are reused only through exact recorded evidence or explicit approval of a suggested match. Reconciliation keeps every unmatched Rekordbox membership and records which memberships are confirmed online relationships.

After reconciliation, the online playlist governs the membership and relative order of those confirmed online entries. Removing an entry online removes only that managed playlist membership; it does not delete audio, preparation, other memberships, or unmatched local entries. Managed entries appear in online order followed by preserved local entries in their existing relative order.

This retains the simple one-way sync model for known relationships while preventing an online snapshot from claiming or erasing content whose identity the manager has not established.
