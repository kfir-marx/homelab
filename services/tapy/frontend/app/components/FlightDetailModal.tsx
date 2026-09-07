"use client";

import { useEffect, useRef, useState } from "react";
import { sendUpsellWhatsApp } from "../actions";
import { nightsBetween } from "../lib/format";
import { useDemo } from "../lib/store";
import type { Flight } from "../lib/types";

type Props = {
  flight: Flight | null;
  onClose: () => void;
};

type T = (key: string, vars?: Record<string, string | number>) => string;
type Fmt = ReturnType<typeof useDemo>["fmt"];

export default function FlightDetailModal({ flight, onClose }: Props) {
  const { markUpsold, markDeclined, markOpen, pushToast, t, fmt, lang } = useDemo();
  const [mounted, setMounted] = useState(false);
  const [working, setWorking] = useState<"yes" | "no" | "restore" | null>(null);
  // Keep last-known flight so the panel can animate out gracefully after `flight` is cleared.
  const [stickyFlight, setStickyFlight] = useState<Flight | null>(flight);
  const [trackedFlight, setTrackedFlight] = useState<Flight | null>(flight);
  if (flight !== trackedFlight) {
    setTrackedFlight(flight);
    if (flight) setStickyFlight(flight);
  }
  const closeTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (flight) {
      if (closeTimerRef.current) {
        window.clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
      requestAnimationFrame(() => requestAnimationFrame(() => setMounted(true)));
    } else if (stickyFlight) {
      requestAnimationFrame(() => setMounted(false));
      closeTimerRef.current = window.setTimeout(() => {
        setStickyFlight(null);
        setWorking(null);
        closeTimerRef.current = null;
      }, 280);
    }
    return () => {
      if (closeTimerRef.current) {
        window.clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, [flight, stickyFlight]);

  useEffect(() => {
    if (!flight) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [flight, onClose]);

  if (!stickyFlight) return null;
  const f = stickyFlight;

  const commission = f.hotelCostUsd * 0.01;
  const nights = nightsBetween(f.departureDate, f.returnDate);
  const firstName = f.passengerName.split(/[&,]/)[0].trim();
  const isRtl = lang === "he";

  async function handleYes() {
    if (working) return;
    setWorking("yes");
    try {
      await markUpsold(f.id);
    } catch (reason) {
      setWorking(null);
      pushToast({ tone: "error", title: "Could not update flight", body: reason instanceof Error ? reason.message : "Try again." });
      return;
    }
    pushToast({
      tone: "success",
      title: t("toast.upsold.title"),
      body: t("toast.upsold.body", { name: firstName, email: f.email }),
    });
    onClose();
    void sendUpsellWhatsApp({
      flightId: f.id,
      bookingRef: f.bookingRef,
      passengerName: f.passengerName,
      customerEmail: f.email,
      customerPhone: f.phone,
      destinationCity: f.destinationCity,
      departureDate: f.departureDate,
      returnDate: f.returnDate,
    }).then((res) => {
      if (!res.ok) {
        pushToast({
          tone: "error",
          title: t("toast.error.title"),
          body: res.error,
        });
      }
    });
  }

  async function handleNo() {
    if (working) return;
    setWorking("no");
    try {
      await markDeclined(f.id);
    } catch (reason) {
      setWorking(null);
      pushToast({ tone: "error", title: "Could not update flight", body: reason instanceof Error ? reason.message : "Try again." });
      return;
    }
    pushToast({
      tone: "info",
      title: t("toast.declined.title"),
      body: t("toast.declined.body", { ref: f.bookingRef }),
    });
    onClose();
  }

  async function handleRestore() {
    if (working) return;
    setWorking("restore");
    try {
      await markOpen(f.id);
    } catch (reason) {
      setWorking(null);
      pushToast({ tone: "error", title: "Could not update flight", body: reason instanceof Error ? reason.message : "Try again." });
      return;
    }
    pushToast({
      tone: "info",
      title: t("toast.restored.title"),
      body: t("toast.restored.body", { ref: f.bookingRef }),
    });
    onClose();
  }

  const travelersLabel =
    f.partySize === 1
      ? t("modal.travelers.one", { email: f.email })
      : t("modal.travelers.other", { count: f.partySize, email: f.email });

  // The panel anchors to the inline-end (right in LTR, left in RTL) and slides in from
  // that edge. We compute the off-screen translate based on text direction.
  const offscreenTransform = isRtl ? "translateX(-100%)" : "translateX(100%)";

  return (
    <div
      aria-hidden={!mounted}
      className="fixed inset-0 z-40"
      onClick={onClose}
    >
      <div
        className={`absolute inset-0 bg-slate-900/40 backdrop-blur-sm transition-opacity duration-300 ${
          mounted ? "opacity-100" : "opacity-0"
        }`}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={t("modal.bookingRef", { ref: f.bookingRef })}
        onClick={(e) => e.stopPropagation()}
        className="absolute top-0 flex max-h-full w-full max-w-xl flex-col overflow-y-auto bg-white shadow-2xl transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{
          insetInlineEnd: 0,
          transform: mounted ? "translateX(0)" : offscreenTransform,
        }}
      >
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-100 bg-white/95 px-5 py-5 backdrop-blur sm:px-7 sm:py-6">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-slate-400">
              {t("modal.bookingRef", { ref: f.bookingRef })}
            </p>
            <h2 className="mt-1.5 text-xl font-semibold tracking-tight text-slate-900">
              {f.passengerName}
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">{travelersLabel}</p>
          </div>
          <button
            type="button"
            aria-label={t("modal.aria.close")}
            onClick={onClose}
            className="rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="px-5 py-5 sm:px-7 sm:py-6">
          <section className="rounded-2xl border border-slate-200 bg-gradient-to-br from-slate-50 to-white p-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
              {t("modal.itinerary")}
            </p>
            <div className="mt-4 flex items-center justify-between gap-4">
              <RouteEnd
                code={f.origin}
                city={f.originCity}
                date={fmt.date(f.departureDate)}
              />
              <div className="flex flex-1 items-center">
                <span className="h-px flex-1 bg-slate-300" />
                <span className="mx-2 inline-flex h-8 w-8 items-center justify-center rounded-full bg-white text-slate-700 shadow-sm ring-1 ring-slate-200">
                  <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4 rtl:-scale-x-100">
                    <path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V18l-2 1.5V21l3.5-1L15 21v-1.5L13 18v-4.5L21 16z" />
                  </svg>
                </span>
                <span className="h-px flex-1 bg-slate-300" />
              </div>
              <RouteEnd
                code={f.destination}
                city={f.destinationCity}
                date={fmt.date(f.returnDate)}
                align="end"
              />
            </div>
            <div className="mt-5 grid grid-cols-2 gap-3 border-t border-slate-200 pt-4 sm:grid-cols-3">
              <SmallStat label={t("modal.nights")} value={fmt.number(nights)} />
              <SmallStat label={t("modal.flightCost")} value={fmt.usd(f.flightCostUsd)} />
              <SmallStat label={t("modal.hotelValue")} value={fmt.usd(f.hotelCostUsd)} />
            </div>
          </section>

          <section className="mt-5 grid grid-cols-2 gap-3">
            <ContactCard label={t("modal.email")} value={f.email} icon="mail" />
            <ContactCard label={t("modal.phone")} value={f.phone} icon="phone" />
          </section>

          {f.status === "open" ? (
            <section className="mt-6 rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 via-amber-50/50 to-white p-6 shadow-[0_8px_30px_-12px_rgba(217,119,6,0.35)]">
              <div className="flex items-start gap-3">
                <span className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-full bg-amber-500/15 text-amber-700">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
                    <path d="M3 7h18M3 12h18M3 17h12" strokeLinecap="round" />
                  </svg>
                </span>
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-amber-800">
                    {t("modal.upsell.eyebrow")}
                  </p>
                  <h3 className="mt-1 text-base font-semibold leading-snug text-slate-900">
                    {t("modal.upsell.title")}
                  </h3>
                  <p className="mt-1.5 text-sm text-slate-600">
                    {t("modal.upsell.body", {
                      firstName,
                      commission: fmt.usd(commission, { decimals: true }),
                    })}
                  </p>
                </div>
              </div>
            </section>
          ) : (
            <HandledStatusCard flight={f} commission={commission} t={t} fmt={fmt} />
          )}
        </div>

        <footer className="sticky bottom-0 z-10 border-t border-slate-100 bg-slate-50/85 px-5 py-4 backdrop-blur sm:px-7 sm:py-5">
          {f.status === "open" ? (
            <>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={handleNo}
                  disabled={!!working}
                  className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 active:scale-[0.99] disabled:opacity-60"
                >
                  {t("modal.btn.no")}
                </button>
                <button
                  type="button"
                  onClick={handleYes}
                  disabled={!!working}
                  className="group relative flex-[1.6] overflow-hidden rounded-xl bg-gradient-to-r from-amber-500 to-orange-500 px-4 py-3 text-sm font-semibold text-white shadow-[0_8px_24px_-8px_rgba(234,88,12,0.6)] transition hover:from-amber-400 hover:to-orange-400 active:scale-[0.99] disabled:opacity-70"
                >
                  <span className="relative inline-flex items-center justify-center gap-2">
                    <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4">
                      <path d="M20 4 9 15l-5-5" stroke="white" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    {t("modal.btn.yes")}
                  </span>
                  <span
                    aria-hidden
                    className="absolute inset-y-0 w-1/3 bg-white/30 blur-md transition-transform duration-700 ease-out"
                    style={{
                      insetInlineStart: "-33%",
                      // Sweep follows reading direction.
                      transform: "translateX(0)",
                    }}
                  />
                </button>
              </div>
              <p className="mt-3 text-center text-[11px] text-slate-500">
                {t("modal.legal")}
              </p>
            </>
          ) : f.status === "declined" ? (
            <div className="flex gap-3">
              <button
                type="button"
                onClick={onClose}
                disabled={!!working}
                className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 active:scale-[0.99] disabled:opacity-60"
              >
                {t("modal.btn.close")}
              </button>
              <button
                type="button"
                onClick={handleRestore}
                disabled={!!working}
                className="flex-[1.6] overflow-hidden rounded-xl bg-gradient-to-r from-slate-900 to-slate-700 px-4 py-3 text-sm font-semibold text-white shadow-[0_8px_24px_-8px_rgba(15,23,42,0.55)] transition hover:from-slate-800 hover:to-slate-600 active:scale-[0.99] disabled:opacity-70"
              >
                <span className="relative inline-flex items-center justify-center gap-2">
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="h-4 w-4 rtl:-scale-x-100"
                  >
                    <path d="M9 14 4 9l5-5" />
                    <path d="M4 9h10a6 6 0 0 1 0 12h-3" />
                  </svg>
                  {t("modal.btn.restore")}
                </span>
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 active:scale-[0.99]"
            >
              {t("modal.btn.close")}
            </button>
          )}
        </footer>
      </aside>
    </div>
  );
}

function RouteEnd({
  code,
  city,
  date,
  align,
}: {
  code: string;
  city: string;
  date: string;
  align?: "start" | "end";
}) {
  return (
    <div className={align === "end" ? "text-end" : "text-start"}>
      <p className="text-3xl font-semibold tracking-tight text-slate-900 tabular-nums">
        {code}
      </p>
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{city}</p>
      <p className="mt-1 text-xs font-medium text-slate-600">{date}</p>
    </div>
  );
}

function SmallStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </p>
      <p className="mt-0.5 text-sm font-semibold text-slate-900 tabular-nums">{value}</p>
    </div>
  );
}

function ContactCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon: "mail" | "phone";
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <span className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-slate-100 text-slate-600">
        {icon === "mail" ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-4 w-4">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="m4 7 8 6 8-6" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-4 w-4">
            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12 12 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12 12 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z" />
          </svg>
        )}
      </span>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </p>
        <p className="truncate text-sm font-medium text-slate-900">{value}</p>
      </div>
    </div>
  );
}

function HandledStatusCard({
  flight,
  commission,
  t,
  fmt,
}: {
  flight: Flight;
  commission: number;
  t: T;
  fmt: Fmt;
}) {
  const variant = {
    upsold: {
      tone: "border-emerald-200 from-emerald-50 via-emerald-50/40 shadow-[0_8px_30px_-12px_rgba(16,185,129,0.35)]",
      pillBg: "bg-emerald-500/15 text-emerald-700",
      eyebrow: "text-emerald-800",
      label: t("handled.upsold.label"),
      title: t("handled.upsold.title"),
      body: t("handled.upsold.body", {
        email: flight.email,
        commission: fmt.usd(commission, { decimals: true }),
      }),
    },
    declined: {
      tone: "border-slate-200 from-slate-50 via-slate-50/40 shadow-none",
      pillBg: "bg-slate-200 text-slate-700",
      eyebrow: "text-slate-700",
      label: t("handled.declined.label"),
      title: t("handled.declined.title"),
      body: t("handled.declined.body"),
    },
    past: {
      tone: "border-slate-200 from-slate-50 via-slate-50/40 shadow-none",
      pillBg: "bg-slate-200 text-slate-700",
      eyebrow: "text-slate-700",
      label: t("handled.past.label"),
      title: t("handled.past.title"),
      body: t("handled.past.body"),
    },
    open: null,
  }[flight.status];

  if (!variant) return null;

  return (
    <section
      className={`mt-6 rounded-2xl border bg-gradient-to-br to-white p-6 ${variant.tone}`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`inline-flex h-8 w-8 flex-none items-center justify-center rounded-full ${variant.pillBg}`}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="h-4 w-4"
          >
            <path d="M20 6 9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <div>
          <p
            className={`text-[11px] font-semibold uppercase tracking-[0.18em] ${variant.eyebrow}`}
          >
            {variant.label}
          </p>
          <h3 className="mt-1 text-base font-semibold leading-snug text-slate-900">
            {variant.title}
          </h3>
          <p className="mt-1.5 text-sm text-slate-600">{variant.body}</p>
        </div>
      </div>
    </section>
  );
}
