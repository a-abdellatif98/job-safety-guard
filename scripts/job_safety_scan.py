"""Heuristic static scan for background job safety problems (Sidekiq/Active Job, Celery, Laravel queues, BullMQ)."""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass

SKIP_DIRS = {
    "venv", "node_modules", "vendor",
    "__pycache__", "tmp", "log", "storage", "dist", "build", "coverage", "spec", "tests", "__tests__", "settings",
}
SKIP_FILE = re.compile(
    r"^\.|^credentials(?:\.[\w.]+)?$|^secrets?\.|^database\.|^queue\.php$|^redis\.\w+$|^settings(?:_\w+)?\.py$|_settings\.py$|\.(?:pem|key)$"
    r"|_spec\.rb$|\.(?:test|spec)\.[jt]sx?$|^test_.*\.py$|_test\.py$", re.I
)
EXT_FRAMEWORK = {
    ".rb": "ruby", ".py": "python", ".php": "php",
    ".js": "node", ".jsx": "node", ".ts": "node", ".tsx": "node", ".mjs": "node", ".cjs": "node",
}
MAX_BYTES = 1_000_000
SUPPRESS_MARKER = "job-safety: ok"
WINDOW = 8

TXN, OBJ, UNBOUNDED, BROAD = "JOB-TXN-ENQUEUE", "JOB-OBJECT-ARG", "JOB-RETRY-UNBOUNDED", "JOB-RETRY-BROAD"
NOBACKOFF, NOTIMEOUT, HTTP = "JOB-RETRY-NO-BACKOFF", "JOB-NO-TIMEOUT", "JOB-HTTP-NO-TIMEOUT"
NOBATCH, NONIDEM, REENQ = "JOB-NO-BATCH", "JOB-NON-IDEMPOTENT", "JOB-SELF-REENQUEUE"

SIDEKIQ_DEFAULT_RETRIES = 25
COMMENT = {"ruby": "#", "python": "#", "php": ("//", "#", "*", "/*"), "node": ("//", "*", "/*")}

