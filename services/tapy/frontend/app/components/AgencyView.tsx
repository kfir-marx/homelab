"use client";

import { useDemo } from "../lib/store";

export default function AgencyView() {
  const { user, metrics, bookings, opportunities } = useDemo();
  if (user?.active_organization_role !== "admin") return null;
  const statuses = [
    ["Open", metrics.open_opportunities], ["Contacted", metrics.contacted_opportunities],
    ["Declined", metrics.declined_opportunities],
    ["Expired", metrics.expired_opportunities], ["Closed", metrics.closed_opportunities],
  ] as const;
  return <>
    <header className="mb-7"><p className="text-xs font-semibold uppercase tracking-[.2em] text-indigo-600">Organization scope · admin only</p><h1 className="mt-2 text-3xl font-semibold">Organization performance</h1><p className="mt-1 text-sm text-slate-500">Backend-authorized data for every agent in the active organization.</p></header>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Metric label="Opportunities" value={metrics.total_opportunities} />
      <Metric label="Delivery successes" value={metrics.delivery_successes} />
      <Metric label="Delivery failures" value={metrics.delivery_failures} />
      <Metric label="Bookings" value={bookings.length} />
      <Metric label="Loaded opportunities" value={opportunities.length} />
    </div>
    <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-semibold">Opportunity lifecycle</h2><div className="mt-4 grid grid-cols-3 gap-3 sm:grid-cols-6">{statuses.map(([name,count]) => <div key={name} className="rounded-xl bg-slate-50 p-3"><p className="text-xs text-slate-500">{name}</p><p className="mt-1 text-xl font-semibold">{count}</p></div>)}</div></section>
    <p className="mt-6 text-sm text-slate-500">Attributed booking outcomes and commission await partner reporting.</p>
  </>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>;
}
