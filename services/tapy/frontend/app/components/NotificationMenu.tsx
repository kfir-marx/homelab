"use client";

import { useEffect, useRef, useState } from "react";
import { LOCALES } from "../lib/i18n";
import { useDemo } from "../lib/store";
const tone = (kind: string) => kind.includes("failed")
  ? "bg-rose-100 text-rose-700"
  : kind.includes("send") ? "bg-indigo-100 text-indigo-700" : "bg-emerald-100 text-emerald-700";

export default function NotificationMenu() {
  const {
    notifications,
    unreadNotificationCount,
    markNotificationsRead,
    pushToast,
    lang,
    t,
  } = useDemo();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", dismiss);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && unreadNotificationCount > 0) {
      void markNotificationsRead().catch((error) => {
        pushToast({
          tone: "error",
          title: t("notifications.readError"),
          body: error instanceof Error ? error.message : t("notifications.tryAgain"),
        });
      });
    }
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-label={t("notifications.aria")}
        aria-expanded={open}
        className="relative flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-900"
      >
        <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M10 21h4" strokeLinecap="round" />
        </svg>
        {unreadNotificationCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-rose-500" />
        )}
      </button>

      {open && (
        <section
          aria-label={t("notifications.title")}
          className="absolute z-50 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"
          style={{ insetInlineEnd: 0 }}
        >
          <header className="border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-900">{t("notifications.title")}</h2>
            <p className="mt-0.5 text-[11px] text-slate-500">{t("notifications.subtitle")}</p>
          </header>
          <div className="max-h-96 overflow-y-auto">
            {notifications.length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-slate-500">
                {t("notifications.empty")}
              </p>
            ) : (
              notifications.map((notification) => (
                <article key={notification.id} className="flex gap-3 border-b border-slate-100 px-4 py-3 last:border-0">
                  <span className={`mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full text-xs ${tone(notification.kind)}`}>
                    {notification.kind.includes("failed") ? "!" : "✓"}
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-slate-900">{notification.title}</p>
                    <p className="mt-0.5 text-xs leading-relaxed text-slate-600">{notification.message}</p>
                    <time className="mt-1 block text-[10px] text-slate-400">
                      {new Intl.DateTimeFormat(LOCALES[lang], {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }).format(new Date(notification.created_at))}
                    </time>
                  </div>
                </article>
              ))
            )}
          </div>
        </section>
      )}
    </div>
  );
}