ENQUEUE = {
    "ruby": re.compile(r"\.((?:perform_async|perform_in|perform_at|perform_later)(?=\()|deliver_later)\(?"),
    "python": re.compile(r"\.(delay|apply_async)\("),
    "php": re.compile(r"(?<![\w>$])(dispatch)\("),
    "node": re.compile(r"\b\w*[Qq]ueue\w*\.(add)\("),
}
TXN_OPENER = {
    "ruby": re.compile(r"\btransaction\b.*\bdo\b|\btransaction\s*\{|\bwith_lock\b.*\bdo\b|\bafter_(?:create|save|update|destroy)\b.*\bdo\b"),
    "python": re.compile(r"\bwith\s+.*\batomic\("),
    "php": re.compile(r"\btransaction\("),
    "node": re.compile(r"\$transaction\(|\.transaction\("),
}
COMMIT_HOOK = re.compile(r"after_commit|after_all_transactions_commit|on_commit|afterCommit|delay_on_commit")
AFTER_CALLBACK = re.compile(r"^\s*after_(?:create|save|update|destroy)\s+(:.*)")
JOB_LIKE = {
    "ruby": re.compile(r"Sidekiq::(?:Job|Worker)|ApplicationJob|ActiveJob::Base|<\s*\w*Job\b"),
    "python": re.compile(r"^\s*@(?:shared_task|\w+\.task)\b|from celery|import celery", re.M),
    "php": re.compile(r"\bShouldQueue\b"),
    "node": re.compile(r"bullmq|new Worker\(", re.I),
}
TASK_DECORATOR = re.compile(r"\s*@(?:shared_task|\w+\.task)\b")
RECORD_NAME = re.compile(r"^(?:self|this|instance|obj|record|model|\$this)$")
RECORD_EXPR = re.compile(
    r"(?:\.|->)(?:attributes|as_json|to_json|to_h|__dict__|toArray|toJSON|toObject)(?:\(\))?$|^(?:model_to_dict|serializers\.serialize)\("
)
IVAR = re.compile(r"^@\w+$")
IVAR_ID = re.compile(r"_ids?$")
SIDEKIQ_PUSH = {"perform_async", "perform_in", "perform_at"}
STRIPE_CREATE = {
    "ruby": re.compile(r"Stripe::(?:Charge|PaymentIntent|Transfer|Refund|Payout)\.create\("),
    "python": re.compile(r"stripe\.(?:Charge|PaymentIntent|Transfer|Refund|Payout)\.create\("),
    "php": re.compile(r"->(?:charges|paymentIntents|transfers|refunds|payouts)->create\("),
    "node": re.compile(r"\.(?:charges|paymentIntents|transfers|refunds|payouts)\.create\("),
}
IDEMPOTENCY_ARG = re.compile(r"idempotency_key|idempotencyKey")
COUNTER = {
    "ruby": re.compile(r"\.(?:increment|decrement)!\(|\b(?:increment_counter|update_counters)\("),
    "python": re.compile(r"\bF\([^)]*\)\s*[+\-]"),
    "php": re.compile(r"->(?:increment|decrement)\("),
    "node": re.compile(r"\b(?:increment|decrement)\s*:"),
}
HTTP_CALL = {
    "python": (re.compile(r"\brequests\.(?:get|post|put|patch|delete|head|request)\("), re.compile(r"timeout")),
    "node": (re.compile(r"\baxios\.(?:get|post|put|patch|delete|request)\(|(?<![\w.])fetch\("), re.compile(r"timeout|signal")),
    "php": (re.compile(r"\bnew\s+(?:\\?GuzzleHttp\\)?Client\("), re.compile(r"timeout")),
}
BATCH = {
    "ruby": (
        re.compile(r"\b[A-Z]\w*(?:::\w+)*\.(?:all|where|joins|includes|order)\b.*\.(?:each|map|flat_map|each_with_index|to_a)\b"),
        re.compile(r"find_each|in_batches|find_in_batches|each_slice|\.limit\(|\.first\(|\.take\(|\.lazy"),
    ),
    "python": (
        re.compile(r"\bfor\s+\w+\s+in\s+[A-Z]\w*\.objects\.(?:all|filter|exclude)\("),
        re.compile(r"\.iterator\(|\)\s*\[|Paginator"),
    ),
    "php": (
        re.compile(r"\b[A-Z]\w*::(?:all\(\)|.*->get\(\)).*(?:->each|->map|->filter)\b|\bforeach\s*\(.*(?:::all\(\)|->get\(\))"),
        re.compile(r"chunk|cursor|lazy"),
    ),
}
VALUE_TYPES = {"Carbon", "CarbonImmutable", "DateTime", "DateTimeImmutable", "DateTimeInterface", "Closure", "UuidInterface"}
PHP_CTOR_PARAM = re.compile(r"(?:^|[(,])\s*(?:(?:private|protected|public)\s+)?(?:readonly\s+)?\??([A-Z]\w*(?:\\\w+)*)\s+\$\w+")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    framework: str
    message: str


def iter_files(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(
                d for d in dirs
                if d not in SKIP_DIRS and not d.startswith(".") and not os.path.exists(os.path.join(root, d, "pyvenv.cfg"))
            )
            for name in sorted(files):
                yield os.path.join(root, name)


def load_sources(paths):
    sources = []
    for path in iter_files(paths):
        framework = EXT_FRAMEWORK.get(os.path.splitext(path)[1])
        if not framework or SKIP_FILE.search(os.path.basename(path)) or os.path.islink(path):
            continue
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8") as handle:
                sources.append((path, framework, handle.read()))
        except (OSError, UnicodeDecodeError):
            continue
    return sources


def indent_of(line):
    return len(line) - len(line.lstrip())


def is_comment(framework, line):
    return line.lstrip().startswith(COMMENT[framework])


def ancestors(lines, idx):
    indent = indent_of(lines[idx])
    for j in range(idx - 1, -1, -1):
        if lines[j].strip() and indent_of(lines[j]) < indent:
            indent = indent_of(lines[j])
            yield j


def region(lines, idx):
    line = lines[idx]
    if "{" in line and line.count("{") == line.count("}"):
        return line
    parents = list(ancestors(lines, idx))
    if not parents:
        return line
    start = parents[0]
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or indent_of(lines[end]) > indent_of(lines[start])):
        end += 1
    return "\n".join(lines[start:end])


