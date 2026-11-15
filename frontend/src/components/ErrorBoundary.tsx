"use client";

import { Component, type ReactNode } from "react";
import { toast } from "@/lib/toast";

/** Contain a crash to one region (e.g. the WebGL globe) instead of blanking the whole dashboard. */
export default class ErrorBoundary extends Component<
  { name: string; children: ReactNode; fallback?: (error: Error, reset: () => void) => ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error(`[OrbitalGuard] ${this.props.name} crashed`, error);
    toast("error", `${this.props.name} stopped working`, `${error.message}. The rest of the dashboard still works.`);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return (
      <div role="alert" className="grid h-full place-items-center p-6 text-center text-sm">
        <div className="max-w-sm rounded-xl border border-red/50 bg-[#2a0f14]/90 p-4">
          <p className="font-semibold text-red">⛔ {this.props.name} failed to render</p>
          <p className="mt-1 text-xs text-ink-2">{error.message}</p>
          <p className="mt-1 text-xs text-muted">Alerts, the assistant and the chat keep working.</p>
          <button type="button" onClick={this.reset} className="mt-3 rounded-md border border-line px-3 py-1 text-xs text-ink hover:border-accent/60">
            ↻ Reload {this.props.name}
          </button>
        </div>
      </div>
    );
  }
}
