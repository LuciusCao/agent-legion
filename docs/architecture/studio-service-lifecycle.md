# Studio service startup and shutdown

`ServiceLifecycle` owns process-local startup admission for `StudioChatService`.
Both public create and resume entry points acquire a startup permit before
reading or mutating session state. The permit covers claim, old-generation
teardown, token creation, registration, handle start, readiness, compensation,
and final response publication. Its `finally` releases the permit on success
and on failure.

Shutdown atomically seals admission, waits for every admitted operation to
finish, and only then snapshots the runtime registry. No startup producer can
register after that snapshot. Admitted operations may finish successfully
while shutdown is waiting; shutdown then cleans their resulting runtimes.
Requests arriving after the seal receive a conflict before creating a row or
token. This is a drain protocol, not cancellation of in-flight database work.

The condition is held only for permit accounting and sealing. Waiting releases
it, and neither the runtime lock nor the registry lock is held while draining.
Readiness callbacks therefore remain free to finish startup. A separate lock
serializes shutdown calls through cleanup, so a second shutdown cannot return
while the first is still draining or cleaning up. Repeated shutdown is safe.

Runtime identity checks remain necessary for normal close/resume and late
callbacks. They do not replace the service-wide producer barrier: identity
checks prevent an old callback from destroying a successor, while the barrier
ensures shutdown includes every successor produced by an admitted operation.

Shutdown waits for admitted operations, including the existing ACP startup
timeout; it does not invent a timeout that would return with producers still
active. Database/network stalls can consequently delay shutdown. Existing
best-effort database cleanup behavior is unchanged: a failed status write is
logged and repaired by startup reconciliation, and failed token revocation is
logged with token TTL as the fallback.

## Quality Impact

`tests/services/test_studio_chat_shutdown_races.py` pauses real create/resume
operations before token minting and after registration, then starts concurrent
shutdown calls. Success and injected startup failure cases assert rejection of
new admission, draining of existing work, empty registry, revoked tokens,
terminated handles, and absence of durable starting/running rows. Existing
admission, resume, generation-bound callbacks and fatal-loop tests retain their
separate per-runtime guarantees. The invariant is `STUDIO-RUNTIME-001`.
