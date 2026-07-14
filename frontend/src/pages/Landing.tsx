/**
 * Public marketing landing (route `/`, no auth). Story #11.
 *
 * Copy sourced from docs/pricing-copy.md (Cycle J DRAFT). [VOICE-CHECK]
 * markers from that file are preserved here as TODO comments — Allen owns the
 * brand pass; this dispatch must NOT resolve them (D4 hard constraint).
 * Pricing rows: DR-003 (LOCKED 2026-05-12) — Free / Solo $50 / Team $250
 * (3 seats) / Enterprise $1,000. Soft caps + retention windows are Cycle J
 * [VOICE-CHECK] guesses pending Allen ratify (D4_specs.md §2 #2/#3 defaults).
 *
 * Plain Tailwind, no animation, no marketing-fluff per Cycle J voice notes
 * (docs/pricing-copy.md:8-14).
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SignedIn, SignedOut } from "@clerk/clerk-react";
import type { DemoResult, TierId } from "../lib/types";
import { getAnonId, track } from "../lib/track";
import { runDemoConversion } from "../lib/demo";

interface Tier {
  id: TierId;
  name: string;
  price: string;
  cadence: string;
  seats: string;
  episodes: string;
  directions: string;
  cap: string;
  retention: string;
  highlight?: boolean;
}

// DR-003 prices LOCKED. [VOICE-CHECK] caps (5/50/200) + retention (24h/30d/
// 30d/90d) are docs/pricing-copy.md:56,58 author guesses — Allen ratifies
// the real ladder (D4_specs.md §2 #2/#3) before launch; do not resolve here.
const TIERS: Tier[] = [
  {
    id: "free",
    name: "Free",
    price: "$0",
    cadence: "",
    seats: "1 seat",
    episodes: "1 episode (preview only)",
    directions: "AgiBot → LeRobot v3 only",
    cap: "5 jobs / mo",
    retention: "24h retention",
  },
  {
    id: "solo",
    name: "Solo",
    price: "$50",
    cadence: "/mo",
    seats: "1 seat",
    episodes: "Full dataset",
    directions: "Both directions",
    cap: "50 jobs / mo",
    retention: "30d retention",
    highlight: true,
  },
  {
    id: "team",
    name: "Team",
    price: "$250",
    cadence: "/mo",
    seats: "3 seats included",
    episodes: "Full dataset",
    directions: "Both directions",
    cap: "200 jobs / mo",
    retention: "30d retention",
  },
  {
    id: "enterprise",
    name: "Enterprise",
    price: "$1,000",
    cadence: "/mo",
    seats: "Unlimited seats",
    episodes: "Full dataset",
    directions: "Both directions",
    cap: "No soft cap",
    retention: "90d retention",
  },
];

function Header() {
  return (
    <header className="flex items-center justify-between px-6 py-4">
      <span className="text-lg font-semibold tracking-tight">agibridge</span>
      <nav className="flex items-center gap-4 text-sm">
        <a href="#pricing" className="hover:underline">
          Pricing
        </a>
        <SignedOut>
          {/* Clerk hosted sign-in/up routes (Story #2). */}
          <Link
            to="/sign-in"
            className="rounded-md px-3 py-1.5 hover:bg-stone-100 dark:hover:bg-stone-900"
          >
            Sign in
          </Link>
          <Link
            to="/sign-up"
            onClick={() => track("signup_started", { cta: "header" })}
            className="rounded-md bg-indigo-600 px-3 py-1.5 font-medium text-white hover:bg-indigo-500"
          >
            {/* [VOICE-CHECK] CTA copy: docs/pricing-copy.md:129 recommends
                "Start free preview". Allen owns final CTA (marker #10). */}
            Sign up free
          </Link>
        </SignedOut>
        <SignedIn>
          <Link
            to="/dashboard"
            className="rounded-md bg-indigo-600 px-3 py-1.5 font-medium text-white hover:bg-indigo-500"
          >
            Dashboard
          </Link>
        </SignedIn>
      </nav>
    </header>
  );
}

function Hero() {
  return (
    <section className="mx-auto max-w-3xl px-6 py-20 text-center">
      {/* [VOICE-CHECK] Headline is the literal option from
          docs/pricing-copy.md:23 (marker #1). Allen may A/B the provocative
          alternates — do not resolve here. */}
      <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
        Convert AgiBot World and LeRobot v3 datasets in your browser.
      </h1>
      {/* [VOICE-CHECK] Sub-head references Pi/AgiBot script-pin pain
          (docs/pricing-copy.md:32, marker #2). May soften vs future AgiBot
          outreach (DR-015) — Allen brand pass. */}
      <p className="mx-auto mt-6 max-w-2xl text-lg text-stone-600 dark:text-stone-400">
        Drop in an AgiBot World tarball, get a LeRobot v3 dataset back. Or the
        other direction. Auth, storage, and verified output included — no{" "}
        <code className="rounded bg-stone-200 px-1 dark:bg-stone-800">
          pip install
        </code>
        , no version pinning, no script-pin debugging.
      </p>
      <div className="mt-10 flex items-center justify-center gap-4">
        <SignedOut>
          <Link
            to="/sign-up"
            onClick={() => track("signup_started", { cta: "hero" })}
            className="rounded-md bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            Sign up free
          </Link>
        </SignedOut>
        <SignedIn>
          <Link
            to="/dashboard"
            className="rounded-md bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500"
          >
            Go to dashboard
          </Link>
        </SignedIn>
        <a
          href="#pricing"
          className="rounded-md border border-stone-300 px-5 py-2.5 text-sm font-medium hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-900"
        >
          View pricing
        </a>
      </div>
    </section>
  );
}

