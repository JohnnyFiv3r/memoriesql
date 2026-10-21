# Automatic investigation checkpoints — source candidate

This descendant of helper source `4ab7adca07910e11326096f32c5f87253f667a81`
implements the existing `checkpoint` and `read_checkpoint` contract and automatic
checkpoint access through result reuse, inspection and hydration. It introduces
forward migration 0047. No new request/reply protocol, named save, restore,
branch action, saved-input SQL or mixed live/saved join is added.

Status: local source development only. PostgreSQL execution, installed artifacts,
distinct-identity host proof, privacy/resource qualification, independent review
and custody/outcome linkage are pending. Neither a mock receipt nor a successful
source check establishes availability. The frozen parent remains unchanged.

An append atomically binds canonical manifest bytes, compared branch head,
investigation-wide sequence, charged allocation, selected root pins, dependency
holds and the common idempotency/disclosure receipt. The newest 20 automatic
slots persist for at most 30 days from creation. Prune/expiry releases only that
checkpoint's holds under the workspace ledger/investigation locks. Charged
noncontent lineage/control stubs remain; no unbounded free history is introduced.
The layout's conservative metadata reservations still need measured physical
qualification. Encoded bytes and ledger charges are not physical disk use.

Root disclosure requires the original complete identity and current authority
over the whole checkpoint closure. A selected root's standalone expiry does not
end its valid explicit checkpoint route. Neither holding a dependency nor a
different surviving context authorizes a fallback. Result digests, original
frames and standalone expiry remain immutable. A checkpoint read exposes no
result rows, facts or evidence; subsequent explicit reuse/inspect/hydrate steps
have their own access receipts and charges. Unknown settlement retains its
reservation. No constant-time or measured-capacity claim is made.

The new unit controls use inert SQL acknowledgments. The separately admitted
runtime suite must execute actual public executor operations, durable journal/
artifact/receipt checks, expiry and pruning boundaries, shared holds, replay/CAS,
finding visibility, contextual helpers/cursors, revocation, storage refusal,
server cancellation and lost acknowledgement after real commit. The full suite
and both supported Python minors remain required. Governed erasure has no new
public operation in this slice; no erasure capability is claimed.
