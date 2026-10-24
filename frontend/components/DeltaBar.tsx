type Props = { value: number; max: number; text: string };

// A signed change as a bar from a center zero line: right and blue when it helps me,
// left and red when it hurts. `max` is the largest |value| in the column.
export function DeltaBar({ value, max, text }: Props) {
  const share = max > 0 ? Math.min(Math.abs(value) / max, 1) * 50 : 0;
  const positive = value >= 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, justifyContent: "flex-end" }}>
      <span style={{ minWidth: 72, textAlign: "right" }}>{text}</span>
      <div className="hide-sm" style={{ position: "relative", width: 96, height: 10, flex: "none" }} aria-hidden>
        <span style={{ position: "absolute", left: "50%", top: -3, bottom: -3, width: 1, background: "var(--axis)" }} />
        <span
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            [positive ? "left" : "right"]: "calc(50% + 1px)",
            width: `${share}%`,
            background: positive ? "var(--win)" : "var(--loss)",
            borderRadius: positive ? "0 4px 4px 0" : "4px 0 0 4px",
          }}
        />
      </div>
    </div>
  );
}
