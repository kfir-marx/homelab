"use client";

import { useMemo, useState } from "react";
import { useDemo } from "../lib/store";
import type { Flight } from "../lib/types";
import AddFlightModal from "./AddFlightModal";
import FlightCard from "./FlightCard";
import FlightDetailModal from "./FlightDetailModal";

export default function AgentView() {
  const { flights, activeAgentId, t, fmt } = useDemo();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const myFlights = useMemo(
    () => flights.filter((f) => f.agentId === activeAgentId),
    [flights, activeAgentId],
  );

  const open = useMemo(
    () => myFlights.filter((f) => f.status === "open"),
    [myFlights],
  );
  const handled = useMemo(
    () => myFlights.filter((f) => f.status !== "open"),
    [myFlights],
  );

  const sortedOpen = useMemo(() => sortByDeparture(open), [open]);
  const sortedHandled = useMemo(() => sortByDeparture(handled), [handled]);

  const potentialUsd = useMemo(
    () => open.reduce((s, f) => s + f.hotelCostUsd, 0),
    [open],
  );

  const selected = useMemo(
    () => myFlights.find((f) => f.id === selectedId) ?? null,
    [myFlights, selectedId],
  );

  const subtitle =
    open.length === 1
      ? t("agent.subtitle.one", { commission: fmt.usd(potentialUsd * 0.01, { decimals: true }) })
      : t("agent.subtitle.other", {
          count: open.length,
          commission: fmt.usd(potentialUsd * 0.01, { decimals: true }),
        });

  return (
    <>
      <section className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            {t("agent.eyebrow")}
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
            {t("agent.title")}
          </h1>
          <p className="mt-1.5 text-sm text-slate-500">{subtitle}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm">
            <Pill tone="amber" count={open.length} label={t("agent.pill.upsellAvailable")} />
            <Pill tone="slate" count={handled.length} label={t("agent.pill.handled")} />
          </div>
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="group inline-flex items-center gap-2 rounded-2xl bg-gradient-to-r from-amber-500 to-orange-500 px-4 py-2.5 text-sm font-semibold text-white shadow-[0_8px_24px_-8px_rgba(234,88,12,0.55)] transition hover:from-amber-400 hover:to-orange-400 active:scale-[0.99]"
          >
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-white/20 transition group-hover:bg-white/30">
              <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden>
                <path d="M10 4v12M4 10h12" strokeLinecap="round" />
              </svg>
            </span>
            {t("newFlight.btn.add")}
          </button>
        </div>
      </section>

      {sortedOpen.length > 0 && (
        <SectionHeader
          eyebrow={t("agent.section.open.eyebrow")}
          title={t("agent.section.open.title")}
          hint={t("agent.section.open.hint")}
        />
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {sortedOpen.map((f, i) => (
          <FlightCard
            key={f.id}
            flight={f}
            stagger={i}
            onOpen={() => setSelectedId(f.id)}
          />
        ))}
      </div>

      {sortedHandled.length > 0 && (
        <>
          <div className="mt-12">
            <SectionHeader
              eyebrow={t("agent.section.closed.eyebrow")}
              title={t("agent.section.closed.title")}
              hint={t("agent.section.closed.hint")}
            />
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {sortedHandled.map((f, i) => (
              <FlightCard
                key={f.id}
                flight={f}
                stagger={i}
                onOpen={() => setSelectedId(f.id)}
              />
            ))}
          </div>
        </>
      )}

      <FlightDetailModal flight={selected} onClose={() => setSelectedId(null)} />
      <AddFlightModal open={addOpen} onClose={() => setAddOpen(false)} />
    </>
  );
}

function sortByDeparture(flights: Flight[]) {
  return [...flights].sort((a, b) => a.departureDate.localeCompare(b.departureDate));
}

function Pill({
  tone,
  count,
  label,
}: {
  tone: "amber" | "slate";
  count: number;
  label: string;
}) {
  const toneClass =
    tone === "amber"
      ? "bg-amber-50 text-amber-900 ring-amber-200"
      : "bg-slate-50 text-slate-700 ring-slate-200";
  return (
    <div
      className={`flex items-center gap-2 rounded-xl px-3 py-1.5 ring-1 ring-inset ${toneClass}`}
    >
      <span className="text-sm font-semibold tabular-nums">{count}</span>
      <span className="text-xs font-medium">{label}</span>
    </div>
  );
}

function SectionHeader({
  eyebrow,
  title,
  hint,
}: {
  eyebrow: string;
  title: string;
  hint: string;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
          {eyebrow}
        </p>
        <h2 className="mt-1 text-lg font-semibold tracking-tight text-slate-800">
          {title}
        </h2>
      </div>
      <p className="hidden text-xs text-slate-500 sm:block">{hint}</p>
    </div>
  );
}
