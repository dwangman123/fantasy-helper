import { Fragment, type ReactNode } from "react";

// The little Markdown the chat's model writes: paragraphs, "-" / "*" and "1." lists, **bold**,
// *italic* and `code`. Everything becomes React elements, so nothing the model writes is ever
// parsed as HTML.
export function Markdown({ text }: { text: string }) {
  return <>{blocks(text)}</>;
}

type Block = { kind: "p" | "ul" | "ol"; lines: string[] };

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

function blocks(text: string): ReactNode[] {
  const out: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const kind = BULLET.test(line) ? "ul" : NUMBERED.test(line) ? "ol" : line.trim() ? "p" : null;
    const last = out[out.length - 1];
    if (kind === null) {
      out.push({ kind: "p", lines: [] }); // a blank line ends the block
    } else if (last && last.kind === kind && last.lines.length > 0) {
      last.lines.push(line);
    } else {
      out.push({ kind, lines: [line] });
    }
  }
  return out
    .filter((b) => b.lines.length > 0)
    .map((b, i) => {
      if (b.kind === "p") return <p key={i}>{inline(b.lines.join(" "))}</p>;
      const items = b.lines.map((l, j) => <li key={j}>{inline(l.replace(b.kind === "ul" ? BULLET : NUMBERED, ""))}</li>);
      return b.kind === "ul" ? <ul key={i}>{items}</ul> : <ol key={i}>{items}</ol>;
    });
}

// **bold**, *italic* and `code`; anything unmatched stays as typed.
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;

function inline(text: string): ReactNode {
  return text.split(INLINE).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <em key={i}>{part.slice(1, -1)}</em>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}
