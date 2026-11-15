/** Tiny global toast bus: call toast() from anywhere, <Toasts /> renders them. */
export type ToastKind = "error" | "warn" | "info" | "ok";
export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
}

export const TOAST_EVENT = "orbitalguard:toast";
let next = 1;

export function toast(kind: ToastKind, title: string, detail?: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<ToastItem>(TOAST_EVENT, { detail: { id: next++, kind, title, detail } }));
}

/** Human-readable message from anything thrown. */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