def call_args(lines, idx, match_end):
    text = "\n".join(lines[idx:idx + WINDOW])
    depth = 1
    for pos in range(match_end, len(text)):
        depth += {"(": 1, ")": -1}.get(text[pos], 0)
        if depth == 0:
            return text[match_end:pos]
    return text[match_end:]


def split_args(text):
    args, depth, current = [], 0, ""
    for ch in text:
        depth += (ch in "([{") - (ch in ")]}")
        if ch == "," and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(current.strip())
    return args


def flatten_args(args):
    flat = []
    for arg in args:
        listed = re.match(r"^args\s*=\s*[\[(]", arg)
        if listed:
            flat.extend(flatten_args(split_args(arg[listed.end():].rstrip("])"))))
        else:
            flat.append(re.sub(r"^\w+\s*=\s*(?!=)", "", arg))
    return flat


def is_record_like(arg, framework, sidekiq_push):
    if RECORD_EXPR.search(arg):
        return True
    if framework == "ruby":
        return sidekiq_push and bool(RECORD_NAME.match(arg) or IVAR.match(arg) and not IVAR_ID.search(arg))
    return bool(RECORD_NAME.match(arg))


def txn_context(framework, lines, idx, callbacks, prefix):
    if TXN_OPENER[framework].search(prefix):
        return True
    for j in ancestors(lines, idx):
        text = lines[j]
        if COMMIT_HOOK.search(text):
            return False
        if TXN_OPENER[framework].search(text):
            return True
        if framework == "ruby":
            m = re.match(r"\s*def\s+(\w+)", text)
            if m and m.group(1) in callbacks:
                return True
        if framework == "python" and re.match(r"\s*(?:async\s+)?def\b", text):
            k = j - 1
            while k >= 0 and lines[k].lstrip().startswith("@"):
                if "atomic" in lines[k]:
                    return True
                k -= 1
    return False


def in_handler(framework, lines, idx):
    keyword = re.compile(r"^\s*rescue\b" if framework == "ruby" else r"^\s*except\b")
    return any(keyword.match(lines[j]) for j in ancestors(lines, idx))


def enqueue_findings(framework, lines, idx, callbacks, job_like):
    out = []
    for m in ENQUEUE[framework].finditer(lines[idx]):
        method = m.group(1)
        if not COMMIT_HOOK.search(lines[idx]) and txn_context(framework, lines, idx, callbacks, lines[idx][:m.start()]):
            out.append((TXN, "enqueued inside a database transaction: the worker can run before the commit (or after a rollback). Enqueue after commit (Active Job enqueue_after_transaction_commit on recent Rails, transaction.on_commit or delay_on_commit in Django, afterCommit in Laravel) or use an outbox."))
        if job_like and framework in ("ruby", "python") and in_handler(framework, lines, idx):
            out.append((REENQ, "job re-enqueues from an exception handler: this bypasses the retry cap and backoff and can storm. Prefer the framework retry with a limit and backoff."))
        if method == "deliver_later":
            continue
        args = flatten_args(split_args(call_args(lines, idx, m.end())))
        if framework == "node":
            args = args[1:]
        if framework == "ruby" and method == "perform_later":
            args = [a for a in args if a != "self"]
        if any(is_record_like(a, framework, method in SIDEKIQ_PUSH) for a in args):
            out.append((OBJ, "passes a record, attributes hash or live object as a job argument: pass the id and reload inside the job so retries see current data and the payload stays small."))
    return out


