"use client";

import { useEffect, useState } from "react";
import { TOAST_EVENT, type ToastItem } from "@/lib/toast";

const STYLE: Record<ToastItem["kind"], { box: string; icon: string }> = {
  error: { box: "border-red/60 bg-[#2a0f14]/95 text-red", icon: "⛔" },
  warn: { box: "border-amber/60 bg-[#2a1e08]/95 text-amber", icon: "⚠" },
  info: { box: "border-watch/50 bg-[#0b2033]/95 text-watch", icon: "ℹ" },
  ok: { box: "border-ok/50 bg-[#0b2a1c]/95 text-ok", icon: "✓" },
};

/** Bottom-right toast stack. Errors stay longer; every toast can be dismissed. */
export default function Toasts() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => {
    const onToast = (e: Event) => {
      const t = (e as CustomEvent<ToastItem>).detail;
      setItems((prev) => [...prev.filter((p) => p.title !== t.title), t].slice(-4)); // de-duplicate repeats
      window.setTimeout(() => setItems((prev) => prev.filter((p) => p.id !== t.id)), t.kind === "error" ? 12000 : 7000);
    };
    window.addEventListener(TOAST_EVENT, onToast);
    return () => window.removeEventListener(TOAST_EVENT, onToast);
  }, []);
  return (
    <div aria-live="assertive" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(380px,calc(100vw-32px))] flex-col gap-2">
      {items.map((t) => (
        <div key={t.id} role={t.kind === "error" ? "alert" : "status"}
          className={`pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-lg backdrop-blur ${STYLE[t.kind].box}`}>
          <span aria-hidden="true">{STYLE[t.kind].icon}</span>
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{t.title}</p>
            {t.detail && <p className="mt-0.5 break-words text-ink-2">{t.detail}</p>}
          </div>
          <button type="button" aria-label="Dismiss" onClick={() => setItems((prev) => prev.filter((p) => p.id !== t.id))}
            className="text-ink-2 hover:text-ink">×</button>
        </div>
      ))}
    </div>
  );
}
