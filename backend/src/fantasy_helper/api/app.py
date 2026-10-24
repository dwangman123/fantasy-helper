"""REST API over the stored snapshot; `fh-api` serves it with uvicorn.

Everything comes from the latest ingest. Simulations are cached in memory per
snapshot (keyed by the league row's updated_at), so the next request after an
ingest recomputes. Matchup and waiver views are from one team's side, mine
unless `team_id` says otherwise.
"""

import argparse
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterator
from typing import Annotated, Any

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fantasy_helper.analytics.waivers import category_board, side_view
from fantasy_helper.api import schemas as s
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.context import LeagueContext, load_context
from fantasy_helper.db import models as m
from fantasy_helper.db import queries as q
from fantasy_helper.db.session import get_sessionmaker
from fantasy_helper.predict import WeekPredictions, predict_current_week
from fantasy_helper.waivers import WaiverReport, waiver_report

CACHE_SIZE = 32


class SnapshotCache:
    """A small LRU of computed results. Two requests for the same key at once may both
    compute it; the results are identical (seeded), so that only costs time."""

    def __init__(self, size: int = CACHE_SIZE):
        self.size = size
        self._items: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable, compute: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = compute()
        with self._lock:
            self._items[key] = value
            while len(self._items) > self.size:
                self._items.popitem(last=False)
        return value


router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as session:
        yield session


class Ctx:
    """Per-request access to settings, the cache and the current snapshot."""

    def __init__(self, request: Request, session: Annotated[Session, Depends(get_session)]):
        self.session = session
        self.settings: Settings = request.app.state.settings
        self.cache: SnapshotCache = request.app.state.cache
        league = session.get(m.League, self.settings.season)
        if league is None:
            raise LookupError(f"No league stored for season {self.settings.season}; run fh-ingest first")
        self.league = league
        self.key = (self.settings.season, league.updated_at)

    @property
    def season(self) -> int:
        return self.settings.season

    def context(self) -> LeagueContext:
        return self.cache.get(("context", self.key), lambda: load_context(self.session, self.season))

    def week(self) -> WeekPredictions:
        return self.cache.get(
            ("week", self.key), lambda: predict_current_week(self.session, self.season, ctx=self.context())
        )


CtxDep = Annotated[Ctx, Depends()]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/league", response_model=s.LeagueOut)
def league(c: CtxDep):
    ctx = c.context()
    run = c.session.scalars(select(m.IngestRun).order_by(m.IngestRun.id.desc()).limit(1)).first()
    return s.LeagueOut(
        season=c.season,
        name=c.league.name,
        my_team_id=c.settings.my_team_id,
        today=ctx.calendar.date_for(ctx.scoring_period),
        scoring_period=ctx.scoring_period,
        week=s.WeekOut(
            matchup_period=ctx.matchup_period,
            start=ctx.week.start,
            end=ctx.week.end,
            days=ctx.week.days,
            days_left=len(ctx.days_left),
        ),
        categories=[s.CategoryOut(key=x.key, is_percentage=x.is_percentage, reverse=x.reverse) for x in ctx.categories],
        teams=[s.TeamOut(**vars(t)) for t in q.load_teams(c.session, c.season)],
        last_ingest=s.IngestOut(
            id=run.id,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            warnings=run.warnings or [],
        )
        if run
        else None,
    )


@router.get("/matchups", response_model=list[s.MatchupSummary])
def matchups(c: CtxDep):
    return [
        s.MatchupSummary(
            espn_id=mu.espn_id,
            home_team_id=mu.home_team_id,
            away_team_id=mu.away_team_id,
            p_home=p.p_home,
            p_tie=p.p_tie,
            p_away=p.p_away,
            expected_home=p.expected_score["home"],
            expected_away=p.expected_score["away"],
        )
        for mu, p in c.week().results
    ]


