# BullMQ (Node)

## Arguments
- Job data is JSON serialized in Redis. Pass ids and plain values, reload inside the processor. `Date` becomes a string, `Map`, `Set` and class instances are lost.

## Retries
- Per job: `attempts` (number of tries) and `backoff`: `{ type: 'exponential', delay: 1000 }` or `{ type: 'fixed', delay: 1000 }`, or a number. Without `attempts` a job is not retried; with `attempts` and no `backoff` it retries immediately.
- Put defaults in `defaultJobOptions` on the `Queue` so every job gets them.
- Throw `UnrecoverableError` to fail a job without further retries (for bad input).
- `removeOnComplete` and `removeOnFail` control how many finished jobs stay in Redis. Unbounded history grows memory; a kept job id also blocks reusing that `jobId`.

## Enqueue and transactions
- A `queue.add` inside `prisma.$transaction(...)`, `knex.transaction(...)` or an ORM transaction callback can start before the commit, and Redis does not roll back with the database. Enqueue after the transaction resolves, or store an outbox row in the transaction and enqueue from it.

## Uniqueness and locking
- A custom `jobId` makes `add` ignore a second job with the same id while the first still exists. Do not use numeric-only ids that look like auto generated ones; check the docs.
- The `deduplication` job option (`{ id, ttl, extend, replace, ... }`) is documented for newer versions; check that your installed version has it.
- `limiter` on the worker and `concurrency` bound parallel work. Use a per resource lock for mutual exclusion of the same entity.

## Stalled jobs
- A worker that cannot renew its lock (a blocked event loop, a crash) has its job marked stalled and picked up again, so the job runs twice. Keep the event loop free (use sandboxed processors for CPU work), and tune `lockDuration` and `maxStalledCount` only after reading the docs for your version.

## Long jobs
- `findMany()` with no `take` loads everything. Page by cursor and use `job.updateProgress` or child jobs through a `FlowProducer` for fan out.

## Timeouts
- BullMQ shows no per job timeout option in the docs consulted; treat it as absent and verify on your version and wrap the work yourself with an `AbortController` and client timeouts (`axios` has `timeout`, `fetch` takes `signal: AbortSignal.timeout(ms)`). Check the "Timeout jobs" pattern page of the docs for the current recommendation.

## Shutdown
- Call `await worker.close()` on SIGTERM so the current job finishes and the lock is released.