def task_has_backoff(lines, idx):
    for j in range(idx, -1, -1):
        if TASK_DECORATOR.match(lines[j]):
            if "(" not in lines[j]:
                return False
            return bool(re.search(r"retry_backoff\s*=\s*(?!False)", call_args(lines, j, lines[j].index("(") + 1)))
    return False


def retry_findings(framework, lines, idx):
    line, out = lines[idx], []
    if framework == "ruby":
        if re.search(r"retry_on\b.*attempts:\s*:unlimited", line):
            out.append((UNBOUNDED, "retry_on with attempts: :unlimited retries forever; cap attempts and handle the exhausted case."))
        if re.search(r"retry_on\(?\s*(?:StandardError|Exception)\b", line):
            out.append((BROAD, "retry_on a broad exception class retries programming errors and bad input; list the transient errors only."))
        m = re.search(r"sidekiq_options\b.*\bretry:\s*(\d+)", line)
        if m and int(m.group(1)) > SIDEKIQ_DEFAULT_RETRIES:
            out.append((UNBOUNDED, f"retry: {m.group(1)} is a very long retry window and exceeds the Sidekiq default of {SIDEKIQ_DEFAULT_RETRIES} retries; confirm a longer retry window is intended."))
    elif framework == "python":
        if TASK_DECORATOR.match(line):
            decorator = call_args(lines, idx, line.index("(") + 1) if "(" in line else ""
            if re.search(r"max_retries\s*=\s*None", decorator):
                out.append((UNBOUNDED, "max_retries=None retries forever; set a finite max_retries."))
            if re.search(r"autoretry_for\s*=\s*\(?\s*(?:Exception|BaseException)\b", decorator):
                out.append((BROAD, "autoretry_for a broad exception class retries programming errors; list the transient errors only."))
            if "autoretry_for" in decorator and "retry_backoff" not in decorator:
                out.append((NOBACKOFF, "autoretry_for without retry_backoff does not delay retries; set retry_backoff (retry_jitter is on by default) to avoid a retry storm."))
        m = re.search(r"\.retry\(", line)
        if m and not re.search(r"countdown|eta", call_args(lines, idx, m.end())) and not task_has_backoff(lines, idx):
            out.append((NOBACKOFF, "self.retry without countdown retries after the task default delay; set countdown with backoff."))
    elif framework == "php":
        if re.search(r"\$tries\s*=\s*0\b", line):
            out.append((UNBOUNDED, "$tries = 0 means unlimited attempts; set a finite $tries or use retryUntil."))
    else:
        m = re.search(r"\battempts:\s*(\d+)", line)
        if m and int(m.group(1)) > 1 and "backoff" not in region(lines, idx):
            out.append((NOBACKOFF, "BullMQ attempts without backoff retries immediately; add a backoff option such as exponential."))
    return out


def php_class_findings(lines, text):
    out = []
    cls = next((i for i, l in enumerate(lines) if re.search(r"\bclass\s+\w+", l)), None)
    if cls is None or "ShouldQueue" not in text:
        return out
    if not re.search(r"\$timeout\b|function\s+timeout\b|#\[Timeout", text):
        out.append((cls, NOTIMEOUT, "queued job has no $timeout (or timeout()); a hung call holds a worker until any worker-level limit."))
    tries = next((i for i, l in enumerate(lines) if re.search(r"\$tries\s*=\s*([2-9]|\d{2,})", l)), None)
    if tries is not None and not re.search(r"\$backoff\b|function\s+backoff\b", text):
        out.append((tries, NOBACKOFF, "$tries above 1 without $backoff retries immediately; set $backoff or a backoff() method."))
    if "SerializesModels" not in text:
        for i, l in enumerate(lines):
            if "__construct(" in l:
                params = "\n".join(lines[i:i + WINDOW]).split(")")[0]
                types = [t for t in PHP_CTOR_PARAM.findall(params) if t.split("\\")[-1] not in VALUE_TYPES]
                if types:
                    out.append((i, OBJ, f"constructor takes {types[0]} but the job does not use SerializesModels; if it is an Eloquent model, the whole object is serialized into the payload."))
                break
    return out


