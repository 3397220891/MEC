"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import "./login.css";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState("");

  function enterMission(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const callsign = String(new FormData(event.currentTarget).get("callsign") ?? "").trim();
    if (!callsign) {
      setError("Enter an operator callsign.");
      return;
    }
    try {
      sessionStorage.setItem("orbitalguard.demoCallsign", callsign);
    } catch {
      setError("Enable browser session storage to open the demo.");
      return;
    }
    router.push("/mission");
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
        <p className="access-intro">Enter your callsign to open the demo console.</p>
        <form className="access-form" onSubmit={enterMission}>
          <label htmlFor="callsign">Operator callsign</label>
          <input id="callsign" name="callsign" autoComplete="off" autoCapitalize="characters" spellCheck={false}
            maxLength={24} required placeholder="e.g. KESTREL" aria-describedby={error ? "access-error" : undefined}
            aria-invalid={Boolean(error)} onChange={() => setError("")} />
          {error && <p id="access-error" role="alert">{error}</p>}
          <button type="submit">Enter mission control <span aria-hidden="true">&#x2197;&#xFE0E;</span></button>
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
