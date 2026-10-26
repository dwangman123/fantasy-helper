"use client";

import { useRefresh } from "@/lib/api";

// Once games start, ESPN's live totals include today's games, which the projections also
// count. The ingest flags such a run in its warnings, and the banner under the top bar shows them.
export function RefreshButton() {
  const refresh = useRefresh();
  return (
    <>
      <button
        type="button"
        className="refresh"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Pull the latest league data from ESPN. Best before the day's first game: once games start, today's games count twice in the projections."
      >
        <span aria-hidden className={refresh.isPending ? "spin" : undefined}>
          ↻
        </span>
        {refresh.isPending ? "Refreshing…" : "Refresh"}
      </button>
      {refresh.isError && (
        <span className="error-text" role="alert">
          {refresh.error.message}
        </span>
      )}
    </>
  );
}
