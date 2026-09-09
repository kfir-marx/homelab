"use client";

import Link from "next/link";
import { useDemo } from "../lib/store";
import AgentView from "./AgentView";
import AgencyView from "./AgencyView";
import AuthScreen from "./AuthScreen";
import NotificationMenu from "./NotificationMenu";
import ToastStack from "./ToastStack";
import { UserInfoPage, UserSettingsPage } from "./UserPages";

export default function AppShell() {
  const { user, view, setView, lang, setLang, logout, loading } = useDemo();
  if (loading) return <main className="grid min-h-screen place-items-center text-sm text-slate-500">Loading Tapy…</main>;
  if (!user) return <AuthScreen />;
  const organization = user.memberships.find((item) => item.organization_id === user.active_organization_id);
  return <div className="min-h-screen bg-slate-50 text-slate-900">
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur"><div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3 sm:px-6"><div className="grid h-9 w-9 place-items-center rounded-xl bg-slate-900 font-bold text-white">T</div><div className="me-auto hidden sm:block"><p className="text-sm font-semibold">Tapy</p><p className="text-xs text-slate-500">{organization?.organization_name} · {user.active_organization_role}</p></div><nav className="flex rounded-xl bg-slate-100 p-1"><Nav active={view === "personal"} onClick={() => setView("personal")}>My data</Nav>{user.active_organization_role === "admin" && <Nav active={view === "organization"} onClick={() => setView("organization")}>Organization</Nav>}</nav><NotificationMenu /><button onClick={() => setLang(lang === "en" ? "he" : "en")} className="rounded-lg border border-slate-200 px-2 py-1.5 text-xs font-semibold">{lang.toUpperCase()}</button><button onClick={() => setView(view === "settings" ? "personal" : "settings")} className="text-xs font-semibold text-slate-600">Settings</button><button onClick={() => void logout()} className="hidden text-xs font-semibold text-rose-600 sm:block">Sign out</button></div></header>
    <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">{view === "personal" ? <AgentView /> : view === "organization" ? <AgencyView /> : view === "profile" ? <UserInfoPage /> : <UserSettingsPage />}</main>
    <footer className="mx-auto flex max-w-7xl justify-center gap-5 px-4 pb-8 text-xs text-slate-500"><Link href="/privacy">Privacy</Link><Link href="/terms">Terms</Link></footer><ToastStack />
  </div>;
}

function Nav({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${active ? "bg-white shadow-sm" : "text-slate-500"}`}>{children}</button>;
}
