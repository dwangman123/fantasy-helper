"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useLeague } from "@/lib/api";
import { dateTime } from "@/lib/format";

const LINKS = [
  { href: "/matchup", label: "Matchup" },
  { href: "/waivers", label: "Waivers" },
];

export function TopBar() {
  const path = usePathname();
  const { data: league } = useLeague();
  const run = league?.last_ingest;
  return (
    <>
      <header className="topbar">
        <Link href="/matchup" className="brand">
          Fantasy Helper
        </Link>
        <nav className="nav">
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} aria-current={path.startsWith(l.href) ? "page" : undefined}>
              {l.label}
            </Link>
          ))}
        </nav>
        {league && (
          <div className="topbar-meta">
            {league.name} · {run?.finished_at ? `data from ${dateTime(run.finished_at)}` : "no ingest run recorded"}
          </div>
        )}
      </header>
      {run && (run.status !== "success" || run.warnings.length > 0) && (
        <div className="notice" style={{ marginBottom: 24 }}>
          <strong>Last ingest {run.status === "success" ? "had warnings" : run.status}.</strong>
          <ul className="assumptions">
            {run.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
