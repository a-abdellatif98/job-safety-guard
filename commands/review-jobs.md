---
description: Review background jobs for production safety (idempotency, retries, transactions, batching, timeouts) and propose fixes
argument-hint: [job file, directory, job class name, or description]
allowed-tools: [Read, Glob, Grep]
---

# Review Background Jobs

Review jobs and their enqueue sites following the `review-job-safety` skill.

## Arguments

The user invoked this command with: $ARGUMENTS

Treat the argument as a path, a job or worker class name, or free text. If it is empty, ask which job to review, or default to the job directories (`app/jobs`, `app/workers`, `app/sidekiq`, `tasks.py`, `app/Jobs`, `src/queues`, `src/workers`).

## Instructions

1. **Detect the stack** with Glob and Read: Sidekiq or Active Job (`Gemfile`, `app/jobs`), Celery (`celery.py`, `tasks.py`), Laravel (`composer.json`, `app/Jobs`), BullMQ (`package.json` with `bullmq`).

   Never open `.env`, `.env.*`, `config/database.yml`, `config/credentials*`, `config/queue.php`, `config/database.php`, `settings.py`, Redis or broker connection files, or anything holding secrets or connection strings. Judge retry and timeout settings from the job code only, and say when a global setting could cover a gap.

2. **Read the job and every enqueue site** (Grep for `perform_async`, `perform_later`, `.delay(`, `apply_async`, `dispatch(`, `queue.add(`). Follow what is enqueued from callbacks, transactions, controllers and other jobs.

3. **Check each point** using `skills/review-job-safety/references/` (`sidekiq-active-job.md`, `celery.md`, `laravel.md`, `bullmq.md`, `checklist.md`): idempotency, ids vs objects, retry limit and backoff, enqueue vs transaction, uniqueness and locking, batching, timeouts, shutdown behaviour.

4. **Propose the fix** in the project's framework, with the tradeoff (for example a uniqueness lock that can drop a needed run).

## Output format

### Job safety review: `target`

**Detected:** stack, queue library, how jobs are enqueued.

**Findings:** numbered, ordered by risk, each with `file:line`, what goes wrong in production, and the fix as code.

**Checked and fine:** points you verified are already handled (say where).

**Unknown:** settings you could not see (worker timeouts, global retry config) and the question to ask.

**Scanner:** mention that `python3 scripts/job_safety_scan.py <project root>` (run from the plugin directory by the user) gives a heuristic first pass, exit code 1 when it finds something. Confirm each hit in the code.

Do not run anything. Read code, produce findings and diffs only.
