# Job assistant

This service discovers DevOps/platform/SRE jobs in Israel and fully remote
roles, recommends at most five good matches per day, and prepares truthful
application material for explicit human review. It is deliberately not a
bulk-application bot: it never logs into or scrapes LinkedIn, never guesses an
email recipient, and never sends outreach without a final Telegram
confirmation.

The implementation is a Python 3.12 modular monolith. One image exposes these
process roles:

| Command | Responsibility | Credentials |
| --- | --- | --- |
| `api` | Private health and Prometheus endpoints | database only |
| `telegram` | Allowlisted long-polling UI and final-file upload | database, Telegram |
| `worker` | Queue/outbox notification and approved SMTP delivery | database, Telegram, SMTP |
| `generation-worker` | Serialized, isolated `codex exec` generation | restricted database, Codex auth only |
| `discover` | One IMAP/public-ATS discovery and ranking pass | database, IMAP |
| `migrate` | Alembic schema migration | database owner |

PostgreSQL is the system of record and queue backend. Work claiming uses
`FOR UPDATE SKIP LOCKED`; notifications use a transactional outbox. Artifact
bytes live behind the `ArtifactStorage` interface and are stored on the
critical retained NFS PVC rather than in PostgreSQL.

## Local checks

Use a Python 3.12 or newer virtual environment:

```bash
cd services/job-assistant
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
```

The PostgreSQL integration test is enabled by setting `TEST_POSTGRES_URL` to a
disposable database. Tests use fake providers and fixtures; none calls Telegram,
SMTP, ATS systems, or a real Codex account.

Run local roles with `JOB_ASSISTANT_*` environment variables, for example:

```bash
JOB_ASSISTANT_DATABASE_URL='postgresql+psycopg://job_assistant:password@localhost/job_assistant' \
JOB_ASSISTANT_GENERATION_DATABASE_URL='postgresql+psycopg://job_assistant_generation:other-password@localhost/job_assistant' \
  .venv/bin/job-assistant migrate
JOB_ASSISTANT_DATABASE_URL='postgresql+psycopg://job_assistant:password@localhost/job_assistant' \
  .venv/bin/job-assistant api
```

Docker Compose is intentionally not introduced because this repository did not
already use it. The Kubernetes deployment is the authoritative production
shape.

## Configuration and private inputs

All settings use the `JOB_ASSISTANT_` prefix. The complete typed defaults are
in `src/job_assistant/config.py`. Important paths are:

| Setting | Default |
| --- | --- |
| `DATABASE_URL` | PostgreSQL SQLAlchemy URL |
| `GENERATION_DATABASE_URL` | Restricted role URL; required only by `migrate` |
| `ARTIFACT_ROOT` | `/data/artifacts` |
| `CAREER_INVENTORY_PATH` | `/data/private/career-inventory.yaml` |
| `CV_TEMPLATE_PATH` | `/data/private/cv-template.docx` |
| `SEARCH_CRITERIA_PATH` | `/app/config/search-criteria.yaml` |
| `COMPANY_REGISTRY_PATH` | `/app/config/company-registry.yaml` |
| `CODEX_HOME` | `/var/lib/codex` |
| `CODEX_TIMEOUT_SECONDS` | `600` |
| `MAX_UPLOAD_BYTES` | `10000000` |

No real career inventory or CV template was found in the repository or home
directory during implementation. Install the real private files at the paths
above before attempting generation. The versioned
`config/career-inventory.example.yaml` contains fake data and documents the
strict schema; it must never be treated as the candidate's factual source.

ATS registry entries are disabled, non-working placeholders until their public
board slugs and terms are verified. Search criteria are versioned through their
content hash, which is stored with every score.

## Extension points

Domain logic depends on protocols in `interfaces.py`:

- `JobSource`
- `GenerationProvider`
- `ContactResolver`
- `DeliveryProvider`
- `ArtifactStorage`
- `NotificationProvider`
- `BounceProvider`

To add a job source, implement `discover() -> Iterable[NormalizedJob]`, keep
network parsing isolated, sanitize HTML, add recorded fixtures, and register it
in `discovery.py`. To add generation or delivery, implement the corresponding
protocol and keep credentials out of the domain layer. Delivery providers must
retain the contact-confidence and explicit-approval gate.

See [architecture](../../docs/job-assistant-architecture.md) and the
[deployment/operations runbook](../../docs/job-assistant-runbook.md).
