"""Ingest job: fetch a snapshot from ESPN and store it. Entry point: `fh-ingest`."""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from fantasy_helper.analytics.actuals import espn_mismatches
from fantasy_helper.calendar import load_calendar
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.db import models as m
from fantasy_helper.db.queries import load_game_stat_days, load_last_move_day
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.espn.client import fetch_snapshot
from fantasy_helper.ingest.store import store_snapshot
from fantasy_helper.predict import predict_current_week, store_predictions

log = logging.getLogger("fantasy_helper.ingest")

LOCK_KEY = 0x46480001  # any constant: names this job's advisory lock in the database


class IngestBusy(RuntimeError):
    """Another ingest is already running against the same database."""


@contextmanager
def exclusive(Session: sessionmaker) -> Iterator[None]:
    """Hold a Postgres advisory lock for the run, so the scheduled ingest and a refresh in another
    process never overlap. SQLite has no such lock; locally the API's own lock is enough."""
    engine = Session.kw["bind"]
    if engine.dialect.name != "postgresql":
        yield
        return
    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            raise IngestBusy("Another ingest is already running")
        conn.commit()  # the lock belongs to the session, so don't sit idle in a transaction
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})


def run(settings: Settings | None = None, session_factory: sessionmaker | None = None) -> m.IngestRun:
    """Also run by the API's refresh endpoint, which passes its own settings and sessions.
    Raises IngestBusy, before recording a run, if another ingest holds the database."""
    settings = settings or get_settings()
    Session = session_factory or get_sessionmaker()
    with exclusive(Session):
        return _run(settings, Session)


def _run(settings: Settings, Session: sessionmaker) -> m.IngestRun:
    with Session.begin() as session:
        run_row = m.IngestRun(season=settings.season, started_at=datetime.now(UTC), status="running")
        session.add(run_row)
        stored_game_days = load_game_stat_days(session, settings.season)
        last_move_day = load_last_move_day(session, settings.season)
    run_id = run_row.id

    try:
        snap = fetch_snapshot(settings, stored_game_days, last_move_day)
        calendar = load_calendar(settings.season)
        warnings = calendar.validate_against_espn(snap.matchup_ids)
        for w in warnings:
            log.warning("calendar mismatch: %s", w)
        for w in snap.warnings:
            log.warning(w)
        warnings += snap.warnings
        week_days = set(calendar.week(snap.current_matchup_period).scoring_periods)
        for w in espn_mismatches(
            [mu for mu in snap.matchups if mu.matchup_period == snap.current_matchup_period],
            [g for g in snap.game_stats if g.scoring_period in week_days],
            snap.live_stats,
        ):
            log.warning(w)
            warnings.append(w)
        with Session.begin() as session:
            counts = store_snapshot(session, snap)
            run_row = session.get(m.IngestRun, run_id)
            run_row.status, run_row.counts, run_row.warnings = "success", counts, warnings
            run_row.finished_at = datetime.now(UTC)
        log.info("ingest %s succeeded: %s", run_id, counts)
        _predict(Session, settings.season, run_id, warnings)
    except Exception as exc:
        with Session.begin() as session:
            run_row = session.get(m.IngestRun, run_id)
            run_row.status, run_row.error = "failed", repr(exc)
            run_row.finished_at = datetime.now(UTC)
        log.exception("ingest %s failed", run_id)
        raise
    return run_row


def _predict(Session, season: int, run_id: int, warnings: list[str]) -> None:
    """Append this week's matchup predictions. A failure here is a warning, not a failed ingest."""
    try:
        with Session.begin() as session:
            n = store_predictions(session, predict_current_week(session, season), run_id, warnings)
            run_row = session.get(m.IngestRun, run_id)
            run_row.counts = {**run_row.counts, "predictions": n}
        log.info("stored %d matchup predictions", n)
    except Exception as exc:
        log.exception("matchup predictions failed")
        with Session.begin() as session:
            run_row = session.get(m.IngestRun, run_id)
            run_row.warnings = [*(run_row.warnings or []), f"matchup predictions failed: {exc!r}"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except IngestBusy as exc:
        log.warning("%s; not starting another", exc)
        sys.exit(1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
