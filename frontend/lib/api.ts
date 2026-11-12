// Types mirror backend/src/fantasy_helper/api/schemas.py.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type Category = { key: string; is_percentage: boolean; reverse: boolean };

export type Team = {
  team_id: number;
  name: string;
  abbrev: string;
  wins: number;
  losses: number;
  ties: number;
  owner: string; // the owner's first name, which the UI shows in place of the team name
};

export type IngestRun = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  warnings: string[];
};

export type WeekSpan = { matchup_period: number; start: string; end: string };

export type League = {
  season: number;
  name: string;
  today: string;
  scoring_period: number;
  week: { matchup_period: number; start: string; end: string; days: number; days_left: number };
  weeks: WeekSpan[]; // the season's weeks so far, the current one last
  categories: Category[];
  teams: Team[];
  data_as_of: string; // when the stored snapshot was fetched
  last_ingest: IngestRun | null;
};

export type MatchupSummary = {
  espn_id: number;
  home_team_id: number;
  away_team_id: number;
  p_home: number;
  p_tie: number;
  p_away: number;
  expected_home: number;
  expected_away: number;
  final: boolean; // a finished week: the odds are its result, the expected scores its categories won
};

export type Label = "locked" | "swing" | "lost";

export type Result = "win" | "tie" | "loss";

export type CategoryOdds = Category & {
  now_me: number | null;
  now_opp: number | null;
  proj_me: number;
  proj_opp: number;
  p_win: number;
  p_tie: number;
  p_loss: number;
  label: Label | null; // null once the week is over
  result: Result | null; // set once the week is over
};

export type OddsPoint = { created_at: string; scoring_period: number; p_win: number; p_tie: number; p_loss: number };

export type MatchupDetail = {
  espn_id: number;
  matchup_period: number;
  team_id: number;
  opponent_id: number;
  week_days: string[];
  days_left: string[];
  p_win: number;
  p_tie: number;
  p_loss: number;
  expected: { me: number; opp: number; ties: number };
  most_likely: { me: number; opp: number; ties: number };
  starts_left_me: number;
  starts_left_opp: number;
  categories: CategoryOdds[];
  history: OddsPoint[];
  n_sims: number;
  final: boolean; // a finished week: ESPN's final totals and results, nothing simulated
};

export type Player = {
  player_id: number;
  name: string;
  pro_team: string;
  position: string;
  injury_status: string | null;
  z: Record<string, number>;
  value: number; // G-score: z weighted by how much each category bounces week to week
};

// Counting stats plus FGM/FGA/FTM/FTA; percentages are derived from makes and attempts.
export type Stats = Record<string, number>;

export type Game = { state: "pre" | "live" | "final"; status: string; tip_at: string | null };

export type LineupCell = {
  slot: string | null;
  opponent: string | null;
  counts: boolean;
  actual: boolean; // a box score: a past day's, or today's so far once his game tips
  stats: Stats;
  projected: Stats; // the game's projected final; differs from stats only while it's live
  game: Game | null; // today's game; null on other days
  // The game as one fantasy value: z-scores summed on the single-game scale (TeamLineup.value_scale).
  // Null without a game.
  value: number | null; // of stats
  projected_value: number | null; // of projected
  value_by_cat: Record<string, number> | null;
};

export type LineupDay = {
  day: string;
  played: boolean;
  actual: Stats; // counted box scores (today's so far)
  projected: Stats; // counted projected finals
  starts: number;
  missed_starts: number | null;
  actual_value: number; // counted box scores' values (today's so far)
  projected_value: number; // counted projected finals' values
};

export type TeamLineup = {
  team_id: number;
  matchup_period: number;
  days: LineupDay[];
  // usual: his per-game averages valued the same way, what his games average out to
  players: { player: Player; cells: LineupCell[]; totals: Stats; usual: number | null }[];
  totals: Stats; // the week's projected final
  value_scale: { mean: number; sd: number }; // one game's value over rostered players' games
};

export type CategoryProfile = { key: string; value: number; rank: number; z: number; punt: boolean };

export type TeamPlayer = {
  player: Player;
  slot: string;
  acquisition_type: string | null;
  acquired_at: string | null;
  gp: number | null;
  mpg: number | null;
  per_game: Stats | null; // null: no line in this window
  z: Record<string, number>; // this window, against rostered players
  value: number; // G-score over this window
  specialties: string[];
};

export type Move = {
  transaction_id: string | null; // shared by the moves of one transaction; null for trades
  at: string | null;
  type: "ADD" | "DROP" | "TRADE";
  kind: string;
  player_id: number;
  player_name: string | null;
};

export type TeamDetail = {
  team: Team;
  window: string;
  windows: string[];
  profile: CategoryProfile[];
  punts: string[];
  players: TeamPlayer[];
  moves: Move[];
  notes: string[];
  sources: { label: string; url: string }[];
};

export type StreamRow = {
  add: Player;
  drop: Player | null;
  games: number;
  extra_starts: number;
  p_win: number;
  d_p_win: number;
  d_p_tie: number;
  d_expected_cats: number;
  cat_d_p_win: Record<string, number>;
  gained: string[];
  lost: string[];
};

export type HoldRow = {
  add: Player;
  drop: Player | null;
  games: number;
  d_cats_per_week: number;
  cat_d_p_win: Record<string, number>;
};

