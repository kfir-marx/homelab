"use client";
import { useState } from "react";
import { api, useDemo } from "../lib/store";

export default function InvitationScreen({ token, done }: { token: string; done: () => void }) {
  const { user, login, logout, socialLogin, refresh } = useDemo();
  const [existing, setExisting] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function accept(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      if (existing && !user) await login(email, password);
      await api("/v1/invitations/accept", { method: "POST", body: JSON.stringify({ token, ...(!user && !existing ? { name, password } : {}) }) });
      sessionStorage.removeItem("tapy-invitation"); history.replaceState(null, "", "/");
      done(); await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Invitation failed"); }
    finally { setBusy(false); }
  }
  const field = "block w-full rounded-lg border p-3 mt-3";
  return <main className="min-h-screen grid place-items-center bg-slate-50 p-6"><section className="w-full max-w-md rounded-2xl bg-white p-8 shadow-sm">
    <h1 className="text-2xl font-semibold">Accept your Tapy invitation</h1>
    <p className="mt-3 text-sm text-slate-600">The invitation determines your email, organization, and role. Links expire after seven days.</p>
    {user ? <p className="mt-4">Signed in as {user.email}. <button className="underline" onClick={() => void logout()}>Use another account</button></p> : <button className="mt-4 underline" onClick={() => setExisting(!existing)}>{existing ? "I need a new account" : "I already have an account"}</button>}
    <form onSubmit={accept}>
      {!user && (existing ? <label>Email<input className={field} type="email" required value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" /></label> : <label>Name<input className={field} required value={name} onChange={e => setName(e.target.value)} maxLength={120} autoComplete="name" /></label>)}
      {!user && <label>{existing ? "Password" : "Choose your password"}<input className={field} type="password" required minLength={existing ? 1 : 10} maxLength={256} value={password} onChange={e => setPassword(e.target.value)} autoComplete={existing ? "current-password" : "new-password"} /></label>}
      {error && <p role="alert" className="mt-3 text-red-700">{error}</p>}
      <button disabled={busy} className="mt-5 rounded-lg bg-indigo-600 p-3 text-white disabled:opacity-50">{busy ? "Please wait…" : "Accept invitation"}</button>
    </form>
    {!user && existing && <div className="mt-4 flex gap-3">{(["google", "microsoft"] as const).map(p => <button key={p} onClick={() => void socialLogin(p).catch(e => setError(String(e)))} className="underline">Sign in with {p}</button>)}</div>}
    <button className="mt-4 text-sm underline" onClick={() => { sessionStorage.removeItem("tapy-invitation"); history.replaceState(null, "", "/"); done(); }}>Cancel</button>
  </section></main>;
}
