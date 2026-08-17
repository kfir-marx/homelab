from prometheus_client import Counter, Gauge, Histogram

JOBS_DISCOVERED = Counter("job_assistant_jobs_discovered_total", "Jobs discovered", ["source"])
JOBS_FILTERED = Counter("job_assistant_jobs_filtered_total", "Jobs filtered", ["reason"])
JOBS_DEDUPLICATED = Counter(
    "job_assistant_jobs_deduplicated_total", "Jobs deduplicated", ["source"]
)
SOURCE_FAILURES = Counter("job_assistant_source_failures_total", "Source failures", ["source"])
GENERATION_DURATION = Histogram(
    "job_assistant_generation_duration_seconds", "Generation duration", ["provider"]
)
GENERATION_FAILURES = Counter(
    "job_assistant_generation_failures_total", "Generation failures", ["code"]
)
CODEX_FAILURES = Counter(
    "job_assistant_codex_failures_total", "Codex auth or limit failures", ["kind"]
)
DELIVERY_FAILURES = Counter(
    "job_assistant_delivery_failures_total", "Notification failures", ["channel"]
)
QUEUE_DEPTH = Gauge("job_assistant_queue_depth", "Pending queue depth", ["queue"])
QUEUE_OLDEST = Gauge("job_assistant_queue_oldest_age_seconds", "Oldest pending item age", ["queue"])
APPLICATIONS = Gauge("job_assistant_applications", "Applications by status", ["status"])
OUTREACH = Gauge("job_assistant_outreach", "Outreach by status", ["status"])
JOBS_BY_SOURCE = Gauge("job_assistant_jobs_by_source", "Persisted source occurrences", ["source"])
JOB_SCORE_OUTCOMES = Gauge(
    "job_assistant_job_score_outcomes", "Persisted ranking outcomes", ["outcome"]
)
GENERATION_RUNS = Gauge(
    "job_assistant_generation_runs",
    "Persisted generation runs",
    ["status", "error_code"],
)
OUTBOX_EVENTS = Gauge(
    "job_assistant_outbox_events", "Persisted outbox events", ["status", "channel"]
)
SOURCE_CONSECUTIVE_FAILURES = Gauge(
    "job_assistant_source_consecutive_failures",
    "Persisted consecutive source failures",
    ["source"],
)
