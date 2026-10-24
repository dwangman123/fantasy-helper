import { ApiError } from "@/lib/api";

export function Loading({ what }: { what: string }) {
  return <p className="sub">Loading {what}…</p>;
}

export function ErrorNotice({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  const hint =
    error instanceof ApiError && error.status === 404
      ? "Run `uv run fh-ingest` in backend/ to load the league."
      : "Is the API running? Start it with `uv run fh-api` in backend/.";
  return (
    <div className="notice error">
      <strong>{message}</strong>
      <div className="sub">{hint}</div>
    </div>
  );
}
