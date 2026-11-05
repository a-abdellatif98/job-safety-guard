# job-safety-guard

A plugin that reviews background jobs for production safety: a job runs at least once, so it must survive running twice, late, in parallel with itself, or being killed halfway.

It pairs with [db-migration-guard](https://github.com/a-abdellatif98/db-migration-guard), [backfill-planner](https://github.com/a-abdellatif98/backfill-planner) and [n-plus-one-hunter](https://github.com/a-abdellatif98/n-plus-one-hunter).

## What it looks for

- **Idempotency**: relative updates, payments and writes that repeat on retry
- **Arguments**: full records or attribute hashes instead of ids
- **Retries**: no cap, broad exception lists, no backoff (retry storms), re-enqueue from an error handler
- **Transactions**: enqueue inside a transaction or an `after_create` callback, before the commit
- **Uniqueness and locking**: unique jobs, overlap middleware, row locks, deterministic job ids
- **Batching**: whole-table loads in a job, resumable work
- **Timeouts**: missing task or job limits, HTTP calls with no timeout

Stacks: Sidekiq and Active Job (Rails), Celery (Django), Laravel queues, BullMQ (Node).

## Installation

```
/plugin install job-safety-guard@claude-plugins-official
```

## Usage

### Slash command

```
/review-jobs app/jobs/sync_invoice_job.rb
/review-jobs the nightly export keeps running twice
```

It reads the job and every enqueue site (read-only tools only) and returns numbered findings with the failure in production and the fix as code. It never opens dotfiles, credential or queue connection files.

### Skill

The `review-job-safety` skill activates when you talk about Sidekiq, Active Job, Celery, Laravel queues or BullMQ, or about jobs that run twice, retry forever or hang. References:

| Reference | Covers |
|---|---|
| `sidekiq-active-job.md` | JSON arguments, GlobalID, `retry_on`, `sidekiq_options retry`, enqueue after commit, `with_lock`, `find_each` |
| `celery.md` | `autoretry_for`, `retry_backoff`, `acks_late`, `time_limit`, `transaction.on_commit`, `delay_on_commit` |
| `laravel.md` | `$tries`, `$backoff`, `$timeout`, `SerializesModels`, `afterCommit`, `ShouldBeUnique`, `WithoutOverlapping` |
| `bullmq.md` | `attempts`, `backoff`, `jobId`, `deduplication`, stalled jobs, `removeOnComplete` |
| `checklist.md` | one table to review a job against |

### Static scanner

```
python3 scripts/job_safety_scan.py path/to/project
python3 scripts/job_safety_scan.py --json app/
```

Exit code 1 when it finds something. Rule ids: `JOB-TXN-ENQUEUE`, `JOB-OBJECT-ARG`, `JOB-RETRY-UNBOUNDED`, `JOB-RETRY-BROAD`, `JOB-RETRY-NO-BACKOFF`, `JOB-NO-TIMEOUT`, `JOB-HTTP-NO-TIMEOUT`, `JOB-NO-BATCH`, `JOB-NON-IDEMPOTENT`, `JOB-SELF-REENQUEUE`. Add `job-safety: ok` to a line to suppress a hit.

It is a heuristic over single files and indentation. It skips dotfiles, credential, secret, database, queue config and `settings*.py` files, and cannot see global timeouts or retry settings or uniqueness designed elsewhere, so confirm each hit in the code. The scanner is not registered as a hook and never runs on its own.

## Tests

```
python3 -m pytest -q tests
python3 -m pyflakes scripts tests
```

## License

MIT