export type Waivers = {
  team_id: number;
  opponent_id: number;
  matchup_period: number;
  first_day: string;
  days_left: string[];
  baseline: { p_win: number; p_tie: number; p_loss: number; expected_cats: number; starts_left: number };
  board: { key: string; label: Label; p_win: number; p_tie: number; p_loss: number }[];
  stream: StreamRow[];
  stream_screened: number;
  hold: {
    weeks: number;
    first_period: number | null;
    cats_per_week: number;
    cat_p_win: Record<string, number>;
    rows: HoldRow[];
    screened: number;
  };
  drops: (Player | null)[];
  free_agents: number;
  assumptions: string[];
  n_sims: number;
};

export type TeamProfile = { team: Team; profile: CategoryProfile[]; punts: string[] };

// Every team's category profile over one stat window: the league heatmap.
export type Profiles = { window: string; windows: string[]; teams: TeamProfile[]; notes: string[] };

export type WinLoss = { wins: number; losses: number; ties: number };

export type PowerRow = {
  team: Team;
  rank: number;
  strength: number; // expected share of weekly matchups won against every other team, rest of season
  cats_per_week: number;
  cat_p_win: Record<string, number>;
  all_play: WinLoss; // completed weeks, against every other team
  punts: string[];
  projected: WinLoss; // record so far plus expected results in the regular-season weeks left (fractional)
};

export type Power = { weeks: number; first_period: number; completed_weeks: number; rows: PowerRow[]; notes: string[] };

export type TradeSide = {
  team_id: number;
  receives: Player[];
  sends: Player[];
  drops: Player[]; // dropped to make room
  adds: Player[]; // free agents picked up for the spots the deal opens
  cats_per_week: number; // before the deal, vs a league-average team
  d_cats_per_week: number;
  p_week: number; // P(win a week) vs a league-average team, before the deal
  d_p_week: number;
  cat_p_win: Record<string, number>;
  cat_d_p_win: Record<string, number>;
  cat_d_value: Record<string, number>; // change in the expected weekly total; percentages: in the percentage
};

export type TradeIdea = { me: TradeSide; partner: TradeSide };

export type Trade = TradeIdea & { weeks: number; first_period: number; warnings: string[]; assumptions: string[] };

export type Trades = {
  team_id: number;
  deals: TradeIdea[]; // both sides gain; best for me first
  candidates: number;
  evaluated: number;
  weeks: number;
  first_period: number;
  assumptions: string[];
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

type Param = string | number | boolean | undefined;

async function get<T>(path: string, params: Record<string, Param | number[]> = {}, init?: RequestInit): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) for (const x of v) query.append(k, String(x));
    else if (v !== undefined) query.set(k, String(v));
  }
  const res = await fetch(`/api${path}${query.size ? `?${query}` : ""}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const useLeague = () => useQuery({ queryKey: ["league"], queryFn: () => get<League>("/league") });

// `period` picks an earlier week; undefined is the current one.
export const useMatchups = (period?: number, enabled = true) =>
  useQuery({
    queryKey: ["matchups", period],
    queryFn: () => get<MatchupSummary[]>("/matchups", { period }),
    enabled,
    placeholderData: keepPreviousData,
  });

export const useMatchup = (teamId: number | undefined, period?: number) =>
  useQuery({
    queryKey: ["matchup", teamId, period],
    queryFn: () => get<MatchupDetail>("/matchup", { team_id: teamId, period }),
    enabled: teamId !== undefined,
    placeholderData: keepPreviousData,
  });

export const useLineup = (teamId: number | undefined, period?: number) =>
  useQuery({
    queryKey: ["lineup", teamId, period],
    queryFn: () => get<TeamLineup>("/lineup", { team_id: teamId, period }),
    enabled: teamId !== undefined,
    placeholderData: keepPreviousData,
  });

// Runs the ingest on the backend, then refetches everything from the new snapshot.
export const useRefresh = () => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => get<IngestRun>("/refresh", {}, { method: "POST" }),
    onSuccess: () => client.invalidateQueries(),
  });
};

export const useTeam = (teamId: number | undefined, window: string) =>
  useQuery({
    queryKey: ["team", teamId, window],
    queryFn: () => get<TeamDetail>("/team", { team_id: teamId, window }),
    enabled: teamId !== undefined,
    placeholderData: keepPreviousData,
  });

export const useWaivers = (teamId: number | undefined, sameDay: boolean) =>
  useQuery({
    queryKey: ["waivers", teamId, sameDay],
    queryFn: () => get<Waivers>("/waivers", { team_id: teamId, same_day: sameDay }),
    enabled: teamId !== undefined,
    placeholderData: keepPreviousData,
  });

export const useProfiles = (window: string) =>
  useQuery({
    queryKey: ["profiles", window],
    queryFn: () => get<Profiles>("/profiles", { window }),
    placeholderData: keepPreviousData,
  });

export const usePower = () => useQuery({ queryKey: ["power"], queryFn: () => get<Power>("/power") });

// A deal from my side: `give` are my players, `receive` the partner's.
export const useTrade = (teamId: number | undefined, partnerId: number | undefined, give: number[], receive: number[]) =>
  useQuery({
    queryKey: ["trade", teamId, partnerId, give, receive],
    queryFn: () => get<Trade>("/trade", { team_id: teamId, partner_id: partnerId, give, get: receive }),
    enabled: teamId !== undefined && partnerId !== undefined && give.length > 0 && receive.length > 0,
    placeholderData: keepPreviousData,
  });

export const useTrades = (teamId: number | undefined) =>
  useQuery({
    queryKey: ["trades", teamId],
    queryFn: () => get<Trades>("/trades", { team_id: teamId }),
    enabled: teamId !== undefined,
    placeholderData: keepPreviousData,
  });
