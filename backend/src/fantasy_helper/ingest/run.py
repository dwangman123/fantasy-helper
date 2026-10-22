"""Ingest job: fetch a snapshot from ESPN and store it. Entry point: `fh-ingest`."""

import logging
import sys
from datetime import UTC, datetime

from fantasy_helper.calendar import load_calendar
from fantasy_helper.config import get_settings
from fantasy_helper.db import models as m
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.espn.client import fetch_snapshot
from fantasy_helper.ingest.store import store_snapshot

log = logging.getLogger("fantasy_helper.ingest")


def run() -> m.IngestRun:
    settings = get_settings()
    Session = get_sessionmaker()

    with Session.begin() as session:
        run_row = m.IngestRun(season=settings.season, started_at=datetime.now(UTC), status="running")
        session.add(run_row)
    run_id = run_row.id

    try:
        snap = fetch_snapshot(settings)
        warnings = load_calendar(settings.season).validate_against_espn(snap.matchup_ids)
        for w in warnings:
            log.warning("calendar mismatch: %s", w)
        with Session.begin() as session:
            counts = store_snapshot(session, snap)
            run_row = session.get(m.IngestRun, run_id)
            run_row.status, run_row.counts, run_row.warnings = "success", counts, warnings
            run_row.finished_at = datetime.now(UTC)
        log.info("ingest %s succeeded: %s", run_id, counts)
    except Exception as exc:
        with Session.begin() as session:
            run_row = session.get(m.IngestRun, run_id)
            run_row.status, run_row.error = "failed", repr(exc)
            run_row.finished_at = datetime.now(UTC)
        log.exception("ingest %s failed", run_id)
        raise
    return run_row


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
