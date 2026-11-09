"use client";

import { useRefresh } from "@/lib/api";

// Runs the ingest now. During games it's live: totals so far include today's lines, and only
// the rest of each game is projected. The run's warnings show in the banner under the top bar.
export function RefreshButton() {
  const refresh = useRefresh();
  return (
    <>
      <button
        type="button"
        className="refresh"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Pull the latest league data from ESPN. During games, totals include today's lines so far and only the rest of each game is projected."
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