/**
 * "Try it, no signup" — the funnel's biggest friction remover. One click
 * runs a REAL AgiBot World -> LeRobot v3 conversion of a bundled sample
 * through the same verified converter the paid flow uses, then shows the
 * real 5-check validator result + the real output dataset shape. No
 * account, no upload (the input is a fixed server-side sample — the
 * endpoint takes nothing; see app/api/demo.py).
 *
 * `demo_started` is client INTENT via the existing track() helper;
 * `demo_run` is emitted SERVER-side by the endpoint (client cannot forge a
 * conversion — same boundary as signup_started vs signup_completed). All
 * states (running / busy / error / done) render readable copy — Landing
 * never shows a raw 500 or blank.
 */
type DemoPhase = "idle" | "running" | "done";

function CheckRow({
  name,
  status,
  detail,
}: {
  name: string | null;
  status: string | null;
  detail: string | null;
}) {
  const ok = status === "PASS";
  const skip = status === "SKIP";
  const tone = ok
    ? "text-emerald-600 dark:text-emerald-400"
    : skip
      ? "text-stone-400"
      : "text-amber-600 dark:text-amber-400";
  return (
    <li className="flex items-baseline gap-2">
      <span className={`font-mono text-xs ${tone}`}>{status ?? "?"}</span>
      <span className="text-sm">{name ?? "check"}</span>
      {detail && (
        <span className="truncate text-xs text-stone-500">— {detail}</span>
      )}
    </li>
  );
}

