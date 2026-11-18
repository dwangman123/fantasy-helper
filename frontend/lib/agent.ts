// The chat: POST /api/agent streams one turn as server-sent events (backend agent/loop.py and
// agent/routes.py). Not a TanStack Query hook: a turn is read as it arrives, not cached.
import { ApiError } from "@/lib/api";

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type Usage = {
  model: string;
  status: string; // ok / incomplete / timeout / error / closed
  steps: number;
  tools: string[];
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  known: boolean;
  cost_usd: number | null;
  seconds: number;
};

export type AgentEvent =
  | { type: "start" }
  | { type: "tool_call"; id: string; name: string; arguments: unknown }
  | { type: "tool_result"; id: string; name: string; ok: boolean; error?: string }
  | { type: "text"; delta: string }
  | { type: "notice"; message: string }
  | { type: "error"; message: string }
  | ({ type: "usage" } & Usage)
  | { type: "done" };

// Mirror backend agent/routes.py: MAX_QUESTION_CHARS, MAX_ANSWER_CHARS and RECENT_MESSAGES. The
// server refuses a longer question or answer, and only reads the latest messages anyway.
export const MAX_QUESTION_CHARS = 2000;
export const MAX_ANSWER_CHARS = 8000;
export const RECENT_MESSAGES = 20;

// What to send of a conversation: its latest messages, starting with a question, earlier answers
// cut to the length the server takes. Sending it all would get a long chat refused.
export function recent(messages: ChatMessage[]): ChatMessage[] {
  const kept = messages.slice(-RECENT_MESSAGES);
  while (kept.length > 1 && kept[0].role !== "user") kept.shift();
  return kept.map((m) => (m.role === "assistant" ? { ...m, content: m.content.slice(0, MAX_ANSWER_CHARS) } : m));
}

// Sends the conversation, the last message a question, and calls `onEvent` for each event as it
// arrives. Throws ApiError when the backend refuses before streaming (429 over the spending cap,
// 503 not set up, 400 / 422 a bad conversation), and an AbortError if `signal` stops it.
export async function askAgent(
  messages: ChatMessage[],
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/agent", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages: recent(messages) }),
    signal,
  });
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null);
    // 422s carry a list of field errors; the others a sentence.
    const detail = typeof body?.detail === "string" ? body.detail : "The chat couldn't read that message.";
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    // Events are "data: {json}" lines, each followed by a blank line.
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as AgentEvent);
      }
    }
  }
}
