export const pct = (p: number) => `${Math.round(p * 100)}%`;

// A change in probability, in percentage points: "+4.1 pts".
export const pts = (d: number) => `${d >= 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(1)} pts`;

export const signed = (x: number, digits = 2) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;

// A category total: FG% as .472, counting stats as whole numbers.
export const stat = (value: number | null | undefined, isPercentage: boolean) => {
  if (value === null || value === undefined) return "–";
  return isPercentage ? value.toFixed(3).replace(/^0/, "") : Math.round(value).toLocaleString();
};

// Dates come from the API as YYYY-MM-DD; parse as local days, not UTC midnight.
const day = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};

export const shortDate = (iso: string) => day(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const weekday = (iso: string) =>
  day(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
