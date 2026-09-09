"use client";

import { useDemo } from "../lib/store";

export default function AgencyView() {
  const { user, metrics, bookings, opportunities, money } = useDemo();
  if (user?.active_organization_role !== "admin") return null;
  const wonCommission = metrics.monetary_totals.map((item) => money(item.won_commission, item.currency)).join(" + ") || "—";
  const potentialCommission = metrics.monetary_totals.map((item) => money(item.potential_commission, item.currency)).join(" + ") || "—";
  const statuses = [
    ["Open", metrics.open_opportunities], ["Contacted", metrics.contacted_opportunities],
    ["Won", metrics.won_opportunities], ["Declined", metrics.declined_opportunities],
    ["Expired", metrics.expired_opportunities], ["Closed", metrics.closed_opportunities],
  ] as const;
  return <>
    <header className="mb-7"><p className="text-xs font-semibold uppercase tracking-[.2em] text-indigo-600">Organization scope · admin only</p><h1 className="mt-2 text-3xl font-semibold">Organization performance</h1><p className="mt-1 text-sm text-slate-500">Backend-authorized data for every agent in the active organization.</p></header>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Metric label="Opportunities" value={metrics.total_opportunities} />
      <Metric label="Conversion" value={`${(metrics.conversion_rate * 100).toFixed(1)}%`} />
      <Metric label="Won commission" value={wonCommission} />
      <Metric label="Potential commission" value={potentialCommission} />
      <Metric label="Delivery successes" value={metrics.delivery_successes} />
      <Metric label="Delivery failures" value={metrics.delivery_failures} />
      <Metric label="Bookings" value={bookings.length} />
      <Metric label="Loaded opportunities" value={opportunities.length} />
    </div>
    <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-semibold">Opportunity lifecycle</h2><div className="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-6">{statuses.map(([name,count]) => <div key={name} className="rounded-xl bg-slate-50 p-3"><p className="text-xs text-slate-500">{name}</p><p className="mt-1 text-xl font-semibold">{count}</p></div>)}</div></section>
    <section className="mt-6 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"><header className="border-b border-slate-100 p-5"><h2 className="font-semibold">Per-agent breakdown</h2><p className="mt-1 text-xs text-slate-500">Each opportunity is counted once, independent of its tickets or recipients.</p></header><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr><th className="p-3">Agent</th><th className="p-3">Total</th><th className="p-3">Won</th><th className="p-3">Conversion</th><th className="p-3">Won commission</th></tr></thead><tbody>{metrics.per_agent.map((row) => <tr key={row.agent_id} className="border-t border-slate-100"><td className="p-3 font-medium">{row.agent_name}</td><td className="p-3">{row.total_opportunities}</td><td className="p-3">{row.won_opportunities}</td><td className="p-3">{(row.conversion_rate * 100).toFixed(1)}%</td><td className="p-3">{Object.entries(row.won_commission_by_currency).map(([currency, value]) => money(value, currency)).join(" + ") || "—"}</td></tr>)}</tbody></table></div></section>
  </>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>;
}
