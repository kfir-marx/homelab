"use client";

import { useDemo } from "../lib/store";
import type { Lang } from "../lib/i18n";
import AgentView from "./AgentView";
import AgencyView from "./AgencyView";
import AuthScreen from "./AuthScreen";
import ChatBot from "./ChatBot";
import ToastStack from "./ToastStack";
import { UserInfoPage, UserSettingsPage } from "./UserPages";

export default function AppShell() {
  const { view, setView, agents, activeAgentId, lang, setLang, t, user, loading, logout } = useDemo();
  const me = agents.find((a) => a.id === activeAgentId);

  if (loading) return <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-500">Loading Tapy…</div>;
  if (!user || !me) return <AuthScreen />;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-white/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-2 px-4 py-3 sm:gap-6 sm:px-6 sm:py-3.5">
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div className="flex h-9 w-9 flex-none items-center justify-center rounded-xl bg-gradient-to-br from-slate-900 to-slate-700 text-sm font-bold text-white shadow-sm">
              T
            </div>
            <div className="hidden sm:block">
              <p className="text-sm font-semibold tracking-tight text-slate-900">
                {t("nav.brand")}
              </p>
              <p className="text-[11px] uppercase tracking-[0.14em] text-slate-500">
                {t("nav.subtitle")}
              </p>
            </div>
          </div>

          <ViewToggle
            current={view === "agency" ? "agency" : "agent"}
            onChange={setView}
            labels={{ agent: t("nav.agent"), agency: t("nav.agency") }}
          />

          <div className="flex items-center gap-2 sm:gap-3">
            <LangToggle
              current={lang}
              onChange={setLang}
              ariaLabel={t("nav.lang.aria")}
              labels={{ en: t("nav.lang.en"), he: t("nav.lang.he") }}
            />
            <div className="hidden text-end md:block">
              <p className="text-xs font-semibold text-slate-900">{me.name}</p>
              <p className="text-[11px] text-slate-500">{t("nav.role")}</p>
            </div>
            <button
              type="button"
              onClick={() => setView(view === "profile" ? "agent" : "profile")}
              aria-label="Open user information"
              className={`flex h-9 w-9 flex-none items-center justify-center rounded-full bg-gradient-to-br ${me.avatarTint} text-xs font-semibold text-white shadow-sm ring-2 ring-white`}
            >
              {me.initials}
            </button>
            <button type="button" onClick={() => setView("settings")} className="hidden text-xs font-semibold text-slate-600 hover:text-slate-900 sm:block">Settings</button>
            <button type="button" onClick={() => void logout()} className="hidden text-xs font-semibold text-slate-400 hover:text-rose-600 sm:block">Sign out</button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-10">
        <div key={view} className="view-fade">
          {view === "agent" ? <AgentView /> : view === "agency" ? <AgencyView /> : view === "profile" ? <UserInfoPage /> : <UserSettingsPage />}
        </div>
      </main>

      <ToastStack />
      {(view === "agent" || view === "agency") && <ChatBot />}
    </div>
  );
}

function ViewToggle({
  current,
  onChange,
  labels,
}: {
  current: "agent" | "agency";
  onChange: (v: "agent" | "agency") => void;
  labels: { agent: string; agency: string };
}) {
  return (
    <div className="relative flex items-center rounded-full border border-slate-200 bg-slate-100/80 p-1 shadow-inner">
      <span
        aria-hidden
        className="absolute top-1 bottom-1 w-[calc(50%-0.25rem)] rounded-full bg-white shadow-sm transition-[inset-inline-start] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{
          insetInlineStart: current === "agent" ? "0.25rem" : "calc(50% - 0.125rem)",
        }}
      />
      <button
        type="button"
        onClick={() => onChange("agent")}
        className={`relative z-10 rounded-full px-4 py-1.5 text-sm font-medium transition ${
          current === "agent" ? "text-slate-900" : "text-slate-500 hover:text-slate-700"
        }`}
      >
        {labels.agent}
      </button>
      <button
        type="button"
        onClick={() => onChange("agency")}
        className={`relative z-10 rounded-full px-4 py-1.5 text-sm font-medium transition ${
          current === "agency" ? "text-slate-900" : "text-slate-500 hover:text-slate-700"
        }`}
      >
        {labels.agency}
      </button>
    </div>
  );
}

function LangToggle({
  current,
  onChange,
  labels,
  ariaLabel,
}: {
  current: Lang;
  onChange: (l: Lang) => void;
  labels: { en: string; he: string };
  ariaLabel: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="relative flex items-center rounded-full border border-slate-200 bg-slate-100/80 p-1 shadow-inner"
    >
      <span
        aria-hidden
        className="absolute top-1 bottom-1 w-[calc(50%-0.25rem)] rounded-full bg-white shadow-sm transition-[inset-inline-start] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{
          insetInlineStart: current === "en" ? "0.25rem" : "calc(50% - 0.125rem)",
        }}
      />
      <button
        type="button"
        onClick={() => onChange("en")}
        aria-pressed={current === "en"}
        className={`relative z-10 rounded-full px-3 py-1 text-xs font-semibold tracking-wide transition ${
          current === "en" ? "text-slate-900" : "text-slate-500 hover:text-slate-700"
        }`}
      >
        {labels.en}
      </button>
      <button
        type="button"
        onClick={() => onChange("he")}
        aria-pressed={current === "he"}
        className={`relative z-10 rounded-full px-3 py-1 text-xs font-semibold tracking-wide transition ${
          current === "he" ? "text-slate-900" : "text-slate-500 hover:text-slate-700"
        }`}
      >
        {labels.he}
      </button>
    </div>
  );
}
