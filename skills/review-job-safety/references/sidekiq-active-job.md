# Sidekiq and Active Job (Rails)

## Arguments
- Sidekiq stores arguments as JSON, so use simple values (ids, strings, numbers). Symbols and objects do not round trip. Sidekiq has a strict args mode (`Sidekiq.strict_args!`) to catch this; check your version's docs for the default.
- Active Job serializes records through GlobalID and reloads them when the job runs. If the record was deleted meanwhile, the job fails with `ActiveJob::DeserializationError`, which you can handle with `discard_on ActiveJob::DeserializationError`; check the API docs of your Rails version for narrower classes.
- A hash from `record.attributes` or `as_json` is a stale copy. Pass the id.

## Retries
- Sidekiq retries failed jobs 25 times by default with growing delays. Set `sidekiq_options retry: 5` (a number) or `retry: false` to change it. Use `sidekiq_retries_exhausted` for the final failure and `sidekiq_retry_in` for a custom delay.
- Active Job: `retry_on SomeError, wait: :polynomially_longer, attempts: 5`. The guide documents `wait:` and `attempts:` and a default of 3 seconds and 5 attempts. Whether `attempts: :unlimited` and `jitter:` exist depends on the Rails version, so check the API docs for yours before using them, and avoid `:unlimited` anyway.
- `retry_on StandardError` retries programming errors. List transient errors (timeouts, deadlocks, rate limits).
- With the Sidekiq adapter, check whether Sidekiq retries and Active Job `retry_on` stack for the same error, which multiplies attempts.
- Do not re-enqueue from a `rescue` block: it escapes the cap and backoff.

## Enqueue and transactions
- `after_create` and `after_save` run inside the transaction. Use `after_commit`, or enqueue after the `transaction` block.
- Active Job documents `enqueue_after_transaction_commit` (`self.enqueue_after_transaction_commit = true`, per job or globally) which defers enqueue until the surrounding transaction commits; the guide does not state the Rails version, so check your version's release notes.
- Sidekiq has an opt-in transaction aware client (`Sidekiq.transactional_push!`, introduced as beta in 6.5.0 and needing the `after_commit_everywhere` gem at that version; later versions changed how it hooks in). Read the changelog for your version.

## Uniqueness and locking
- Core Sidekiq has no unique jobs. Sidekiq Enterprise has unique job options; the `sidekiq-unique-jobs` gem adds `lock:` strategies. Check the docs of what you use, including lock expiry.
- Row lock inside the job: `record.with_lock { ... }` re-reads the row with a lock and runs the block in a transaction. Keep slow calls out of it.
- Idempotency without a lock: a unique index plus `create_or_find_by` or `upsert`, or a status column moved with a conditional `update_all`.

## Long jobs
- `find_each` and `in_batches` load bounded batches; `each` on `Model.where(...)` loads everything.
- Sidekiq 7.3 added `Sidekiq::IterableJob` (beta at introduction) for resumable iteration; read the changelog of your version before relying on it.
- Otherwise fan out: one parent enqueues one child per id range.

## Timeouts
- Sidekiq has no per job timeout. Set timeouts on the HTTP client, database statement and any external call, and keep the job short enough to finish inside the deploy shutdown window.
- Avoid wrapping work in `Timeout.timeout`: it can interrupt code anywhere, including inside a transaction or a connection pool checkout.
