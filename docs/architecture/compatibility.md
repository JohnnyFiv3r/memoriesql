# Compatibility policy

The `0.x` package line is experimental and provides no general API compatibility guarantee. Names, signatures, catalog wrappers, and command output may change between distribution versions.

Distribution releases use plain numeric `X.Y.Z` versions with matching `vX.Y.Z`
tags. The next release is `0.0.2`, tagged `v0.0.2`. Alpha, beta, and release-candidate
suffixes require a separate owner decision. This naming convention does not imply
production readiness or expand the experimental compatibility guarantees. The
published `0.0.1a1` version and its evidence remain unchanged.

Two narrower integrity rules apply after publication:

1. A contract payload identified by a published contract ID and version is immutable.
2. A breaking contract-payload change must use a new contract version.

If an uploaded wheel or source distribution is defective, it is not replaced in place. A correction uses a new distribution version and retains the immutable published artifacts.