function TryDemo() {
  const [phase, setPhase] = useState<DemoPhase>("idle");
  const [result, setResult] = useState<DemoResult | null>(null);
  const inFlight = useRef(false);

  async function run() {
    // Guard double-clicks: the backend is single-flight anyway (it 503s),
    // but not spamming it is the polite client behavior.
    if (inFlight.current) return;
    inFlight.current = true;
    setPhase("running");
    setResult(null);
    // Client INTENT (fire-and-forget). The server emits the trustworthy
    // `demo_run` itself; pass the same anon_id so the funnel can join
    // demo_started -> demo_run -> signup_started.
    track("demo_started", { surface: "landing" });
    let anonId: string | null = null;
    try {
      anonId = getAnonId();
    } catch {
      anonId = null;
    }
    const r = await runDemoConversion(anonId);
    setResult(r);
    setPhase("done");
    inFlight.current = false;
  }

  const dataset = result?.dataset;

  return (
    <section
      aria-labelledby="demo-heading"
      className="mx-auto max-w-3xl px-6 py-12"
    >
      <div className="rounded-lg border border-stone-200 p-6 dark:border-stone-800">
        <h2
          id="demo-heading"
          className="text-2xl font-bold tracking-tight"
        >
          Try it now — no signup
        </h2>
        <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
          Run a real AgiBot World &rarr; LeRobot v3 conversion on a bundled
          sample (1 episode, ~1.5&nbsp;MB) through the exact verified
          converter the paid plans use. Real run, real validator output,
          nothing to upload.
        </p>

        <div className="mt-6">
          <button
            type="button"
            onClick={run}
            disabled={phase === "running"}
            className="rounded-md bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {phase === "running"
              ? "Converting on real infra…"
              : phase === "done"
                ? "Run it again"
                : "Run the demo conversion"}
          </button>
        </div>

        {phase === "running" && (
          <p
            role="status"
            className="mt-4 text-sm text-stone-600 dark:text-stone-400"
          >
            Spinning up the converter and validating the output… this is the
            real thing, usually a few seconds.
          </p>
        )}

        {phase === "done" && result && result.ok && (
          <div className="mt-6 rounded-md bg-stone-50 p-4 dark:bg-stone-900">
            <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
              {result.message ??
                "Real conversion complete — validated LeRobot v3 output."}
            </p>
            {dataset && (
              <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
                Output: <strong>{dataset.episodes}</strong> episode
                {dataset.episodes === 1 ? "" : "s"},{" "}
                <strong>{dataset.frames}</strong> frames,{" "}
                LeRobot {dataset.codebase_version ?? "v3"},{" "}
                {(dataset.output_bytes / 1024).toFixed(0)}&nbsp;KB.
              </p>
            )}
            {result.checks && result.checks.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-medium uppercase tracking-wide text-stone-500">
                  Validator: {result.validator_result ?? "PASS"}
                </p>
                <ul className="mt-2 space-y-1">
                  {result.checks.map((c, i) => (
                    <CheckRow
                      key={`${c.name}-${i}`}
                      name={c.name}
                      status={c.status}
                      detail={c.detail}
                    />
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-5">
              <SignedOut>
                <Link
                  to="/sign-up"
                  onClick={() => track("signup_started", { cta: "demo" })}
                  className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
                >
                  Now convert your own data &rarr;
                </Link>
              </SignedOut>
              <SignedIn>
                <Link
                  to="/dashboard"
                  className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
                >
                  Convert your own data &rarr;
                </Link>
              </SignedIn>
            </div>
          </div>
        )}

        {phase === "done" && result && !result.ok && (
          <div className="mt-6 rounded-md bg-stone-50 p-4 dark:bg-stone-900">
            <p className="text-sm font-medium text-amber-600 dark:text-amber-400">
              {result.message ?? "The demo hit a snag."}
            </p>
            {result.suggestion && (
              <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
                {result.suggestion}
              </p>
            )}
            <div className="mt-4 flex items-center gap-3">
              <button
                type="button"
                onClick={run}
                className="rounded-md border border-stone-300 px-4 py-2 text-sm font-medium hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-900"
              >
                Try again
              </button>
              <SignedOut>
                <Link
                  to="/sign-up"
                  onClick={() => track("signup_started", { cta: "demo_fail" })}
                  className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400"
                >
                  Or sign up free
                </Link>
              </SignedOut>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function PricingTable() {
  const sectionRef = useRef<HTMLElement | null>(null);
  const fired = useRef(false);

  // `pricing_view` fires once, when the pricing section actually scrolls
  // into the viewport — a true "saw pricing" signal, not a page-load
  // proxy. IntersectionObserver is a zero-dependency browser primitive
  // (no analytics SDK). Guarded for the test/jsdom env where IO may be
  // absent (then we conservatively fire on mount so the funnel step is
  // never silently lost). track() is fire-and-forget; the observer is
  // torn down on unmount.
  useEffect(() => {
    const el = sectionRef.current;
    const fire = () => {
      if (fired.current) return;
      fired.current = true;
      track("pricing_view");
    };
    if (typeof IntersectionObserver === "undefined" || !el) {
      fire();
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) fire();
      },
      { threshold: 0.25 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <section
      id="pricing"
      ref={sectionRef}
      aria-labelledby="pricing-heading"
      className="mx-auto max-w-6xl px-6 py-16"
    >
      <h2
        id="pricing-heading"
        className="text-center text-2xl font-bold tracking-tight"
      >
        Pricing
      </h2>
      <p className="mt-2 text-center text-sm text-stone-600 dark:text-stone-400">
        All prices in USD. Billed monthly via Stripe. Cancel any time through
        your customer portal.
      </p>
      <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {TIERS.map((t) => (
          <div
            key={t.id}
            className={[
              "flex flex-col rounded-lg border p-6",
              t.highlight
                ? "border-indigo-500 ring-1 ring-indigo-500"
                : "border-stone-200 dark:border-stone-800",
            ].join(" ")}
          >
            <h3 className="text-lg font-semibold">{t.name}</h3>
            <p className="mt-2">
              <span className="text-3xl font-bold">{t.price}</span>
              <span className="text-sm text-stone-500">{t.cadence}</span>
            </p>
            <ul className="mt-6 flex-1 space-y-2 text-sm text-stone-600 dark:text-stone-400">
              <li>{t.seats}</li>
              <li>{t.episodes}</li>
              <li>{t.directions}</li>
              <li>{t.cap}</li>
              <li>{t.retention}</li>
            </ul>
            <Link
              to={t.id === "free" ? "/sign-up" : "/dashboard"}
              onClick={() => {
                // The Free-tier card CTA is a third sign-up entry point;
                // tag it so the funnel attributes pricing-driven signups.
                if (t.id === "free")
                  track("signup_started", { cta: "pricing_free" });
              }}
              className={[
                "mt-6 rounded-md px-4 py-2 text-center text-sm font-medium",
                t.highlight
                  ? "bg-indigo-600 text-white hover:bg-indigo-500"
                  : "border border-stone-300 hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-900",
              ].join(" ")}
            >
              {t.id === "free" ? "Start free" : `Choose ${t.name}`}
            </Link>
          </div>
        ))}
      </div>
      {/* [VOICE-CHECK] OSS-lib disclosure in footer is a deliberate brand
          choice (docs/pricing-copy.md:141, marker #11). Allen owns whether
          to keep it prominent. */}
      <p className="mt-10 text-center text-xs text-stone-500">
        The underlying conversion engine is the open-source{" "}
        <a
          href="https://pypi.org/project/embodied-data/"
          className="underline"
          target="_blank"
          rel="noopener noreferrer"
        >
          embodied-data
        </a>{" "}
        library — we make it hosted, secure, and team-ready.
      </p>
    </section>
  );
}

export function Landing() {
  // Top of the funnel: one `landing_view` per mount of the public
  // marketing page. Empty deps → fires once per navigation to `/`.
  // track() is fire-and-forget and never blocks the render.
  useEffect(() => {
    track("landing_view");
  }, []);

  return (
    <main>
      <Header />
      <Hero />
      <TryDemo />
      <PricingTable />
    </main>
  );
}