@router.get("/matchup", response_model=s.MatchupDetail)
def matchup(c: CtxDep, team_id: int | None = None):
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx, week = c.context(), c.week()
    found = [(mu, p) for mu, p in week.results if team_id in (mu.home_team_id, mu.away_team_id)]
    if not found:
        raise LookupError(f"Team {team_id} has no opponent in matchup period {week.matchup_period}")
    mu, pred = found[0]
    side, other = ("home", "away") if mu.home_team_id == team_id else ("away", "home")
    view = side_view(pred, side)
    board = category_board(view.cat_p_win)
    now = {"home": mu.home_cats, "away": mu.away_cats}
    rows = c.session.scalars(
        select(m.MatchupPrediction)
        .where(m.MatchupPrediction.season == c.season, m.MatchupPrediction.espn_id == mu.espn_id)
        .order_by(m.MatchupPrediction.id)
    )
    history = [
        s.OddsPoint(
            created_at=r.created_at,
            scoring_period=r.scoring_period,
            p_win=r.p_home if side == "home" else r.p_away,
            p_tie=r.p_tie,
            p_loss=r.p_away if side == "home" else r.p_home,
        )
        for r in rows
    ]
    hw, aw, ties = pred.most_likely_score
    likely = {"home": hw, "away": aw}
    return s.MatchupDetail(
        espn_id=mu.espn_id,
        matchup_period=mu.matchup_period,
        team_id=team_id,
        opponent_id=mu.away_team_id if side == "home" else mu.home_team_id,
        week_days=[ctx.calendar.date_for(d) for d in week.week_days],
        days_left=[ctx.calendar.date_for(d) for d in week.days_left],
        p_win=view.p_win,
        p_tie=view.p_tie,
        p_loss=view.p_loss,
        expected=s.Score(
            me=pred.expected_score[side], opp=pred.expected_score[other], ties=pred.expected_score["ties"]
        ),
        most_likely=s.Score(me=likely[side], opp=likely[other], ties=ties),
        starts_left_me=pred.expected_games[side],
        starts_left_opp=pred.expected_games[other],
        categories=[
            s.CategoryOdds(
                key=cat.key,
                is_percentage=cat.is_percentage,
                reverse=cat.reverse,
                now_me=now[side].get(cat.key),
                now_opp=now[other].get(cat.key),
                proj_me=pred.projected_totals[side][cat.key],
                proj_opp=pred.projected_totals[other][cat.key],
                p_win=view.cat_p_win[cat.key],
                p_tie=pred.cat_probs[cat.key]["tie"],
                p_loss=view.cat_p_loss[cat.key],
                label=board[cat.key],
            )
            for cat in ctx.categories
        ],
        history=history,
        n_sims=pred.n_sims,
    )


def _player(ctx: LeagueContext, pid: int | None) -> s.PlayerOut | None:
    if pid is None:
        return None
    p, z = ctx.players[pid], ctx.z[pid]
    return s.PlayerOut(
        player_id=pid,
        name=p.name,
        pro_team=p.pro_team,
        position=p.position,
        injury_status=p.injury_status,
        z=z.by_cat,
        z_total=z.total,
    )


@router.get("/waivers", response_model=s.WaiversOut)
def waivers(c: CtxDep, team_id: int | None = None, same_day: bool = False):
    """`same_day`: rank as if a pickup can play today, not from tomorrow."""
    team_id = c.settings.my_team_id if team_id is None else team_id
    ctx = c.context()
    report: WaiverReport = c.cache.get(
        ("waivers", c.key, team_id, same_day),
        lambda: waiver_report(c.session, c.season, team_id, same_day=same_day, ctx=ctx),
    )
    base, date_for = report.stream.baseline, ctx.calendar.date_for
    return s.WaiversOut(
        team_id=team_id,
        opponent_id=report.opponent_id,
        matchup_period=report.matchup_period,
        first_day=date_for(report.first_day),
        days_left=[date_for(d) for d in report.days_left],
        baseline=s.Baseline(
            p_win=base.p_win,
            p_tie=base.p_tie,
            p_loss=base.p_loss,
            expected_cats=base.expected_cats,
            starts_left=base.expected_games,
        ),
        board=[
            s.BoardEntry(
                key=k,
                label=label,
                p_win=base.cat_p_win[k],
                p_tie=1 - base.cat_p_win[k] - base.cat_p_loss[k],
                p_loss=base.cat_p_loss[k],
            )
            for k, label in report.stream.board.items()
        ],
        stream=[
            s.StreamRow(
                add=_player(ctx, o.add_id),
                drop=_player(ctx, o.drop_id),
                games=report.games_this_week[o.add_id],
                extra_starts=o.extra_starts,
                p_win=o.result.p_win,
                d_p_win=o.d_p_win,
                d_p_tie=o.d_p_tie,
                d_expected_cats=o.d_expected_cats,
                cat_d_p_win=o.cat_d_p_win,
                gained=o.gained,
                lost=o.lost,
            )
            for o in report.stream.options
        ],
        stream_screened=report.stream.screened,
        hold=s.HoldOut(
            weeks=report.hold.weeks,
            first_period=report.hold_periods[0] if report.hold_periods else None,
            cats_per_week=report.hold.cats_per_week,
            cat_p_win=report.hold.cat_p_win,
            rows=[
                s.HoldRow(
                    add=_player(ctx, o.add_id),
                    drop=_player(ctx, o.drop_id),
                    games=report.hold_games[o.add_id],
                    d_cats_per_week=o.d_cats_per_week,
                    cat_d_p_win=o.cat_d_p_win,
                )
                for o in report.hold.options
            ],
            screened=report.hold.screened,
        ),
        drops=[_player(ctx, d) for d in report.drop_ids],
        free_agents=report.free_agents,
        assumptions=report.assumptions,
        n_sims=report.n_sims,
    )


def create_app(session_factory: sessionmaker | None = None, settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Fantasy Helper", version="0.1.0")
    app.state.sessionmaker = session_factory or get_sessionmaker()
    app.state.settings = settings or get_settings()
    app.state.cache = SnapshotCache()
    app.include_router(router)

    @app.exception_handler(LookupError)
    def not_found(_: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    def bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Fantasy Helper REST API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = parser.parse_args()
    uvicorn.run("fantasy_helper.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
