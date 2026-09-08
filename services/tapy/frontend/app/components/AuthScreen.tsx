"use client";

import Link from "next/link";
import { useState } from "react";
import { useDemo } from "../lib/store";

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
    <main className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-7 shadow-xl shadow-slate-200/50 sm:p-9">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-900 text-lg font-bold text-white">T</div>
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Welcome to Tapy</h1>
            <p className="text-sm text-slate-500">Your live hotel upsell workspace</p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
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
      <section className="mt-6 w-full max-w-md rounded-2xl border border-slate-200/80 bg-white/70 px-6 py-5 text-sm leading-6 text-slate-600 shadow-sm">
        <h2 className="font-semibold text-slate-900">Built for travel professionals</h2>
        <p className="mt-2">
          Tapy is an invitation-based pilot that uses delegated, read-only mailbox access to identify confirmed flights and hotels, match bookings, and help travel teams manage hotel-upsell opportunities. Tapy cannot change or delete mailbox messages or make travel reservations.
        </p>
        <p className="mt-3 text-xs leading-5 text-slate-500">
          Tapy&apos;s use of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements.
        </p>
      </section>
    </main>
  );
}

const INPUT = "mt-1.5 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-sm font-medium text-slate-700">{label}{children}</label>;
}

function ProviderButton({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-60">Continue with {children}</button>;
}
