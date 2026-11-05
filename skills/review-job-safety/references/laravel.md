# Laravel queues

## Arguments
- Eloquent models in a job constructor are serialized as a model identifier and reloaded when the trait `SerializesModels` is used. Without it the whole object graph is serialized. If the model is deleted before the job runs, the job fails with `ModelNotFoundException`, unless the job sets `public $deleteWhenMissingModels = true`.
- Pass ids and plain values when the data is not an Eloquent model. Do not pass `$model->toArray()`: it is a stale copy.

## Retries
- `public $tries` (attempts), `public $maxExceptions`, `public $backoff` (seconds or an array for escalating delay) and the `backoff()` method, `retryUntil()` for a deadline. `$tries = 0` means unlimited.
- `ThrottlesExceptions` middleware backs off after repeated exceptions. Check your version's docs for its options.
- Handle exhaustion with the `failed(Throwable $e)` method and the failed jobs table.
- A job that exceeds `$timeout` is marked failed; `public $failOnTimeout = true` makes timeouts fail the job instead of retrying it (verify on your version).

## Enqueue and transactions
- A `dispatch` inside `DB::transaction(...)` can start before the commit. Options: `->afterCommit()` on the dispatch, `public $afterCommit = true` on the job, or the `after_commit` key of the queue connection config (a config file this plugin does not read). `ShouldQueueAfterCommit` also exists in recent versions; confirm on yours.

## Uniqueness and locking
- `ShouldBeUnique` with `uniqueId()` and `public $uniqueFor` (lock expiry in seconds) keeps a second copy off the queue while one is queued or running; `ShouldBeUniqueUntilProcessing` releases the lock when processing starts. Unique locks need a cache driver that supports atomic locks.
- `WithoutOverlapping` job middleware (`Illuminate\Queue\Middleware\WithoutOverlapping`) takes a key; `releaseAfter()`, `dontRelease()` and `expireAfter()` control what happens to the overlapping copy and how long the lock lives.
- `RateLimited` middleware for external API limits.

## Long jobs
- `Model::all()` and `->get()` load everything. Use `chunkById()`, `cursor()` or `lazyById()`. Use `Bus::batch([...])` to fan out.

## Timeouts
- `public $timeout` per job (or a `timeout()` method; a `#[Timeout]` attribute may exist on newer versions, check the docs for yours). The queue connection's `retry_after` must be longer than the job timeout, or a still running job is handed to another worker. The worker also has a `--timeout` option; the process control extension (`pcntl`) is needed for timeouts.
- `Http::timeout(seconds)` sets the request timeout on the HTTP client; a raw Guzzle `Client` with no `timeout` option waits indefinitely (the Guzzle default is 0).
