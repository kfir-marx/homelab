"use client";

import Link from "next/link";
import { useState } from "react";
import { useDemo } from "../lib/store";

const PRODUCT_STEPS = [
  {
    number: "01",
    title: "Connect a mailbox",
    body: "A travel professional can optionally connect Gmail or Outlook with delegated, read-only access. Tapy cannot send, edit, or delete email.",
  },
  {
    number: "02",
    title: "Identify travel confirmations",
    body: "Tapy examines a bounded set of recent messages to identify confirmed flights and hotels and extract the booking details needed for matching.",
  },
  {
    number: "03",
    title: "Review hotel opportunities",
    body: "Ticket-level opportunities appear in an agent workspace; organization-wide data is available only to organization admins.",
  },
  {
    number: "04",
    title: "Act with human review",
    body: "An agent reviews the result and can choose to send a hotel offer through WhatsApp, update its status, or dismiss the opportunity.",
  },
];

export default function AuthScreen() {
  const { login, register, socialLogin } = useDemo();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError("");
    try {
      if (mode === "register") await register(name, email, password);
      else await login(email, password);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Authentication failed");
    } finally {
      setWorking(false);
    }
  }

  async function provider(value: "google" | "microsoft") {
    setWorking(true);
    setError("");
    try {
      await socialLogin(value);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Authentication failed");
      setWorking(false);
    }
  }

  return (
    <main className="min-h-screen overflow-hidden bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200/80 bg-white/90 backdrop-blur">
        <nav aria-label="Main navigation" className="mx-auto flex max-w-7xl items-center justify-between gap-5 px-4 py-4 sm:px-6 lg:px-8">
          <Link href="/" aria-label="Tapy home" className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-950 text-base font-bold text-white shadow-sm">T</span>
            <span>
              <span className="block text-base font-semibold leading-5 tracking-tight">Tapy</span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.16em] text-slate-500">Travel upsell workspace</span>
            </span>
          </Link>
          <div className="hidden items-center gap-7 text-sm font-medium text-slate-600 md:flex">
            <a href="#product" className="transition hover:text-slate-950">How it works</a>
            <a href="#data-use" className="transition hover:text-slate-950">How data is used</a>
            <Link href="/privacy" className="transition hover:text-slate-950">Privacy Policy</Link>
          </div>
          <a href="#access" className="rounded-full bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800">
            Sign in
          </a>
        </nav>
      </header>

      <section className="relative">
        <div aria-hidden className="absolute inset-x-0 top-0 -z-0 h-[34rem] bg-[radial-gradient(circle_at_75%_25%,rgba(99,102,241,0.14),transparent_35%),radial-gradient(circle_at_20%_15%,rgba(14,165,233,0.09),transparent_32%)]" />
        <div className="relative z-10 mx-auto grid max-w-7xl items-center gap-14 px-4 py-20 sm:px-6 sm:py-28 lg:grid-cols-[1.1fr_0.9fr] lg:px-8 lg:py-32">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700">
              <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
              Invitation-based pilot
            </div>
            <h1 className="mt-6 max-w-3xl text-4xl font-semibold tracking-[-0.04em] text-slate-950 sm:text-6xl sm:leading-[1.06]">
              Turn travel confirmations into timely hotel opportunities.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-slate-600">
              Tapy is a workspace for travel professionals. It identifies confirmed flights and hotels from a connected business mailbox, highlights trips that may still need accommodation, and helps agents manage and send hotel-upsell offers.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-4">
              <a href="#access" className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white shadow-lg shadow-indigo-200 transition hover:bg-indigo-500">
                Access Tapy
              </a>
              <a href="#product" className="rounded-xl border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-700 transition hover:border-slate-400 hover:text-slate-950">
                See how it works
              </a>
            </div>
            <p className="mt-5 text-sm text-slate-500">Built for small pilot teams working with real travel-booking data.</p>
          </div>

          <div className="rounded-[2rem] border border-slate-200 bg-white p-4 shadow-2xl shadow-slate-300/50 sm:p-6">
            <div className="rounded-2xl bg-slate-950 p-6 text-white sm:p-8">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-300">From inbox to action</p>
              <div className="mt-7 space-y-4">
                <JourneyRow label="Booking confirmation received" detail="Read-only mailbox connection" />
                <JourneyRow label="Flight and hotel details identified" detail="Relevant booking facts only" />
                <JourneyRow label="Hotel gap shown to the agent" detail="Review before any action" />
              </div>
              <div className="mt-7 rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-sm leading-6 text-slate-300">
                Tapy does not book travel, alter mailbox messages, or send an offer without an agent choosing to do so.
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="product" className="border-y border-slate-200 bg-white py-20 sm:py-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="max-w-2xl">
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-indigo-600">How Tapy works</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">A focused workflow for travel teams</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">Tapy supports the path from a booking confirmation to a reviewed hotel opportunity. It is not a travel provider and does not make reservations.</p>
          </div>
          <div className="mt-12 grid gap-px overflow-hidden rounded-3xl border border-slate-200 bg-slate-200 md:grid-cols-2">
            {PRODUCT_STEPS.map((step) => (
              <article key={step.number} className="bg-white p-7 sm:p-9">
                <span className="text-sm font-semibold text-indigo-600">{step.number}</span>
                <h3 className="mt-4 text-lg font-semibold text-slate-950">{step.title}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-600">{step.body}</p>
              </article>
            ))}
          </div>
          <div className="mt-8 grid gap-5 sm:grid-cols-3">
            <Capability title="Personal workspace" body="See only bookings and opportunities assigned to the signed-in agent." />
            <Capability title="Admin overview" body="Authorized admins see organization metrics and per-agent performance." />
            <Capability title="Recipient control" body="Select one or more recipients and record manual inclusion or exclusion decisions." />
          </div>
        </div>
      </section>

      <section id="data-use" className="bg-slate-950 py-20 text-white sm:py-24">
        <div className="mx-auto grid max-w-7xl gap-12 px-4 sm:px-6 lg:grid-cols-[0.8fr_1.2fr] lg:px-8">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-indigo-300">Transparent data use</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">Why Tapy requests Google user data</h2>
            <p className="mt-5 text-base leading-7 text-slate-300">
              If a user chooses to connect Gmail, Tapy requests the <strong className="font-semibold text-white">gmail.readonly</strong> scope solely to find flight and hotel confirmations and provide the matching workflow described on this page.
            </p>
            <Link href="/privacy" className="mt-7 inline-flex rounded-xl bg-white px-5 py-3 text-sm font-semibold text-slate-950 transition hover:bg-slate-100">
              Read the Privacy Policy
            </Link>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <DataCard title="What is processed" body="A bounded set of recent message subjects, senders, dates, and text is classified to identify booking confirmations. Relevant booking facts are then shown in Tapy." />
            <DataCard title="How AI is involved" body="Selected message content is sent to Alibaba Cloud Qwen for booking classification and fact extraction. Deterministic application code creates opportunities and recipients." />
            <DataCard title="What Tapy cannot do" body="The requested Gmail permission does not allow Tapy to send, edit, or delete messages. Tapy cannot make or change a travel reservation." />
            <DataCard title="What Tapy does not do" body="Tapy does not sell Google user data, use it for advertising or credit decisions, or use it to train generalized AI models." />
          </div>
        </div>
        <div className="mx-auto mt-10 max-w-7xl px-4 text-sm leading-6 text-slate-400 sm:px-6 lg:px-8">
          Tapy&apos;s use and transfer of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements. Mailbox connection is optional and can be disconnected from Tapy settings.
        </div>
      </section>

      <section id="access" className="bg-slate-100 py-20 sm:py-24">
        <div className="mx-auto grid max-w-5xl items-start gap-12 px-4 sm:px-6 lg:grid-cols-[0.85fr_1.15fr] lg:px-8">
          <div className="pt-4">
            <p className="text-sm font-semibold uppercase tracking-[0.16em] text-indigo-600">Pilot access</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">Sign in to your workspace</h2>
            <p className="mt-4 text-base leading-7 text-slate-600">Tapy is currently an invitation-based pilot for travel professionals. Sign-in identifies your Tapy account; connecting a mailbox is a separate, optional step performed inside Settings.</p>
            <div className="mt-7 space-y-3 text-sm text-slate-600">
              <p className="flex gap-3"><Check />Your mailbox is not accessed merely by signing in.</p>
              <p className="flex gap-3"><Check />Mailbox access is requested on a separate provider consent screen.</p>
              <p className="flex gap-3"><Check />You can use an email/password account or supported identity provider.</p>
            </div>
          </div>

          <div className="rounded-3xl border border-slate-200 bg-white p-7 shadow-xl shadow-slate-300/40 sm:p-9">
            <div>
              <h3 className="text-xl font-semibold text-slate-900">{mode === "login" ? "Welcome back" : "Create your Tapy account"}</h3>
              <p className="mt-1 text-sm text-slate-500">{mode === "login" ? "Sign in to continue to your travel workspace." : "Pilot access may be limited to approved travel teams."}</p>
            </div>

            <div className="mt-7 grid grid-cols-2 gap-3">
              <ProviderButton onClick={() => void provider("google")} disabled={working}>Google</ProviderButton>
              <ProviderButton onClick={() => void provider("microsoft")} disabled={working}>Microsoft</ProviderButton>
            </div>

            <div className="my-6 flex items-center gap-3 text-xs uppercase tracking-wider text-slate-400">
              <span className="h-px flex-1 bg-slate-200" />or use email<span className="h-px flex-1 bg-slate-200" />
            </div>

            <form onSubmit={submit} className="space-y-4">
              {mode === "register" && (
                <Field label="Name">
                  <input required autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} className={INPUT} />
                </Field>
              )}
              <Field label="Email">
                <input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} className={INPUT} />
              </Field>
              <Field label="Password">
                <input required type="password" minLength={mode === "register" ? 10 : 1} autoComplete={mode === "register" ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} className={INPUT} />
              </Field>
              {error && <p role="alert" className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
              <button disabled={working} className="w-full rounded-xl bg-slate-900 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60">
                {working ? "Please wait…" : mode === "register" ? "Create account" : "Sign in"}
              </button>
            </form>

            <button type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }} className="mt-5 w-full text-center text-sm font-medium text-indigo-600 hover:text-indigo-500">
              {mode === "login" ? "New to Tapy? Create an account" : "Already have an account? Sign in"}
            </button>

            <p className="mt-6 border-t border-slate-100 pt-5 text-center text-xs leading-5 text-slate-500">
              By using Tapy, you agree to the <Link href="/terms" className="font-medium text-slate-700 underline underline-offset-2 hover:text-slate-950">Terms of Service</Link> and acknowledge the <Link href="/privacy" className="font-medium text-slate-700 underline underline-offset-2 hover:text-slate-950">Privacy Policy</Link>.
            </p>
          </div>
        </div>
      </section>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-8 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
          <div>
            <p className="font-semibold text-slate-900">Tapy</p>
            <p className="mt-1">An invitation-based travel operations service operated by Kfir Marx.</p>
          </div>
          <nav aria-label="Footer navigation" className="flex flex-wrap gap-x-6 gap-y-2 font-medium">
            <Link href="/privacy" className="hover:text-slate-950">Privacy Policy</Link>
            <Link href="/terms" className="hover:text-slate-950">Terms of Service</Link>
            <a href="mailto:kfir.marx@gmail.com" className="hover:text-slate-950">Contact</a>
          </nav>
        </div>
      </footer>
    </main>
  );
}

const INPUT = "mt-1.5 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100";

function JourneyRow({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-white/10 bg-white/[0.04] p-4">
      <span className="flex h-8 w-8 flex-none items-center justify-center rounded-full bg-emerald-400/15 text-emerald-300">✓</span>
      <span>
        <span className="block text-sm font-semibold text-white">{label}</span>
        <span className="mt-0.5 block text-xs text-slate-400">{detail}</span>
      </span>
    </div>
  );
}

function Capability({ title, body }: { title: string; body: string }) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-slate-50 p-6">
      <h3 className="font-semibold text-slate-950">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-600">{body}</p>
    </article>
  );
}

function DataCard({ title, body }: { title: string; body: string }) {
  return (
    <article className="rounded-2xl border border-white/10 bg-white/[0.05] p-6">
      <h3 className="font-semibold text-white">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-300">{body}</p>
    </article>
  );
}

function Check() {
  return <span aria-hidden className="mt-0.5 text-emerald-600">✓</span>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-sm font-medium text-slate-700">{label}{children}</label>;
}

function ProviderButton({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-60">Continue with {children}</button>;
}
