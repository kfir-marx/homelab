"use client";

import { useEffect, useState } from "react";
import { useDemo } from "../lib/store";

export function UserInfoPage() {
  const { user, bookings, opportunities, setView, logout } = useDemo();
  if (!user) return null;
  return (
    <Page title="Your account" subtitle="Identity and account details used by Tapy.">
      <div className="grid gap-4 sm:grid-cols-2">
        <Info label="Name" value={user.name} />
        <Info label="Email" value={user.email} />
        <Info label="Sign-in methods" value={user.auth_providers.join(", ") || "—"} />
        <Info label="Assigned bookings" value={String(bookings.length)} />
        <Info label="Assigned opportunities" value={String(opportunities.length)} />
      </div>
      <button type="button" onClick={() => setView("settings")} className="mt-6 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white">Open settings</button>
      <button type="button" onClick={() => void logout()} className="ms-3 mt-6 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-600">Sign out</button>
    </Page>
  );
}

export function UserSettingsPage() {
  const { user, updateProfile, connectMailbox, disconnectMailbox, pushToast } = useDemo();
  const [name, setName] = useState(user?.name ?? "");
  const [working, setWorking] = useState("");
  useEffect(() => setName(user?.name ?? ""), [user?.name]);
  if (!user) return null;
  const currentUser = user;

  async function save() {
    setWorking("profile");
    try {
      await updateProfile({ name });
      pushToast({ tone: "success", title: "Settings saved", body: "Your profile is up to date." });
    } catch (reason) {
      pushToast({ tone: "error", title: "Could not save", body: reason instanceof Error ? reason.message : "Try again." });
    } finally { setWorking(""); }
  }

  return (
    <Page title="Settings" subtitle="Manage your profile and delegated email permissions.">
      <section className="rounded-2xl border border-slate-200 p-5">
        <h2 className="font-semibold text-slate-900">Profile</h2>
        <label className="mt-4 block text-sm font-medium text-slate-700">Display name
          <input value={name} onChange={(event) => setName(event.target.value)} className="mt-1.5 w-full max-w-md rounded-xl border border-slate-200 px-3.5 py-2.5 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
        </label>
        <button type="button" disabled={!name.trim() || working === "profile"} onClick={() => void save()} className="mt-4 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">Save profile</button>
      </section>

      <section className="mt-5 rounded-2xl border border-slate-200 p-5">
        <h2 className="font-semibold text-slate-900">Active organization</h2>
        <p className="mt-1 text-sm text-slate-500">All booking, opportunity, notification, and metric requests use this tenant context.</p>
        <select value={user.active_organization_id} onChange={(event) => void updateProfile({ active_organization_id: event.target.value })} className="mt-4 w-full max-w-md rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm">
          {user.memberships.map((membership) => <option key={membership.organization_id} value={membership.organization_id}>{membership.organization_name} · {membership.role}</option>)}
        </select>
      </section>

      <section className="mt-5 rounded-2xl border border-slate-200 p-5">
        <h2 className="font-semibold text-slate-900">Email permissions</h2>
        <p className="mt-1 text-sm leading-relaxed text-slate-500">Tapy asks for read-only email access in the provider&apos;s own consent window. When connected, Tapy reads a bounded set of recent messages and sends their subject, sender, date, and text to Alibaba Cloud Qwen to identify flight and hotel confirmations. Full message bodies are not saved in Tapy&apos;s product database. Tokens go directly to the backend, are encrypted there, and are never exposed to this page. Selecting <strong>Grant read access</strong> requests this processing. See the <a href="/privacy" className="font-medium text-indigo-600 underline underline-offset-2">Privacy Policy</a>.</p>
        <div className="mt-5 space-y-3">
          <MailboxRow provider="gmail" title="Gmail" />
          <MailboxRow provider="outlook" title="Microsoft Outlook" />
        </div>
      </section>
    </Page>
  );

  function MailboxRow({ provider, title }: { provider: "gmail" | "outlook"; title: string }) {
    const mailbox = currentUser.mailboxes.find((item) => item.provider === provider);
    return (
      <div className="flex flex-col gap-3 rounded-xl bg-slate-50 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-slate-900">{title}</p>
          <p className="mt-0.5 text-xs text-slate-500">{mailbox ? `${mailbox.email_address} · ${mailbox.webhook_active ? "live notifications active" : "connected; webhook configuration pending"}` : "Not connected"}</p>
        </div>
        {mailbox ? (
          <button type="button" onClick={() => void disconnectMailbox(provider)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700">Disconnect</button>
        ) : (
          <button type="button" onClick={() => void connectMailbox(provider)} className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white">Grant read access</button>
        )}
      </div>
    );
  }
}

function Page({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <section className="mx-auto max-w-3xl"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Account</p><h1 className="mt-2 text-3xl font-semibold text-slate-900">{title}</h1><p className="mt-1.5 mb-7 text-sm text-slate-500">{subtitle}</p><div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">{children}</div></section>;
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-slate-50 p-4"><p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p><p className="mt-1 text-sm font-medium text-slate-900">{value}</p></div>;
}
