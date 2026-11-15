import type { ChatReply, ChatTurn, Investigation, ManeuverPlan, MissionState, Scenario, Separation, Trajectories } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
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
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError") throw new ApiError(0, "Backend request timed out");
    throw new ApiError(0, "Backend unreachable. Is the FastAPI server running on :8000?");
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
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
