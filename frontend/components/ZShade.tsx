// Shading by z-score: how far a player or team is from the average in a category.
// Blue above, red below, unshaded within half a standard deviation.
export function zClass(z: number | undefined): string {
  if (z === undefined) return "";
  if (z >= 1) return "z-hi2";
  if (z >= 0.5) return "z-hi1";
  if (z <= -1) return "z-lo2";
  if (z <= -0.5) return "z-lo1";
  return "";
}

export function ZLegend() {
  return (
    <div className="legend">
      <span>
        <span className="key z-hi2" />
        well above average
      </span>
      <span>
        <span className="key z-hi1" />
        above
      </span>
      <span>
        <span className="key z-lo1" />
        below
      </span>
      <span>
        <span className="key z-lo2" />
        well below
      </span>
    </div>
  );
}
