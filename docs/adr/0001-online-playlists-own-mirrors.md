# Online playlists own their Rekordbox mirrors

Online playlists govern the membership and order of their mirrored Rekordbox playlists; preparation and independently curated playlists remain owned in Rekordbox. This avoids three-way conflict resolution between online services, folders, and Rekordbox, at the cost of overwriting local membership and order edits to a mirror on reconciliation. Folders serve as managed storage, and local imports enter the shared library without becoming a second playlist authority.

Status: refined by [ADR 0006](0006-reconcile-before-online-ownership.md). Online ownership now applies only to memberships whose identity has been confirmed. Initial reconciliation preserves unmatched local entries.
