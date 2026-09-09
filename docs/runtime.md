# Experimental provider-neutral runtime

The unreleased `0.0.2a1` adds the explicitly inventoried neutral runtime to the
migration substrate. Python 3.13 is the support floor; 3.13 and 3.14 are the
initial qualification matrix (`>=3.13,<3.15`). Install an exact reviewed runtime
wheel. An older Python resolver may select the immutable catalog-only `0.0.1a1`;
that is not a runtime installation. No release is authorized by this PR.

The runtime includes authorization, historical canonical writes and receipts,
neutral capture/range/fold ports, semantic task resolution, PostgreSQL queue and
worker fencing, bounded synthetic execution and accounting. The executor still
accepts only `TestModel` and `FunctionModel`. It preserves conductor support,
authoring limits, task hashes, transaction boundaries and cancellation settlement.
It does not assemble or start a product service.

`contracts/runtime-inventory.json` enumerates every added runtime source and its
hash. Copy entries must equal their approved source content hash. The one task
loader seam takes a caller-supplied `BuiltInModuleRegistry`; canonical task,
agent, contract, effort and evidence-policy values remain unchanged. The internal
static module mechanics retain composition, dependency, placement, integrity and
reserved-identity checks. They are not a supported plugin API or a public product
module catalog. The capture initializer imports no acquisition or adoption code.

The source inventory and sanitized provenance are default-deny. Wheel and sdist
inspection checks exact member sets and runtime hashes as well as all fourteen
unchanged migration resources. CI rebuilds from the public sdist independently,
installs both wheels in clean environments, records transitive dependency artifact
hashes for each supported Python, and runs fictional acceptance with checkout
filesystem access denied. Catalog checks retain all 49 payloads and twelve APIs.

N2 fixtures are authored in the public repository: a fictional orchard, generated
identifiers, in-memory byte ports and synthetic models. No acquisition is needed.
The cancellation contract deliberately retains outcome-settlement authorization
after cancellation, while preventing hydration and canonical apply; this lets
late usage remain accountable. Existing within-bead semantics remain historical
behavior, not the future immutable correction contract.

N3 namespace migration and released-core consumption, immutable correction,
retained-evidence authoring handoff, real providers, provider discovery, host
filesystem helpers, harness adoption, product composition, recall, CP execution,
and publication remain separate work. Public `memoriesql` alone is the future
core namespace. This PR must remain unmerged pending owner review.

Base prerequisite: public PR #3 merged the reviewed head
`ad692e66ac064cec2e37cd1b1a3b4e93d548a663` as
`ab99b42b1836deed1a1d8137e55b3b85205fa007`. The latter was exact public
`origin/main` when this isolated N2 branch was created.

## Inherited cancellation limitation

A cancellation-resistant `FunctionModel` callback can outlive the worker's first
cancellation grace interval. The existing worker cancels a second time and then
waits without a second timeout while retaining its cleanup lease. Accordingly,
N2 does not claim hard wall-clock termination for such callbacks. This behavior
is inherited unchanged from the approved worker source (SHA-256
`a6b990a6a74461f34e7e757a3e6f7426020f8cef8df4cdbaad2a95f2fcd10398`).
Broad review identified it at PR #4. A separate repair must define safe
abandonment, fencing and late-accounting settlement before changing this path;
it is explicitly deferred to preserve this extraction's cancellation semantics.
