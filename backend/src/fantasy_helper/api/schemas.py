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
    owner: str  # the owner's first name, which the UI shows in place of the team name


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


class WeekSpan(BaseModel):
    matchup_period: int
    start: date
    end: date


class LeagueOut(BaseModel):
    season: int
    name: str
    my_team_id: int
    today: date
    scoring_period: int
    week: WeekOut
    weeks: list[WeekSpan]  # the season's weeks so far, the current one last
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
    final: bool = False  # a finished week: the odds are its result, the expected scores its categories won


class Score(BaseModel):
    me: float
    opp: float
    ties: float


class CategoryOdds(BaseModel):
    key: str
    is_percentage: bool
    reverse: bool
    now_me: float | None  # started box scores so far, live games included; None: a percentage with no attempt
    now_opp: float | None
    proj_me: float
    proj_opp: float
    p_win: float
    p_tie: float
    p_loss: float
    label: str | None  # locked / swing / lost; None once the week is over
    result: str | None = None  # win / tie / loss once the week is over


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
    history: list[OddsPoint]  # stored predictions from each ingest that week, oldest first
    n_sims: int
    # A finished week: ESPN's final totals, each category's result, and odds of 1 or 0
    # for the outcome; nothing is simulated (n_sims 0).
    final: bool = False


class PlayerOut(BaseModel):
    player_id: int
    name: str
    pro_team: str
    position: str
    injury_status: str | None
    z: dict[str, float]  # blended per-category z-scores
    value: float  # G-score: z weighted by how much each category bounces week to week


class GameOut(BaseModel):
    """A game today: "pre" (not started), "live" or "final"."""

    state: str
    status: str  # e.g. "Q3 5:21", "OT 1:02", "Final"; empty before the tip
    tip_at: UtcDatetime | None


class LineupCell(BaseModel):
    slot: str | None  # None: not on the team that day, or a played day without a box score
    opponent: str | None  # None: no game
    counts: bool  # a started game, so it adds to the team's totals
    actual: bool  # box score (today's so far once his game tips); otherwise projected
    stats: dict[str, float]  # counting stats plus FGM/FGA/FTM/FTA
    projected: dict[str, float]  # the game's projected final; differs from stats only while it's live
    game: GameOut | None  # today's game; None on other days
    # The game as one fantasy value: its z-scores summed on the single-game scale (see
    # TeamLineup.value_scale). None without a game.
    value: float | None  # of `stats`
    projected_value: float | None  # of `projected`
    value_by_cat: dict[str, float] | None  # `value` per category


class LineupPlayer(BaseModel):
    player: PlayerOut
    cells: list[LineupCell]  # one per day of the week
    totals: dict[str, float]  # counted games' projected finals
    usual: float | None  # his per-game averages valued the same way: what his games average out to


class LineupDay(BaseModel):
    day: date
    played: bool
    actual: dict[str, float]  # counted box scores (today's so far); zeros on days ahead
    projected: dict[str, float]  # counted projected finals
    starts: float  # started games; from today, weighted by the chance of playing
    missed_starts: int | None  # extra games the best lineup would start; None once played
    actual_value: float  # counted box scores' values summed (today's so far)
    projected_value: float  # counted projected finals' values summed


class ValueScale(BaseModel):
    """Mean and spread of one game's value over rostered players' games this season."""

    mean: float
    sd: float


class TeamLineup(BaseModel):
    """One team's week by player and day, following the lineup set on ESPN."""

    team_id: int
    matchup_period: int
    days: list[LineupDay]
    players: list[LineupPlayer]
    totals: dict[str, float]  # the week's projected final
    value_scale: ValueScale


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


class CategoryProfile(BaseModel):
    key: str
    value: float  # per game: the sum of the roster's averages (IR excluded); a fraction for percentages
    rank: int  # 1 = best in the league
    z: float  # against the other teams, higher is better
    punt: bool  # z below the punt threshold


class TeamPlayer(BaseModel):
    player: PlayerOut
    slot: str
    acquisition_type: str | None  # DRAFT / ADD / TRADE
    acquired_at: UtcDatetime | None
    gp: float | None  # games behind this window's averages; None without a line
    mpg: float | None
    per_game: dict[str, float] | None  # counting stats plus makes and attempts
    z: dict[str, float]  # this window, against rostered players
    value: float  # G-score over this window
    specialties: list[str]  # categories he stands out in, strongest first


class MoveOut(BaseModel):
    transaction_id: str | None  # moves in one ESPN transaction share it; None for trades (ESPN hides their ids)
    at: UtcDatetime | None
    type: str  # ADD / DROP / TRADE (a player received in a trade)
    kind: str  # FREEAGENT / WAIVER / TRADE
    player_id: int
    player_name: str | None


class SourceOut(BaseModel):
    label: str
    url: str


class TeamDetail(BaseModel):
    team: TeamOut
    window: str
    windows: list[str]
    profile: list[CategoryProfile]
    punts: list[str]
    players: list[TeamPlayer]
    moves: list[MoveOut]  # newest first
    notes: list[str]
    sources: list[SourceOut]  # where the notes' methods come from


class TeamProfileOut(BaseModel):
    team: TeamOut
    profile: list[CategoryProfile]
    punts: list[str]


class ProfilesOut(BaseModel):
    """Every team's category profile over one stat window: the league heatmap."""

    window: str
    windows: list[str]
    teams: list[TeamProfileOut]
    notes: list[str]


class RecordOut(BaseModel):
    wins: int
    losses: int
    ties: int


class ProjectedRecord(BaseModel):
    wins: float
    losses: float
    ties: float


class PowerRow(BaseModel):
    team: TeamOut
    rank: int
    strength: float  # expected share of weekly matchups won against every other team, rest of season (ties half)
    cats_per_week: float  # expected categories won per week against the other teams
    cat_p_win: dict[str, float]
    all_play: RecordOut  # every completed week against every other team
    punts: list[str]
    projected: ProjectedRecord  # the record so far plus expected results in the regular-season weeks left


class PowerOut(BaseModel):
    weeks: int  # remaining weeks the strength covers
    first_period: int
    completed_weeks: int
    rows: list[PowerRow]  # strongest first
    notes: list[str]


class TradeSideOut(BaseModel):
    team_id: int
    receives: list[PlayerOut]
    sends: list[PlayerOut]
    drops: list[PlayerOut]  # dropped to make room
    adds: list[PlayerOut]  # free agents picked up for the spots the deal opens
    cats_per_week: float  # before the deal, vs a league-average team
    d_cats_per_week: float
    p_week: float  # mean P(win a week) vs a league-average team, before the deal
    d_p_week: float
    cat_p_win: dict[str, float]
    cat_d_p_win: dict[str, float]
    cat_d_value: dict[str, float]  # change in the expected weekly total; percentages: in the percentage


class TradeOut(BaseModel):
    me: TradeSideOut
    partner: TradeSideOut
    weeks: int
    first_period: int
    warnings: list[str]
    assumptions: list[str]


class TradeIdea(BaseModel):
    me: TradeSideOut
    partner: TradeSideOut


class TradesOut(BaseModel):
    team_id: int
    deals: list[TradeIdea]  # both sides gain; best for me first
    candidates: int  # deals screened
    evaluated: int  # deals scored in full
    weeks: int
    first_period: int
    assumptions: list[str]
