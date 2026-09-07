"use client";

import { nightsBetween } from "../lib/format";
import { useDemo } from "../lib/store";
import type { Flight, FlightStatus } from "../lib/types";

type Props = {
  flight: Flight;
  stagger?: number;
  onOpen?: () => void;
};

export default function FlightCard({ flight, stagger = 0, onOpen }: Props) {
  const { t, fmt } = useDemo();
  const isOpen = flight.status === "open";

  return (
    <button
      type="button"
      onClick={onOpen}
      className={`card-enter group relative flex w-full flex-col items-stretch overflow-hidden rounded-2xl border text-start transition-all duration-300 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 ${
        isOpen
          ? "border-amber-200 bg-gradient-to-br from-amber-50 via-amber-50/40 to-white shadow-[0_4px_30px_-12px_rgba(217,119,6,0.35)] hover:-translate-y-0.5 hover:shadow-[0_18px_44px_-16px_rgba(217,119,6,0.45)] focus-visible:ring-amber-400"
          : "border-slate-200 bg-white opacity-70 saturate-50 shadow-none hover:opacity-90 hover:saturate-75 hover:shadow-sm focus-visible:ring-slate-400"
      }`}
      style={{ animationDelay: `${stagger * 40}ms` }}
    >
      {isOpen && (
        <span
          aria-hidden
          className="pointer-events-none absolute -top-12 h-32 w-32 rounded-full bg-amber-300/30 blur-3xl"
          style={{ insetInlineEnd: "-3rem" }}
        />
      )}

      <div className="relative flex items-start justify-between gap-3 px-5 pt-5">
        <div className="min-w-0">
          <p className="font-mono text-[11px] tracking-wider text-slate-400">
            {flight.bookingRef}
          </p>
          <p className="mt-1 truncate text-base font-semibold text-slate-900">
            {flight.passengerName}
          </p>
        </div>
        <StatusBadge status={flight.status} label={t(`card.status.${flight.status}`)} />
      </div>

      <div className="relative mt-5 px-5">
        <div className="flex items-center justify-between">
          <Endpoint code={flight.origin} city={flight.originCity} />
          <PlaneLine open={isOpen} />
          <Endpoint code={flight.destination} city={flight.destinationCity} align="end" />
        </div>
      </div>

      <div className="relative mx-5 mt-5 grid grid-cols-2 gap-3 border-t border-dashed border-slate-200/80 pt-4">
        <Stat
          label={t("card.dates")}
          value={fmt.dateRange(flight.departureDate, flight.returnDate)}
        />
        <Stat
          label={t("modal.nights")}
          value={fmt.number(nightsBetween(flight.departureDate, flight.returnDate))}
        />
      </div>

      <div className="relative mt-4 flex items-center justify-between gap-3 border-t border-slate-100 bg-white/60 px-5 py-3.5">
        {isOpen ? (
          <>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
                {t("card.potentialCommission")}
              </p>
              <p className="mt-0.5 text-base font-semibold text-amber-700 tabular-nums">
                {fmt.usd(flight.hotelCostUsd * 0.01, { decimals: true })}
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate-900 transition group-hover:gap-2.5">
              {t("card.review")}
              <Chevron />
            </span>
          </>
        ) : (
          <>
            <p className="text-xs text-slate-500">{handledHelpText(flight.status, t)}</p>
            <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 transition group-hover:gap-1.5 group-hover:text-slate-700">
              {t("card.view")}
              <Chevron small />
            </span>
          </>
        )}
      </div>
    </button>
  );
}

function Chevron({ small = false }: { small?: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={`${small ? "h-3.5 w-3.5" : "h-4 w-4"} rtl:-scale-x-100`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
    >
      <path d="M6 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function handledHelpText(
  status: FlightStatus,
  t: (key: string, vars?: Record<string, string | number>) => string,
) {
  switch (status) {
    case "upsold":
      return t("card.help.upsold");
    case "declined":
      return t("card.help.declined");
    case "past":
      return t("card.help.past");
    default:
      return "";
  }
}

function StatusBadge({ status, label }: { status: FlightStatus; label: string }) {
  const styles = {
    open: "bg-amber-100 text-amber-800 ring-amber-200",
    upsold: "bg-emerald-100 text-emerald-800 ring-emerald-200",
    declined: "bg-slate-200 text-slate-700 ring-slate-300",
    past: "bg-slate-100 text-slate-600 ring-slate-200",
  } as const;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-inset ${styles[status]}`}
    >
      {status === "open" && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-500" />
        </span>
      )}
      {label}
    </span>
  );
}

function Endpoint({
  code,
  city,
  align,
}: {
  code: string;
  city: string;
  align?: "start" | "end";
}) {
  return (
    <div className={align === "end" ? "text-end" : "text-start"}>
      <p className="text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">
        {code}
      </p>
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{city}</p>
    </div>
  );
}

function PlaneLine({ open }: { open: boolean }) {
  return (
    <div className="relative mx-3 flex flex-1 items-center">
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-slate-300 to-transparent" />
      <span
        className={`mx-1 inline-flex h-7 w-7 items-center justify-center rounded-full ${
          open
            ? "bg-amber-100 text-amber-700 ring-1 ring-amber-200"
            : "bg-slate-100 text-slate-400 ring-1 ring-slate-200"
        }`}
      >
        <svg
          viewBox="0 0 24 24"
          fill="currentColor"
          className="h-3.5 w-3.5 rtl:-scale-x-100"
          aria-hidden
        >
          <path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V18l-2 1.5V21l3.5-1L15 21v-1.5L13 18v-4.5L21 16z" />
        </svg>
      </span>
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-slate-300 to-transparent" />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
        {label}
      </p>
      <p className="mt-0.5 text-sm font-medium text-slate-800">{value}</p>
    </div>
  );
}
