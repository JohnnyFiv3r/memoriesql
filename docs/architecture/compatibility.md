# Compatibility policy

The `0.x` package line is experimental and provides no general API compatibility guarantee. Names, signatures, catalog wrappers, and command output may change between distribution versions.

Distribution releases use plain numeric `X.Y.Z` versions with matching `vX.Y.Z`
tags. The current development candidate is `0.0.4`; its publication is not authorized. Alpha, beta, and release-candidate
suffixes require a separate owner decision. This naming convention does not imply
production readiness or expand the experimental compatibility guarantees. The
published `0.0.1a1`, `0.0.2` and `0.0.3` versions and their evidence remain unchanged.

Two narrower integrity rules apply after publication:

1. A contract payload identified by a published contract ID and version is immutable.
2. A breaking contract-payload change must use a new contract version.

If an uploaded wheel or source distribution is defective, it is not replaced in place. A correction uses a new distribution version and retains the immutable published artifacts.

Schema 15 is a forward compatibility transition: the version-1 command models and
successful receipts keep their exact payloads, and initial work can still finish.
An accepted bead cannot receive another semantic version. Such a legacy write
fails with `accepted_bead_immutable`; it is never silently translated into a new
bead. Callers must explicitly adopt version-2 correction commands. Old records
remain available under current authorization. See [the complete map](../immutable-observations.md).
