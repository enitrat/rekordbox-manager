# Preserve both requested and acquired source identities

A curation source can differ from the service supplying the audio, so the library retains provider-qualified identities for both the requested entry and actual acquisition, together with acquisition settings and file hash. Reacquisition tries the recorded source and reports its loss instead of silently resolving a replacement. This makes provenance and confirmed relationships portable, but does not remove the need to back up audio because online sources may change or disappear.
