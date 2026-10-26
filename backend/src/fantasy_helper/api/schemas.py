"""Response models. Matchup and waiver views are from one team's side ("me" / "opp"),
so the UI never has to work out whether that team is home or away."""

from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel


def _as_utc(t: datetime) -> datetime:
    # Timestamps are written in UTC, but SQLite hands them back naive; without an
    # offset the browser would read them as local time.
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]


class CategoryOut(BaseModel):
    key: str
    is_percentage: bool
    reverse: bool


class TeamOut(BaseModel):
    team_id: int
    name: str
    abbrev: str
    wins: int
    losses: int
    ties: int


class IngestOut(BaseModel):
    id: int
    status: str
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    warnings: list[str]


class WeekOut(BaseModel):
    matchup_period: int
    start: date
    end: date
    days: int
    days_left: int


class LeagueOut(BaseModel):
    season: int
    name: str
    my_team_id: int
    today: date
    scoring_period: int
    week: WeekOut
    categories: list[CategoryOut]
    teams: list[TeamOut]
    data_as_of: UtcDatetime  # when the stored snapshot was fetched; a failed ingest leaves it as is
    last_ingest: IngestOut | None


class MatchupSummary(BaseModel):
    espn_id: int
    home_team_id: int
    away_team_id: int
    p_home: float
    p_tie: float
    p_away: float
    expected_home: float
    expected_away: float


class Score(BaseModel):
    me: float
    opp: float
    ties: float


class CategoryOdds(BaseModel):
    key: str
    is_percentage: bool
    reverse: bool
    now_me: float | None
    now_opp: float | None
    proj_me: float
    proj_opp: float
    p_win: float
    p_tie: float
    p_loss: float
    label: str  # locked / swing / lost


class OddsPoint(BaseModel):
    created_at: UtcDatetime
    scoring_period: int
    p_win: float
    p_tie: float
    p_loss: float


class MatchupDetail(BaseModel):
    espn_id: int
    matchup_period: int
    team_id: int
    opponent_id: int
    week_days: list[date]
    days_left: list[date]
    p_win: float
    p_tie: float
    p_loss: float
    expected: Score
    most_likely: Score
    starts_left_me: float
    starts_left_opp: float
    categories: list[CategoryOdds]
    history: list[OddsPoint]  # stored predictions from each ingest this week, oldest first
    n_sims: int


class PlayerOut(BaseModel):
    player_id: int
    name: str
    pro_team: str
    position: str
    injury_status: str | None
    z: dict[str, float]  # blended per-category z-scores
    z_total: float


class LineupCell(BaseModel):
    slot: str | None  # None: not on the team that day, or a played day without a box score
    opponent: str | None  # None: no game
    counts: bool  # a started game, so it adds to the team's totals
    actual: bool  # box score; otherwise projected (rate x opponent factor x chance of playing)
    stats: dict[str, float]  # counting stats plus FGM/FGA/FTM/FTA


class LineupPlayer(BaseModel):
    player: PlayerOut
    cells: list[LineupCell]  # one per day of the week
    totals: dict[str, float]  # counted games only


class LineupDay(BaseModel):
    day: date
    played: bool
    totals: dict[str, float]  # counted games only
    starts: float  # started games; from today, weighted by the chance of playing
    missed_starts: int | None  # extra games the best lineup would start; None once played


class TeamLineup(BaseModel):
    """One team's week by player and day, following the lineup set on ESPN."""

    team_id: int
    matchup_period: int
    days: list[LineupDay]
    players: list[LineupPlayer]
    totals: dict[str, float]


class BoardEntry(BaseModel):
    key: str
    label: str
    p_win: float
    p_tie: float
    p_loss: float


class Baseline(BaseModel):
    p_win: float
    p_tie: float
    p_loss: float
    expected_cats: float
    starts_left: float


class StreamRow(BaseModel):
    add: PlayerOut
    drop: PlayerOut | None  # None: an open roster spot
    games: int  # his games this week from the first day he can play
    extra_starts: float
    p_win: float
    d_p_win: float
    d_p_tie: float
    d_expected_cats: float
    cat_d_p_win: dict[str, float]
    gained: list[str]
    lost: list[str]


class HoldRow(BaseModel):
    add: PlayerOut
    drop: PlayerOut | None
    games: int  # his games over the weeks the ranking covers
    d_cats_per_week: float
    cat_d_p_win: dict[str, float]


class HoldOut(BaseModel):
    weeks: int
    first_period: int | None
    cats_per_week: float
    cat_p_win: dict[str, float]
    rows: list[HoldRow]
    screened: int


class WaiversOut(BaseModel):
    team_id: int
    opponent_id: int
    matchup_period: int
    first_day: date  # first day a pickup can play
    days_left: list[date]
    baseline: Baseline
    board: list[BoardEntry]
    stream: list[StreamRow]
    stream_screened: int
    hold: HoldOut
    drops: list[PlayerOut | None]
    free_agents: int
    assumptions: list[str]
    n_sims: int
