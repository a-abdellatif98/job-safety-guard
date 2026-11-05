import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "job_safety_scan.py"
spec = importlib.util.spec_from_file_location("job_safety_scan", SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def run(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return [(f.rule, f.line) for f in scanner.scan([str(tmp_path)])]


def rules(tmp_path, name, text):
    return [r for r, _ in run(tmp_path, name, text)]


SIDEKIQ_HEAD = "class SyncJob\n  include Sidekiq::Job\n"


def test_rails_enqueue_inside_transaction(tmp_path):
    text = "class Order < ApplicationRecord\n  def pay\n    transaction do\n      update!(state: 1)\n      SyncJob.perform_async(id)\n    end\n  end\nend\n"
    assert run(tmp_path, "app/models/order.rb", text) == [("JOB-TXN-ENQUEUE", 5)]


def test_rails_enqueue_after_transaction_is_fine(tmp_path):
    text = "def pay\n  transaction do\n    update!(state: 1)\n  end\n  SyncJob.perform_async(id)\nend\n"
    assert rules(tmp_path, "app/models/order.rb", text) == []


def test_rails_after_commit_hook_inside_transaction_is_fine(tmp_path):
    text = "transaction do\n  update!(a: 1)\n  ActiveRecord.after_all_transactions_commit do\n    SyncJob.perform_async(id)\n  end\nend\n"
    assert rules(tmp_path, "app/models/order.rb", text) == []


def test_rails_after_create_callback_block_and_method(tmp_path):
    text = (
        "class Order < ApplicationRecord\n  after_create do\n    SyncJob.perform_later(self)\n  end\n"
        "  after_save :enqueue_sync\n  after_commit :other\n\n  def enqueue_sync\n    SyncJob.perform_async(id)\n  end\n"
        "  def other\n    SyncJob.perform_async(id)\n  end\nend\n"
    )
    assert run(tmp_path, "app/models/order.rb", text) == [("JOB-TXN-ENQUEUE", 3), ("JOB-TXN-ENQUEUE", 9)]


def test_django_enqueue_inside_atomic(tmp_path):
    text = "def pay(order):\n    with transaction.atomic():\n        order.save()\n        send.delay(order.id)\n"
    assert run(tmp_path, "shop/views.py", text) == [("JOB-TXN-ENQUEUE", 4)]


def test_django_atomic_decorator_and_on_commit(tmp_path):
    text = (
        "@transaction.atomic\ndef pay(order):\n    order.save()\n    send.delay(order.id)\n\n"
        "def ok(order):\n    with transaction.atomic():\n        transaction.on_commit(lambda: send.delay(order.id))\n"
        "        send.delay_on_commit(order.id)\n"
    )
    assert run(tmp_path, "shop/views.py", text) == [("JOB-TXN-ENQUEUE", 4)]


def test_laravel_dispatch_inside_transaction(tmp_path):
    text = (
        "<?php\nDB::transaction(function () use ($order) {\n    $order->save();\n    SyncJob::dispatch($order->id);\n"
        "    SyncJob::dispatch($order->id)->afterCommit();\n});\n"
    )
    assert run(tmp_path, "app/Services/Pay.php", text) == [("JOB-TXN-ENQUEUE", 4)]


def test_bullmq_add_inside_transaction(tmp_path):
    text = (
        "await prisma.$transaction(async (tx) => {\n  await tx.order.update({ where: { id }, data: {} });\n"
        "  await syncQueue.add('sync', { orderId: id });\n});\n"
    )
    assert run(tmp_path, "src/pay.ts", text) == [("JOB-TXN-ENQUEUE", 3)]


def test_sidekiq_full_record_arguments(tmp_path):
    text = "SyncJob.perform_async(self)\nSyncJob.perform_async(@order)\nSyncJob.perform_async(order.attributes)\nSyncJob.perform_async(@order_id, order.id)\nSyncJob.perform_in(5, order.as_json)\n"
    assert run(tmp_path, "app/models/o.rb", text) == [
        ("JOB-OBJECT-ARG", 1), ("JOB-OBJECT-ARG", 2), ("JOB-OBJECT-ARG", 3), ("JOB-OBJECT-ARG", 5)
    ]


def test_active_job_accepts_records_but_not_hashes(tmp_path):
    text = "SyncJob.perform_later(order)\nSyncJob.perform_later(order.attributes)\nSyncJob.perform_later(order.to_h)\n"
    assert run(tmp_path, "app/models/o.rb", text) == [("JOB-OBJECT-ARG", 2), ("JOB-OBJECT-ARG", 3)]


def test_celery_object_arguments(tmp_path):
    text = "send.delay(instance)\nsend.delay(order.id)\nsend.apply_async(args=[obj.__dict__])\nsend.delay(user=instance)\nsend.delay(model_to_dict(order))\n"
    assert run(tmp_path, "shop/tasks.py", text) == [
        ("JOB-OBJECT-ARG", 1), ("JOB-OBJECT-ARG", 3), ("JOB-OBJECT-ARG", 4), ("JOB-OBJECT-ARG", 5)
    ]


def test_laravel_dispatch_object_and_constructor_without_serializes_models(tmp_path):
    text = "<?php\nSyncJob::dispatch($order->toArray());\nSyncJob::dispatch($order->id);\n"
    assert run(tmp_path, "app/Http/C.php", text) == [("JOB-OBJECT-ARG", 2)]
    job = (
        "<?php\nclass SyncJob implements ShouldQueue\n{\n    public $timeout = 60;\n\n"
        "    public function __construct(private Order $order, private int $id) {}\n}\n"
    )
    assert rules(tmp_path / "a", "app/Jobs/SyncJob.php", job) == ["JOB-OBJECT-ARG"]
    safe = job.replace("{\n    public $timeout", "{\n    use SerializesModels;\n    public $timeout")
    assert rules(tmp_path / "b", "app/Jobs/SyncJob.php", safe) == []


def test_bullmq_record_payload(tmp_path):
    text = "await queue.add('sync', record);\nawait queue.add('sync', { id: record.id });\nawait queue.add('sync', user.toJSON());\n"
    assert run(tmp_path, "src/q.ts", text) == [("JOB-OBJECT-ARG", 1), ("JOB-OBJECT-ARG", 3)]


def test_unbounded_and_broad_retries(tmp_path):
    text = (
        "class A < ApplicationJob\n  retry_on Timeout::Error, attempts: :unlimited\n  retry_on StandardError, wait: 5\n"
        "  retry_on Net::ReadTimeout, wait: :polynomially_longer, attempts: 5\nend\n"
        "class B\n  include Sidekiq::Job\n  sidekiq_options retry: 100\n  sidekiq_options retry: 5\nend\n"
    )
    assert run(tmp_path, "app/jobs/a.rb", text) == [
        ("JOB-RETRY-UNBOUNDED", 2), ("JOB-RETRY-BROAD", 3), ("JOB-RETRY-UNBOUNDED", 8)
    ]


def test_celery_retry_rules(tmp_path):
    text = (
        "@shared_task(bind=True, max_retries=None, time_limit=30)\ndef a(self):\n    pass\n\n"
        "@shared_task(autoretry_for=(Exception,), retry_backoff=True, time_limit=30)\ndef b():\n    pass\n\n"
        "@shared_task(autoretry_for=(IOError,), max_retries=3, time_limit=30)\ndef c():\n    pass\n\n"
        "@shared_task(autoretry_for=(IOError,), retry_backoff=True, time_limit=30)\ndef d():\n    pass\n"
    )
    assert run(tmp_path, "shop/tasks.py", text) == [
        ("JOB-RETRY-UNBOUNDED", 1), ("JOB-RETRY-BROAD", 5), ("JOB-RETRY-NO-BACKOFF", 9)
    ]


def test_celery_manual_retry_without_countdown(tmp_path):
    text = "@shared_task(time_limit=5)\ndef a(self):\n    try:\n        x()\n    except IOError as exc:\n        raise self.retry(exc=exc)\n"
    assert run(tmp_path, "shop/tasks.py", text) == [("JOB-RETRY-NO-BACKOFF", 6)]
    assert rules(tmp_path / "ok", "shop/tasks.py", text.replace("exc=exc", "exc=exc, countdown=30")) == []


def test_celery_missing_time_limit(tmp_path):
    text = "@shared_task\ndef a():\n    pass\n\n@app.task(soft_time_limit=10)\ndef b():\n    pass\n\n@app.task(bind=True)\ndef c(self):\n    pass\n"
    assert run(tmp_path, "shop/tasks.py", text) == [("JOB-NO-TIMEOUT", 1), ("JOB-NO-TIMEOUT", 9)]


def test_laravel_job_retry_and_timeout(tmp_path):
    bad = "<?php\nclass A implements ShouldQueue\n{\n    public $tries = 0;\n}\n"
    assert run(tmp_path / "a", "app/Jobs/A.php", bad) == [("JOB-NO-TIMEOUT", 2), ("JOB-RETRY-UNBOUNDED", 4)]
    nobackoff = "<?php\nclass B implements ShouldQueue\n{\n    public $tries = 5;\n    public $timeout = 30;\n}\n"
    assert run(tmp_path / "b", "app/Jobs/B.php", nobackoff) == [("JOB-RETRY-NO-BACKOFF", 4)]
    good = nobackoff.replace("30;", "30;\n    public $backoff = [10, 60];")
    assert rules(tmp_path / "c", "app/Jobs/B.php", good) == []
    method_style = "<?php\nclass C implements ShouldQueue\n{\n    public $tries = 3;\n    public function timeout() { return 5; }\n    public function backoff() { return 5; }\n}\n"
    assert rules(tmp_path / "d", "app/Jobs/C.php", method_style) == []


def test_bullmq_attempts_without_backoff(tmp_path):
    text = (
        "await queue.add('a', {}, { attempts: 5 });\n"
        "await queue.add('b', {}, { attempts: 5, backoff: { type: 'exponential', delay: 1000 } });\n"
        "await queue.add('c', {}, {\n  attempts: 3,\n  backoff: 500,\n});\n"
        "await queue.add('d', {}, { attempts: 1 });\n"
    )
    assert run(tmp_path, "src/q.ts", text) == [("JOB-RETRY-NO-BACKOFF", 1)]


def test_http_without_timeout_in_jobs(tmp_path):
    py = "@shared_task(time_limit=5)\ndef a():\n    requests.get(url)\n    requests.post(url,\n        json=data, timeout=5)\n"
    assert run(tmp_path / "py", "shop/tasks.py", py) == [("JOB-HTTP-NO-TIMEOUT", 3)]
    js = "import { Worker } from 'bullmq';\nnew Worker('q', async () => {\n  await axios.get(url);\n  await axios.get(url, { timeout: 5000 });\n  await fetch(url);\n  await fetch(url, { signal: AbortSignal.timeout(5000) });\n});\n"
    assert run(tmp_path / "js", "src/w.ts", js) == [("JOB-HTTP-NO-TIMEOUT", 3), ("JOB-HTTP-NO-TIMEOUT", 5)]
    php = "<?php\nclass A implements ShouldQueue\n{\n    public $timeout = 5;\n    public function handle() { $c = new Client(); $d = new Client(['timeout' => 5]); }\n}\n"
    assert run(tmp_path / "php", "app/Jobs/A.php", php) == [("JOB-HTTP-NO-TIMEOUT", 5)]


def test_http_outside_jobs_is_ignored(tmp_path):
    assert rules(tmp_path, "shop/views.py", "requests.get(url)\n") == []


def test_unbatched_iteration_in_jobs(tmp_path):
    rb = SIDEKIQ_HEAD + "  def perform\n    User.where(active: true).each { |u| u.touch }\n    User.where(active: true).find_each { |u| u.touch }\n    User.all.in_batches.each { |b| b.touch_all }\n  end\nend\n"
    assert run(tmp_path / "rb", "app/jobs/s.rb", rb) == [("JOB-NO-BATCH", 4)]
    py = "@shared_task(time_limit=5)\ndef a():\n    for o in Order.objects.all():\n        pass\n    for o in Order.objects.all().iterator(chunk_size=500):\n        pass\n    for o in Order.objects.filter(a=1)[:100]:\n        pass\n"
    assert run(tmp_path / "py", "shop/tasks.py", py) == [("JOB-NO-BATCH", 3)]
    php = "<?php\nclass A implements ShouldQueue\n{\n    public $timeout = 5;\n    public function handle() {\n        User::all()->each(fn ($u) => $u->touch());\n        User::query()->chunkById(100, fn ($c) => $c->each->touch());\n        foreach (User::cursor() as $u) {}\n    }\n}\n"
    assert run(tmp_path / "php", "app/Jobs/A.php", php) == [("JOB-NO-BATCH", 6)]
    js = "import { Worker } from 'bullmq';\nnew Worker('q', async () => {\n  const a = await prisma.user.findMany();\n  const b = await prisma.user.findMany({ take: 100 });\n});\n"
    assert run(tmp_path / "js", "src/w.ts", js) == [("JOB-NO-BATCH", 3)]


def test_non_idempotent_operations(tmp_path):
    rb = SIDEKIQ_HEAD + "  def perform(id)\n    Stripe::PaymentIntent.create({ amount: 1 })\n    Stripe::PaymentIntent.create({ amount: 1 }, { idempotency_key: \"o-#{id}\" })\n    Order.find(id).increment!(:tries)\n  end\nend\n"
    assert run(tmp_path / "rb", "app/jobs/s.rb", rb) == [("JOB-NON-IDEMPOTENT", 4), ("JOB-NON-IDEMPOTENT", 6)]
    py = "@shared_task(time_limit=5)\ndef a(i):\n    stripe.PaymentIntent.create(amount=1)\n    stripe.PaymentIntent.create(amount=1, idempotency_key=str(i))\n    Order.objects.filter(pk=i).update(n=F('n') + 1)\n"
    assert run(tmp_path / "py", "shop/tasks.py", py) == [("JOB-NON-IDEMPOTENT", 3), ("JOB-NON-IDEMPOTENT", 5)]
    js = "import { Worker } from 'bullmq';\nnew Worker('q', async () => {\n  await stripe.paymentIntents.create({ amount: 1 });\n  await stripe.paymentIntents.create({ amount: 1 }, { idempotencyKey: key });\n  await prisma.user.update({ data: { n: { increment: 1 } } });\n});\n"
    assert run(tmp_path / "js", "src/w.ts", js) == [("JOB-NON-IDEMPOTENT", 3), ("JOB-NON-IDEMPOTENT", 5)]
    php = "<?php\nclass A implements ShouldQueue\n{\n    public $timeout = 5;\n    public function handle() {\n        $s->paymentIntents->create(['amount' => 1]);\n        $s->paymentIntents->create(['amount' => 1], ['idempotency_key' => $k]);\n        $u->increment('n');\n    }\n}\n"
    assert run(tmp_path / "php", "app/Jobs/A.php", php) == [("JOB-NON-IDEMPOTENT", 6), ("JOB-NON-IDEMPOTENT", 8)]


def test_self_reenqueue_from_rescue(tmp_path):
    rb = SIDEKIQ_HEAD + "  def perform(id)\n    work(id)\n  rescue Timeout::Error\n    SyncJob.perform_in(5, id)\n  end\nend\n"
    assert run(tmp_path / "rb", "app/jobs/s.rb", rb) == [("JOB-SELF-REENQUEUE", 6)]
    py = "@shared_task(time_limit=5)\ndef a(i):\n    try:\n        x()\n    except IOError:\n        a.delay(i)\n"
    assert run(tmp_path / "py", "shop/tasks.py", py) == [("JOB-SELF-REENQUEUE", 6)]


def test_suppression_marker(tmp_path):
    text = "transaction do\n  SyncJob.perform_async(id) # job-safety: ok\nend\n"
    assert rules(tmp_path, "app/models/o.rb", text) == []


def test_comments_are_ignored(tmp_path):
    text = "transaction do\n  # SyncJob.perform_async(id)\nend\n"
    assert rules(tmp_path, "app/models/o.rb", text) == []


def test_secret_and_config_files_are_never_read(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    for name in (".hidden.rb", "app/.hidden.rb", "config/credentials.rb", "config/secrets.rb", "config/database.php", "config/queue.php", "config/redis.rb", "app/settings.py"):
        assert rules(tmp_path / name.replace("/", "_"), name, body) == [], name


def test_hidden_directories_and_virtualenvs_are_skipped(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    assert rules(tmp_path / "a", ".hidden-dir/o.rb", body) == []
    assert rules(tmp_path / "b", "myenv/o.rb", body) == ["JOB-TXN-ENQUEUE"]
    (tmp_path / "c" / "venvdir").mkdir(parents=True)
    (tmp_path / "c" / "venvdir" / "pyvenv.cfg").write_text("home = x\n")
    assert rules(tmp_path / "c", "venvdir/o.rb", body) == []


def test_symlinked_files_are_not_read(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    outside = tmp_path / "outside.rb"
    outside.write_text(body)
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "link.rb").symlink_to(outside)
    assert [f.rule for f in scanner.scan([str(tmp_path / "proj")])] == []


def test_skips_vendor_and_node_modules(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    assert rules(tmp_path, "vendor/x/o.rb", body) == []
    assert rules(tmp_path, "node_modules/x/o.rb", body) == []


def test_cli_exit_codes_and_json(tmp_path):
    (tmp_path / "o.rb").write_text("transaction do\n  SyncJob.perform_async(id)\nend\n")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--json", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)[0]["rule"] == "JOB-TXN-ENQUEUE"
    (tmp_path / "o.rb").write_text("x = 1\n")
    assert subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True).returncode == 0


def test_cli_missing_path_errors(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope")], capture_output=True, text=True)
    assert proc.returncode == 2


def test_active_job_record_named_variables_are_fine(tmp_path):
    assert rules(tmp_path, "app/models/o.rb", "SyncJob.perform_later(record)\nSyncJob.perform_later(model)\n") == []
    assert rules(tmp_path / "s", "app/models/o.rb", "SyncJob.perform_async(record)\n") == ["JOB-OBJECT-ARG"]


def test_one_line_transaction_forms(tmp_path):
    assert rules(tmp_path / "a", "a.rb", "ActiveRecord::Base.transaction { Foo.perform_async(1) }\n") == ["JOB-TXN-ENQUEUE"]
    assert rules(tmp_path / "b", "b.php", "<?php\nDB::transaction(function () { dispatch(new Foo(1)); });\n") == ["JOB-TXN-ENQUEUE"]
    assert rules(tmp_path / "c", "c.ts", "await prisma.$transaction(async tx => { await emailQueue.add('a', {}); });\n") == ["JOB-TXN-ENQUEUE"]


def test_retry_backoff_is_checked_per_task(tmp_path):
    text = (
        "@shared_task(retry_backoff=True, time_limit=5)\ndef a():\n    pass\n\n"
        "@shared_task(time_limit=5)\ndef b(self):\n    try:\n        x()\n    except IOError as exc:\n        raise self.retry(exc=exc)\n"
    )
    assert run(tmp_path, "shop/tasks.py", text) == [("JOB-RETRY-NO-BACKOFF", 10)]


def test_retry_on_with_parentheses_and_multiline_php_class(tmp_path):
    assert rules(tmp_path / "a", "app/jobs/a.rb", "class A < ApplicationJob\n  retry_on(StandardError, wait: 5)\nend\n") == ["JOB-RETRY-BROAD"]
    php = "<?php\nclass A\n    extends Base\n    implements ShouldQueue\n{\n}\n"
    assert rules(tmp_path / "b", "app/Jobs/A.php", php) == ["JOB-NO-TIMEOUT"]


def test_job_files_named_like_credentials_are_scanned_but_tests_are_not(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    assert rules(tmp_path / "a", "app/jobs/refresh_credentials_job.rb", body) == ["JOB-TXN-ENQUEUE"]
    assert rules(tmp_path / "b", "spec/models/o_spec.rb", body) == []
    assert rules(tmp_path / "c", "src/o.test.ts", "await prisma.$transaction(async tx => { await emailQueue.add('a', {}); });\n") == []


def test_batch_negative_with_limit(tmp_path):
    text = SIDEKIQ_HEAD + "  def perform\n    User.where(a: 1).limit(10).each { |u| u.touch }\n  end\nend\n"
    assert rules(tmp_path, "app/jobs/s.rb", text) == []


def test_bare_task_decorator_with_manual_retry_does_not_crash(tmp_path):
    text = "@shared_task\ndef a(self):\n    try:\n        x()\n    except IOError as exc:\n        raise self.retry(exc=exc)\n"
    assert ("JOB-RETRY-NO-BACKOFF", 6) in run(tmp_path, "shop/tasks.py", text)


def test_credentials_named_job_file_is_scanned_but_credentials_yml_is_not(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    assert rules(tmp_path / "a", "app/jobs/credentials_refresh_job.rb", body) == ["JOB-TXN-ENQUEUE"]


def test_split_settings_modules_are_never_read(tmp_path):
    body = "transaction do\n  SyncJob.perform_async(id)\nend\n"
    for name in ("config/settings/production.py", "local_settings.py"):
        assert rules(tmp_path / name.replace("/", "_"), name, body.replace("transaction do", "with transaction.atomic():")) == [], name
