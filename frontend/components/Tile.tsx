export function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {note && <div className="sub">{note}</div>}
    </div>
  );
}
