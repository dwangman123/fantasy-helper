"use client";

import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/Markdown";
import { ApiError } from "@/lib/api";
import { type AgentEvent, type ChatMessage, MAX_QUESTION_CHARS, askAgent } from "@/lib/agent";

// One step the assistant took: a tool it ran, as it runs.
type Step = { id: string; name: string; state: "running" | "done" | "failed" };

type Turn = {
  role: "user" | "assistant";
  content: string;
  steps?: Step[];
  notice?: string; // the answer was cut short
  error?: string; // the turn couldn't finish
  pending?: boolean;
};

// What each tool does, as the assistant runs it and after.
const TOOL_LABELS: Record<string, [string, string]> = {
  get_matchup: ["Checking the matchup", "Checked the matchup"],
  get_team: ["Looking at a team", "Looked at a team"],
  get_category_profiles: ["Comparing every team's categories", "Compared every team's categories"],
  get_power_rankings: ["Checking the power rankings", "Checked the power rankings"],
  rank_waivers: ["Ranking free agents", "Ranked free agents"],
  player_lookup: ["Looking up players", "Looked up players"],
  evaluate_trade: ["Scoring the trade", "Scored the trade"],
  find_trades: ["Searching for trades", "Searched for trades"],
};

const STARTERS = [
  "How's my matchup looking this week?",
  "Who should I pick up to win steals this week?",
  "What are my team's punt categories?",
  "Who are the top 3 teams in the power rankings?",
];

// The conversation lives in this tab only: a reload keeps it, closing the tab clears it.
const STORAGE_KEY = "fh-chat";

function load(): Turn[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(saved) ? saved.filter((t: Turn) => !t.pending) : [];
  } catch {
    return [];
  }
}

function save(turns: Turn[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(turns.filter((t) => !t.pending)));
  } catch {
    // storage full or blocked: the chat still works, it just won't survive a reload
  }
}

// What the model sees of earlier turns: their text, without failed or empty answers.
function history(turns: Turn[]): ChatMessage[] {
  return turns.filter((t) => t.content.trim() && !(t.role === "assistant" && t.error)).map(({ role, content }) => ({ role, content }));
}

export default function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false); // read from storage after the first render, never during
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTurns(load());
    setLoaded(true);
  }, []);
  useEffect(() => {
    if (loaded && !busy) save(turns);
    if (turns.length) end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns, busy, loaded]);
  useEffect(() => () => abort.current?.abort(), []);

  // Changes the answer being written, the last turn; nothing if "New chat" cleared it.
  const update = (change: (t: Turn) => Turn) =>
    setTurns((ts) => {
      const last = ts[ts.length - 1];
      return last?.role === "assistant" ? [...ts.slice(0, -1), change(last)] : ts;
    });

  function onEvent(e: AgentEvent) {
    if (e.type === "text") update((t) => ({ ...t, content: t.content + e.delta }));
    else if (e.type === "tool_call")
      update((t) => ({ ...t, steps: [...(t.steps ?? []), { id: e.id, name: e.name, state: "running" }] }));
    else if (e.type === "tool_result")
      update((t) => ({
        ...t,
        steps: (t.steps ?? []).map((s) => (s.id === e.id ? { ...s, state: e.ok ? "done" : "failed" } : s)),
      }));
    else if (e.type === "notice") update((t) => ({ ...t, notice: e.message }));
    else if (e.type === "error") update((t) => ({ ...t, error: e.message }));
  }

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    const asked: Turn = { role: "user", content: question };
    const messages = [...history(turns), { role: "user" as const, content: question }];
    setTurns((ts) => [...ts, asked, { role: "assistant", content: "", pending: true }]);
    setDraft("");
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      await askAgent(messages, onEvent, controller.signal);
    } catch (err) {
      const message =
        err instanceof DOMException && err.name === "AbortError"
          ? "Stopped."
          : err instanceof ApiError
            ? err.message
            : "The chat couldn't be reached. Please try again.";
      update((t) => ({ ...t, error: message }));
    } finally {
      update((t) => ({ ...t, pending: false }));
      setBusy(false);
      abort.current = null;
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void ask(draft);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void ask(draft);
    }
  }

  function startOver() {
    abort.current?.abort();
    setTurns([]);
    save([]);
  }

  return (
    <div className="stack chat">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>Chat</h1>
          <div className="sub">
            Ask about matchups, pickups, teams and trades. Every number comes from the app&apos;s own analytics, as on
            the other pages. &ldquo;My team&rdquo; means the site owner&apos;s; name a team by its owner to ask about
            another.
          </div>
        </div>
        {turns.length > 0 && (
          <button type="button" onClick={startOver}>
            New chat
          </button>
        )}
      </div>

      <div className="chat-log" role="log" aria-label="Conversation">
        {turns.length === 0 && (
          <div className="chat-starters">
            {STARTERS.map((q) => (
              <button key={q} type="button" onClick={() => void ask(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) => (t.role === "user" ? <Question key={i} text={t.content} /> : <Answer key={i} turn={t} />))}
        <div ref={end} />
      </div>

      <form className="chat-form" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask a question about the league…"
          aria-label="Your question"
          maxLength={MAX_QUESTION_CHARS}
          rows={2}
          disabled={busy}
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" className="primary" disabled={!draft.trim()}>
            Ask
          </button>
        )}
      </form>
      <div className="sub">
        Conversations stay in this browser tab and aren&apos;t stored. Answers can be wrong; the other pages show the
        full numbers.
      </div>
    </div>
  );
}

function Question({ text }: { text: string }) {
  return <div className="chat-msg question">{text}</div>;
}

function Answer({ turn }: { turn: Turn }) {
  const thinking = turn.pending && !turn.content && !(turn.steps ?? []).some((s) => s.state === "running");
  return (
    <div className="chat-msg answer" aria-busy={turn.pending}>
      {turn.steps && turn.steps.length > 0 && (
        <ul className="chat-steps">
          {turn.steps.map((s) => {
            const [running, done] = TOOL_LABELS[s.name] ?? [`Running ${s.name}`, `Ran ${s.name}`];
            return (
              <li key={s.id} className={s.state}>
                <span aria-hidden className={s.state === "running" ? "spin" : undefined}>
                  {s.state === "running" ? "↻" : s.state === "done" ? "✓" : "✗"}
                </span>
                {s.state === "running" ? `${running}…` : done}
              </li>
            );
          })}
        </ul>
      )}
      {turn.content && (
        <div className="chat-text">
          <Markdown text={turn.content} />
        </div>
      )}
      {thinking && <div className="sub">Thinking…</div>}
      {turn.notice && <div className="notice">{turn.notice}</div>}
      {turn.error && <div className="notice error">{turn.error}</div>}
    </div>
  );
}
