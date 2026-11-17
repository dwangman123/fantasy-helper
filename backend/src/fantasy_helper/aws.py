"""The AWS Lambda entry point for the scheduled ingest, which EventBridge Scheduler runs (see
infra/terraform). The REST API needs none: its image runs the app with uvicorn behind the Lambda
Web Adapter (see the Dockerfile). Both read their secrets through FH_SECRET_ID (see config.py)."""

import logging

from fantasy_helper.ingest.run import IngestBusy
from fantasy_helper.ingest.run import run as run_ingest

# Lambda installs its own root handler, so basicConfig would do nothing; without INFO the
# ingest's progress lines never reach CloudWatch.
logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("fantasy_helper.aws")


def ingest(event: dict, context: object) -> dict:
    """A failed ingest raises, so Lambda counts it as an error; a busy one is skipped, since the
    run holding the lock is doing the same work."""
    try:
        run = run_ingest()
    except IngestBusy as exc:
        log.warning("%s; skipping this run", exc)
        return {"status": "skipped"}
    return {"id": run.id, "status": run.status, "counts": run.counts, "warnings": run.warnings}