def celery_timeout_findings(lines):
    out = []
    for i, line in enumerate(lines):
        if TASK_DECORATOR.match(line):
            decorator = call_args(lines, i, line.index("(") + 1) if "(" in line else ""
            if "time_limit" not in decorator:
                out.append((i, NOTIMEOUT, "Celery task has no time_limit or soft_time_limit; a global task_time_limit in your Celery config (not scanned) would cover it."))
    return out


def job_body_findings(framework, lines, idx):
    line, out = lines[idx], []
    if framework in HTTP_CALL:
        pattern, ok = HTTP_CALL[framework]
        for m in pattern.finditer(line):
            if not ok.search(call_args(lines, idx, m.end())):
                out.append((HTTP, "HTTP call in a job without a timeout (for fetch, a signal); a stuck socket pins the worker. Set connect and read timeouts."))
    if framework in BATCH:
        pattern, ok = BATCH[framework]
        if pattern.search(line) and not ok.search(line):
            out.append((NOBATCH, "loads a whole table to iterate in a job: process in batches (find_each, iterator, chunkById, cursor) and keep the job resumable."))
    if framework == "node":
        for m in re.finditer(r"\bfindMany\(|\bfindAll\(", line):
            if not re.search(r"take|limit", call_args(lines, idx, m.end())):
                out.append((NOBATCH, "unbounded findMany/findAll in a job: page with take or limit, or use a cursor."))
    for m in STRIPE_CREATE[framework].finditer(line):
        if not IDEMPOTENCY_ARG.search(call_args(lines, idx, m.end())):
            out.append((NONIDEM, "payment API create without an idempotency key: a retry can charge twice."))
    if COUNTER[framework].search(line):
        out.append((NONIDEM, "relative counter update (increment or F() + 1) in a job: a retry applies it again; guard with a processed marker or write an absolute value."))
    return out


def callbacks_in(lines):
    names = set()
    for line in lines:
        m = AFTER_CALLBACK.match(line)
        if m:
            names.update(re.findall(r":(\w+)", re.split(r"\b(?:if|unless|on):", m.group(1))[0]))
    return names


def scan_source(path, framework, text):
    lines = text.splitlines()
    job_like = bool(JOB_LIKE[framework].search(text))
    callbacks = callbacks_in(lines) if framework == "ruby" else set()
    raw = []
    for i, line in enumerate(lines):
        if is_comment(framework, line):
            continue
        found = enqueue_findings(framework, lines, i, callbacks, job_like) + retry_findings(framework, lines, i)
        if job_like:
            found += job_body_findings(framework, lines, i)
        raw.extend((i, rule, msg) for rule, msg in found)
    if framework == "php":
        raw.extend(php_class_findings(lines, text))
    elif framework == "python":
        raw.extend(celery_timeout_findings(lines))
    return {Finding(path, i + 1, rule, framework, msg) for i, rule, msg in raw if SUPPRESS_MARKER not in lines[i]}


def scan(paths):
    findings = set()
    for path, framework, text in load_sources(paths):
        findings.update(scan_source(path, framework, text))
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule, f.message))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Heuristically flag background job safety problems in Sidekiq/Active Job, Celery, Laravel and BullMQ code."
    )
    parser.add_argument("paths", nargs="*", default=["."], help="files or directories to scan (default: .)")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
        parser.error(f"path not found: {', '.join(missing)}")
    findings = scan(args.paths)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.path}:{f.line}: {f.rule} [{f.framework}] {f.message}")
        print(f"{len(findings)} suspected job safety issue(s). Heuristic: confirm each one in the code.", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
