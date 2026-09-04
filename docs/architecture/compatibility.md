# Compatibility policy

The `0.x` package line is experimental and provides no general API compatibility guarantee. Names, signatures, catalog wrappers, and command output may change between distribution versions.

Two narrower integrity rules apply after publication:

1. A contract payload identified by a published contract ID and version is immutable.
2. A breaking contract-payload change must use a new contract version.

If an uploaded wheel or source distribution is defective, it is not replaced in place. A correction uses a new distribution version and retains the immutable published artifacts.
