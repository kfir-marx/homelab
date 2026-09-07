"use client";

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
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10">
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
      </div>
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
