import type { Label } from "@/lib/api";

const DOT: Record<Label, string> = { locked: "var(--win)", swing: "var(--muted)", lost: "var(--loss)" };

export function LabelChip({ label }: { label: Label }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: DOT[label] }} />
      {label}
    </span>
  );
}
