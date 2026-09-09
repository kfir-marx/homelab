"use client";

import { useState } from "react";
import { useDemo } from "../lib/store";
import type { Opportunity, OpportunityStatus } from "../lib/types";

export default function AgentView() {
  const { bookings, opportunities, metrics, money, addBooking, updateOpportunity, updateRecipient, sendOpportunity, pushToast } = useDemo();
  const [showForm, setShowForm] = useState(false);
  const potential = metrics.monetary_totals.map((item) => money(item.potential_commission, item.currency)).join(" + ") || "—";
  return (
    <>
      <header className="mb-7 flex items-end justify-between gap-4">
        <div><p className="text-xs font-semibold uppercase tracking-[.2em] text-slate-500">Personal scope</p><h1 className="mt-2 text-3xl font-semibold">My opportunities</h1><p className="mt-1 text-sm text-slate-500">Only bookings and opportunities assigned to you.</p></div>
        <button onClick={() => setShowForm((value) => !value)} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white">{showForm ? "Cancel" : "Add booking"}</button>
      </header>
      <MetricStrip values={[
        ["Total", metrics.total_opportunities], ["Open", metrics.open_opportunities],
        ["Won", metrics.won_opportunities], ["Conversion", `${(metrics.conversion_rate * 100).toFixed(1)}%`],
        ["Potential commission", potential],
      ]} />
      {showForm && <BookingForm onDone={() => setShowForm(false)} />}
      <div className="mt-7 grid gap-4 lg:grid-cols-2">
        {opportunities.map((item) => <OpportunityCard key={item.id} opportunity={item} />)}
        {!opportunities.length && <p className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-500">No opportunities in your personal scope.</p>}
      </div>
      <p className="mt-8 text-xs text-slate-500">{bookings.length} assigned bookings. Sending changes an open opportunity to contacted; only an explicit won action counts as conversion.</p>
    </>
  );

  function OpportunityCard({ opportunity }: { opportunity: Opportunity }) {
    const selected = opportunity.recipients.filter((item) => item.selection_status === "selected");
    const reference = bookings.find((item) => item.id === opportunity.booking_id)?.external_reference;
    async function act(action: () => Promise<void>, success: string) {
      try { await action(); pushToast({ tone: "success", title: success, body: reference || opportunity.id }); }
      catch (error) { pushToast({ tone: "error", title: "Action failed", body: error instanceof Error ? error.message : "Try again." }); }
    }
    return <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between"><div><p className="text-xs font-semibold uppercase text-indigo-600">{opportunity.product_type} · {opportunity.destination || "Destination pending"}</p><h2 className="mt-1 font-semibold">{reference || opportunity.id}</h2></div><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold">{opportunity.status}</span></div>
      <p className="mt-4 text-sm text-slate-600">Tickets: {opportunity.tickets.map((item) => item.ticket_number).join(", ") || "none"}</p>
      <p className="mt-1 text-sm text-slate-600">Potential: {money(opportunity.potential_commission, opportunity.currency)} commission</p>
      <section className="mt-4 border-t border-slate-100 pt-4"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Recipients ({selected.length} selected)</p>
        {opportunity.recipients.length ? opportunity.recipients.map((recipient) => {
          const contact = recipient.person.contacts.find((item) => item.id === recipient.contact_point_id) || recipient.person.contacts[0];
          return <div key={recipient.id} className="mb-2 flex items-center justify-between gap-3 rounded-xl bg-slate-50 p-3"><div><p className="text-sm font-medium">{recipient.person.display_name}</p><p className="text-xs text-slate-500">{contact?.display_value || "No contact"} · {recipient.selection_method}</p></div><select aria-label={`Recipient status for ${recipient.person.display_name}`} value={recipient.selection_status} onChange={(event) => void act(() => updateRecipient(opportunity.id, recipient.person.id, contact?.id || null, event.target.value), "Recipient updated")} className="rounded-lg border border-slate-200 bg-white p-2 text-xs"><option value="selected">Selected</option><option value="candidate">Candidate</option><option value="excluded">Excluded</option><option value="needs_contact">Needs contact</option></select></div>;
        }) : <p className="text-sm text-slate-500">No recipient candidates. Add them through the opportunity API.</p>}
      </section>
      <div className="mt-4 flex flex-wrap gap-2"><button disabled={!selected.length} onClick={() => void act(() => sendOpportunity(opportunity.id), "Delivery batch completed")} className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">Send to selected</button>{(["open", "won", "declined"] as OpportunityStatus[]).map((status) => <button key={status} disabled={opportunity.status === status} onClick={() => void act(() => updateOpportunity(opportunity, status), `Marked ${status}`)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold capitalize disabled:opacity-40">{status}</button>)}</div>
    </article>;
  }

  function BookingForm({ onDone }: { onDone: () => void }) {
    const [working, setWorking] = useState(false);
    async function submit(event: React.FormEvent<HTMLFormElement>) {
      event.preventDefault(); setWorking(true); const data = new FormData(event.currentTarget);
      try { await addBooking({ reference: String(data.get("reference")), passenger: String(data.get("passenger")), phone: String(data.get("phone")), email: String(data.get("email")), pnr: String(data.get("pnr")), ticketNumber: String(data.get("ticket")), origin: String(data.get("origin")).toUpperCase(), destination: String(data.get("destination")).toUpperCase(), departureAt: new Date(String(data.get("departure"))).toISOString(), arrivalAt: new Date(String(data.get("arrival"))).toISOString(), amount: Number(data.get("amount")), currency: String(data.get("currency")).toUpperCase() }); pushToast({ tone: "success", title: "Booking created", body: "Ticket and one-ticket opportunity created." }); onDone(); }
      catch (error) { pushToast({ tone: "error", title: "Could not create booking", body: error instanceof Error ? error.message : "Try again." }); } finally { setWorking(false); }
    }
    return <form onSubmit={submit} className="mt-6 grid gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:grid-cols-3">{[["reference","Booking reference","text"],["passenger","Passenger","text"],["phone","WhatsApp (E.164)","tel"],["email","Email fallback","email"],["pnr","PNR","text"],["ticket","Ticket number","text"],["origin","Origin code","text"],["destination","Destination code","text"],["departure","Departure","datetime-local"],["arrival","Arrival","datetime-local"],["amount","Ticket amount","number"],["currency","ISO currency","text"]].map(([name,label,type]) => <label key={name} className="text-xs font-semibold text-slate-600">{label}<input name={name} type={type} required={!(["reference","phone","email"] as string[]).includes(name)} defaultValue={name === "currency" ? "USD" : undefined} step={type === "number" ? ".01" : undefined} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" /></label>)}<button disabled={working} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white sm:col-span-3">{working ? "Creating…" : "Create booking, ticket and opportunity"}</button></form>;
  }
}

function MetricStrip({ values }: { values: Array<[string, string | number]> }) {
  return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{values.map(([label,value]) => <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold">{value}</p></div>)}</div>;
}
