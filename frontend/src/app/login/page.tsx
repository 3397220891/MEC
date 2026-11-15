"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import "./login.css";

export default function LoginPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function enterMission(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    setError("");
    setBusy(true);
    try {
      const result = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: String(fields.get("name") ?? "").trim() }),
      });
      if (!result.ok) {
        setError(result.status === 401 ? "This name is outside the demo operator roster." : "Sign-in is unavailable. Try again.");
        return;
      }
      router.replace("/mission");
      router.refresh();
    } catch {
      setError("Sign-in is unavailable. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="access-page">
      <div className="access-visual">
        <Image src="/earth-through-window.jpg" alt="Earth seen through a spacecraft window" fill priority sizes="(max-width: 720px) 100vw, 77vw" />
      </div>
      <div className="access-shade" aria-hidden="true" />
      <header className="access-brand"><span aria-hidden="true">◎</span> OrbitalGuard AI</header>
      <section className="access-content" aria-labelledby="access-title">
        <h1 id="access-title">
          <span className="access-title-line">Mission</span>{" "}
          <span className="access-title-line">control</span><span className="access-cursor" aria-hidden="true" />
        </h1>
        <p className="access-intro">Enter your full name to join the demo.</p>
        <form className="access-form" onSubmit={enterMission}>
          <label htmlFor="name">Operator name</label>
          <input id="name" name="name" autoComplete="name" spellCheck={false}
            maxLength={80} required aria-describedby={error ? "access-error" : undefined} onChange={() => setError("")} />
          {error && <p id="access-error" role="alert">{error}</p>}
          <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Enter mission control"} <span aria-hidden="true">&#x2197;&#xFE0E;</span></button>
        </form>
        <section className="access-terminal" aria-label="Simulated startup log">
          <p>[SYSTEM BOOT] OrbitalGuard AI</p>
          <p>Loading demo orbit catalog... [OK]</p>
          <p>Preparing conjunction monitor... [OK]</p>
          <p>Mission console ready<span className="access-terminal-cursor" aria-hidden="true" /></p>
        </section>
      </section>
    </main>
  );
}
