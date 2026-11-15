import type { ChatReply, ChatStreamEvent, ChatTurn, Health, Investigation, ManeuverPlan, MissionState, Scenario, Separation, Trajectories } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const BACKEND_DOWN = "Backend unreachable: the API server is not running or not reachable from the web server (check the uvicorn terminal).";

/** Turn an error response into a readable ApiError (FastAPI {detail}, our 500 handler, or a proxy failure). */
async function errorFrom(res: Response): Promise<ApiError> {
  let detail: string | null = null;
  try {
    const body = await res.json();
    detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* non-JSON body: the Next.js proxy could not reach FastAPI */
  }
  if (detail === null) {
    return new ApiError(0, res.status >= 500 ? BACKEND_DOWN : `${res.status} ${res.statusText}`);
  }
  return new ApiError(res.status, detail);
}

async function req<T>(path: string, init?: RequestInit, timeoutMs = 20000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`/api${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
    if (!res.ok) throw await errorFrom(res);
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError") throw new ApiError(0, `Backend did not answer within ${Math.round(timeoutMs / 1000)} s`);
    throw new ApiError(0, BACKEND_DOWN);
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  health: (probe = false) => req<Health>(`/health${probe ? "?probe=true" : ""}`, undefined, 12000),
  scenarios: () => req<Scenario[]>("/scenarios"),
  loadScenario: (id: string) =>
    req<{ scenario_id: string; investigations_started: string[] }>(`/scenarios/${id}/load`, { method: "POST" }, 60000),
  state: () => req<MissionState>("/state"),
  trajectories: (step = 60) => req<Trajectories>(`/trajectories?step_s=${step}`, undefined, 30000),
  investigation: (id: string) => req<Investigation>(`/investigations/${id}`),
  startInvestigation: (eventId: string) =>
    req<{ investigation_id: string }>("/investigations", { method: "POST", body: JSON.stringify({ event_id: eventId }) }),
  decide: (id: string, decision: "approve" | "reject", operator: string, note: string) =>
    req(`/investigations/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, operator, note }) }),
  /** Simulated maneuver options (burn points at T−N) and the maneuvered path of the recommended/selected one. */
  plan: (invId: string, maneuverId?: string) =>
    req<ManeuverPlan>(`/investigations/${invId}/plan${maneuverId ? `?maneuver_id=${encodeURIComponent(maneuverId)}` : ""}`, undefined, 30000),
  /** Ask a Sentinel agent (eventId) or the Mission Director (eventId = null). The server adds the context. */
  chat: (question: string, eventId: string | null, history: ChatTurn[]) =>
    req<ChatReply>("/chat", { method: "POST", body: JSON.stringify({ question, event_id: eventId, history }) }, 45000),
  separation: (eventId: string, invId?: string, maneuverId?: string) => {
    const q = new URLSearchParams({ half_window_s: "60" });
    if (invId && maneuverId) {
      q.set("investigation_id", invId);
      q.set("maneuver_id", maneuverId);
    }
    return req<Separation>(`/events/${eventId}/separation?${q}`);
  },
};

/**
 * Stream an agent chat answer: reasoning, tool calls and their results, then the answer text, as they happen.
 * Calls onEvent for every Server-Sent Event; resolves when the stream ends.
 */
export async function chatStream(question: string, eventId: string | null, history: ChatTurn[],
  onEvent: (e: ChatStreamEvent) => void, idleTimeoutMs = 60000): Promise<void> {
  // Abort if nothing arrives for idleTimeoutMs (stuck model or network), so the UI never spins forever.
  const ctrl = new AbortController();
  let idle = setTimeout(() => ctrl.abort(), idleTimeoutMs);
  const touch = () => { clearTimeout(idle); idle = setTimeout(() => ctrl.abort(), idleTimeoutMs); };
  let gotFinal = false;
  try {
    let res: Response;
    try {
      res = await fetch("/api/chat/stream", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ctrl.signal,
        body: JSON.stringify({ question, event_id: eventId, history }),
      });
    } catch {
      throw new ApiError(0, ctrl.signal.aborted ? `No response from the agent within ${idleTimeoutMs / 1000} s` : BACKEND_DOWN);
    }
    if (!res.ok || !res.body) throw await errorFrom(res);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch {
        throw new ApiError(0, ctrl.signal.aborted
          ? `The agent stopped responding (no data for ${idleTimeoutMs / 1000} s); the answer is incomplete`
          : "Connection to the backend dropped mid-answer; the answer is incomplete");
      }
      if (chunk.done) break;
      touch();
      buf += decoder.decode(chunk.value, { stream: true });
      let cut: number;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        for (const line of block.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const e = JSON.parse(line.slice(6)) as ChatStreamEvent;
          if (e.type === "final") gotFinal = true;
          onEvent(e);
        }
      }
    }
    if (!gotFinal) throw new ApiError(0, "The answer stream ended unexpectedly; the answer may be incomplete");
  } finally {
    clearTimeout(idle);
  }
}
