"use client";

import { useMemo } from "react";
import { useAgencyMetrics, useDemo } from "../lib/store";
import CountUp from "./CountUp";
import InfoTip from "./InfoTip";

export default function AgencyView() {
  const metrics = useAgencyMetrics();
  const { flights, agents, t, fmt } = useDemo();

  const perAgent = useMemo(() => {
    return agents
      .map((a) => {
        const own = flights.filter((f) => f.agentId === a.id);
        const upsold = own.filter((f) => f.status === "upsold");
        const declined = own.filter((f) => f.status === "declined");
        const past = own.filter((f) => f.status === "past");
        const open = own.filter((f) => f.status === "open");
        const handled = upsold.length + declined.length + past.length;
        const rate = handled === 0 ? 0 : upsold.length / handled;
        const revenue = upsold.reduce((s, f) => s + f.hotelCostUsd, 0) * 0.01;
        const potential = open.reduce((s, f) => s + f.hotelCostUsd, 0) * 0.01;
        return {
          agent: a,
          totalBookings: own.length,
          upsold: upsold.length,
          rate,
          revenue,
          potential,
        };
      })
      .sort((a, b) => b.revenue - a.revenue);
  }, [agents, flights]);

  const statusMix = useMemo(
    () => [
      { key: "upsold", label: t("agency.mix.upsold"), count: metrics.upsoldCount, tone: "bg-emerald-500" },
      { key: "open", label: t("agency.mix.open"), count: metrics.openCount, tone: "bg-amber-400" },
      { key: "declined", label: t("agency.mix.declined"), count: metrics.declinedCount, tone: "bg-slate-400" },
      { key: "past", label: t("agency.mix.past"), count: metrics.pastCount, tone: "bg-slate-300" },
    ],
    [metrics, t],
  );
  const statusTotal = statusMix.reduce((s, e) => s + e.count, 0) || 1;

  const potentialDelta =
    metrics.openCount === 1
      ? t("agency.metric.potentialProfit.delta.one")
      : t("agency.metric.potentialProfit.delta.other", { count: metrics.openCount });

  return (
    <>
      <section className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
            {t("agency.eyebrow")}
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
            {t("agency.title")}
          </h1>
          <p className="mt-1.5 text-sm text-slate-500">{t("agency.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 shadow-sm">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          <span className="text-xs font-medium text-slate-700">{t("agency.live")}</span>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
        <MetricCard
          eyebrow={t("agency.metric.closingRate")}
          tip={
            <InfoTip title={t("tip.closingRate.title")}>
              {t("tip.closingRate.body")}
            </InfoTip>
          }
          value={
            <CountUp value={metrics.closingRate} format={(n) => fmt.percent(n, 1)} />
          }
          delta={t("agency.metric.closingRate.delta", {
            upsold: metrics.upsoldCount,
            total: metrics.totalFlights,
          })}
          accent="indigo"
          progress={metrics.closingRate}
        />
        <MetricCard
          eyebrow={t("agency.metric.netProfit")}
          tip={
            <InfoTip title={t("tip.netProfit.title")}>
              {t("tip.netProfit.body")}
            </InfoTip>
          }
          value={
            <CountUp
              value={metrics.netProfit}
              format={(n) => fmt.usd(n, { decimals: true })}
            />
          }
          delta={t("agency.metric.netProfit.delta", {
            revenue: fmt.usd(metrics.totalHotelRevenue),
          })}
          accent="emerald"
          progress={
            metrics.netProfit / Math.max(1, metrics.netProfit + metrics.potentialProfit)
          }
        />
        <MetricCard
          eyebrow={t("agency.metric.potentialProfit")}
          tip={
            <InfoTip title={t("tip.potentialProfit.title")}>
              {t("tip.potentialProfit.body")}
            </InfoTip>
          }
          value={
            <CountUp
              value={metrics.potentialProfit}
              format={(n) => fmt.usd(n, { decimals: true })}
            />
          }
          delta={potentialDelta}
          accent="amber"
          progress={
            metrics.potentialProfit /
            Math.max(1, metrics.netProfit + metrics.potentialProfit)
          }
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-5">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6 lg:col-span-3">
          <div className="flex items-end justify-between">
            <div>
              <div className="flex items-center gap-1.5">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                  {t("agency.leaderboard.eyebrow")}
                </p>
                <InfoTip title={t("tip.leaderboard.title")}>
                  {t("tip.leaderboard.body", {
                    example: t("agency.leaderboard.row", {
                      upsold: perAgent[0]?.upsold ?? 0,
                      rate: fmt.percent(perAgent[0]?.rate ?? 0, 0),
                    }),
                    upsold: perAgent[0]?.upsold ?? 0,
                    rate: fmt.percent(perAgent[0]?.rate ?? 0, 0),
                  })}
                </InfoTip>
              </div>
              <h2 className="mt-1 text-base font-semibold text-slate-900">
                {t("agency.leaderboard.title")}
              </h2>
            </div>
            <span className="text-xs text-slate-500">
              {t("agency.leaderboard.tracked", { count: flights.length })}
            </span>
          </div>

          <div className="mt-5 divide-y divide-slate-100">
            {perAgent.map((row, idx) => (
              <div key={row.agent.id} className="flex items-center gap-3 py-3.5 sm:gap-4">
                <span className="w-5 text-center text-xs font-semibold tabular-nums text-slate-400">
                  {idx + 1}
                </span>
                <span
                  className={`flex h-9 w-9 flex-none items-center justify-center rounded-full bg-gradient-to-br ${row.agent.avatarTint} text-xs font-semibold text-white shadow-sm`}
                >
                  {row.agent.initials}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="truncate text-sm font-semibold text-slate-900">
                      {row.agent.name}
                    </p>
                    <p className="text-sm font-semibold text-slate-900 tabular-nums">
                      <CountUp
                        value={row.revenue}
                        format={(n) => fmt.usd(n, { decimals: true })}
                      />
                    </p>
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="absolute inset-y-0 rounded-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-[width] duration-700 ease-out"
                        style={{
                          insetInlineStart: 0,
                          width: `${Math.min(100, (row.revenue / Math.max(1, perAgent[0].revenue)) * 100)}%`,
                        }}
                      />
                    </div>
                    <p className="w-28 text-end text-[11px] text-slate-500">
                      {t("agency.leaderboard.row", {
                        upsold: row.upsold,
                        rate: fmt.percent(row.rate, 0),
                      })}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6 lg:col-span-2">
          <div className="flex items-center gap-1.5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
              {t("agency.mix.eyebrow")}
            </p>
            <InfoTip title={t("tip.mix.title")} align="end">
              <p>{t("tip.mix.body")}</p>
              <ul className="mt-2 space-y-1.5">
                <li>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle" />
                  <span className="ms-2 align-middle">{t("tip.mix.upsold")}</span>
                </li>
                <li>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400 align-middle" />
                  <span className="ms-2 align-middle">{t("tip.mix.open")}</span>
                </li>
                <li>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-400 align-middle" />
                  <span className="ms-2 align-middle">{t("tip.mix.declined")}</span>
                </li>
                <li>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-300 align-middle" />
                  <span className="ms-2 align-middle">{t("tip.mix.past")}</span>
                </li>
              </ul>
            </InfoTip>
          </div>
          <h2 className="mt-1 text-base font-semibold text-slate-900">
            {t("agency.mix.title")}
          </h2>

          <div className="mt-5 h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
            <div className="flex h-full w-full">
              {statusMix.map((s) => (
                <div
                  key={s.key}
                  className={`${s.tone} transition-[flex-grow] duration-700 ease-out`}
                  style={{ flexGrow: s.count / statusTotal }}
                />
              ))}
            </div>
          </div>

          <ul className="mt-5 space-y-3.5">
            {statusMix.map((s) => (
              <li key={s.key} className="flex items-center gap-3">
                <span className={`h-2.5 w-2.5 flex-none rounded-full ${s.tone}`} />
                <span className="flex-1 text-sm text-slate-700">{s.label}</span>
                <span className="text-sm font-semibold tabular-nums text-slate-900">
                  <CountUp value={s.count} format={(n) => fmt.number(n)} />
                </span>
                <span className="w-14 text-end text-xs text-slate-500 tabular-nums">
                  {fmt.percent(s.count / statusTotal, 0)}
                </span>
              </li>
            ))}
          </ul>

          <div className="mt-6 rounded-xl border border-dashed border-slate-200 bg-slate-50/60 p-4 text-xs leading-relaxed text-slate-600">
            {t("agency.mix.footer", {
              amount: fmt.usd(metrics.potentialProfit, { decimals: true }),
            })}
          </div>
        </section>
      </div>
    </>
  );
}

function MetricCard({
  eyebrow,
  tip,
  value,
  delta,
  accent,
  progress,
}: {
  eyebrow: string;
  tip?: React.ReactNode;
  value: React.ReactNode;
  delta: string;
  accent: "indigo" | "emerald" | "amber";
  progress: number;
}) {
  const accentMap = {
    indigo: {
      ring: "ring-indigo-100",
      bar: "bg-gradient-to-r from-indigo-500 to-violet-500",
      glow: "from-indigo-200/40",
    },
    emerald: {
      ring: "ring-emerald-100",
      bar: "bg-gradient-to-r from-emerald-500 to-teal-500",
      glow: "from-emerald-200/40",
    },
    amber: {
      ring: "ring-amber-100",
      bar: "bg-gradient-to-r from-amber-500 to-orange-500",
      glow: "from-amber-200/40",
    },
  }[accent];

  return (
    <div
      className={`relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ring-1 ring-inset sm:p-6 ${accentMap.ring}`}
    >
      <div
        aria-hidden
        className={`pointer-events-none absolute -top-16 h-40 w-40 rounded-full bg-gradient-to-br ${accentMap.glow} to-transparent blur-2xl`}
        style={{ insetInlineEnd: "-4rem" }}
      />
      <div className="flex items-center gap-1.5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
          {eyebrow}
        </p>
        {tip}
      </div>
      <p className="mt-3 text-4xl font-semibold tracking-tight text-slate-900 tabular-nums">
        {value}
      </p>
      <p className="mt-2 text-xs text-slate-500">{delta}</p>
      <div className="mt-5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${accentMap.bar} transition-[width] duration-700 ease-out`}
          style={{ width: `${Math.max(4, Math.min(100, progress * 100))}%` }}
        />
      </div>
    </div>
  );
}
