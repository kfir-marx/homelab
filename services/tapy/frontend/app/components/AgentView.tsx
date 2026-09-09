"use client";

import { useState } from "react";
import { useDemo } from "../lib/store";
import type { Opportunity } from "../lib/types";
import JobPanel from "./JobPanel";

export default function AgentView() {
  const { opportunities, loading, error, setView, refresh } = useDemo();
  const open = opportunities.filter((item) => item.status === "open");
  return <>
    <header className="mb-7"><h1 className="text-3xl font-semibold">Accommodation opportunities</h1>
      <p className="mt-2 text-sm text-slate-500">Flights found in your connected email without matching accommodation.</p>
      <button className="mt-3 text-sm text-indigo-600" onClick={() => setView("settings")}>Manage email and scans</button>
    </header>
    {error && <p role="alert">{error} <button onClick={() => void refresh()}>Retry refresh</button></p>}
    <JobPanel />
    {loading ? <p role="status">Loading opportunities…</p> : <div className="mt-6 grid gap-4 lg:grid-cols-2">
      {open.map((item) => <OpportunityCard key={item.id} opportunity={item} />)}
      {!open.length && <p className="rounded-xl border border-dashed p-8 text-slate-500">No actionable opportunities. Connect your inbox and run a scan to check for flights without accommodation.</p>}
    </div>}
    <p className="mt-6 text-sm text-slate-500">Booking outcomes and commission will be available only through partner reporting.</p>
  </>;
}

function OpportunityCard({ opportunity }: { opportunity: Opportunity }) {
  const { updateOpportunity, updateRecipient, sendOpportunity, pushToast } = useDemo();
  const [working, setWorking] = useState(false);
  const details = opportunity.flight_details;
  const selected = opportunity.recipients.filter((item) => item.selection_status === "selected" && item.contact_point_id);
  async function act(action: () => Promise<void>, title: string) {
    setWorking(true);
    try { await action(); pushToast({ tone: "success", title, body: "" }); }
    catch (error) { pushToast({ tone: "error", title: "Action failed", body: error instanceof Error ? error.message : "Try again." }); }
    finally { setWorking(false); }
  }
  return <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
    <h2 className="text-xl font-semibold">{opportunity.destination}</h2>
    {details.booking_reference && <p className="text-sm text-slate-500">Booking {details.booking_reference}</p>}
    {!!details.pnrs?.length && <p className="text-sm text-slate-500">PNR: {details.pnrs.join(", ")}</p>}
    <p className="mt-3 font-medium">{details.travelers?.join(", ")}</p>
    {opportunity.service_start && <p className="mt-2 text-sm">Stay from {(details.start || opportunity.service_start).slice(0, 10)}{opportunity.service_end ? ` to ${(details.end || opportunity.service_end).slice(0, 10)}` : ""}</p>}
    {details.segments?.map((segment, index) => <p className="mt-1 text-sm text-slate-600" key={index}>
      {[segment.airline, segment.flight_number, segment.origin_code && segment.destination_code ? `${segment.origin_code} → ${segment.destination_code}` : null, segment.departure_at?.replace("T", " ")].filter(Boolean).join(" · ")}
    </p>)}
    <section className="mt-4 border-t pt-3"><h3 className="text-sm font-semibold">Send to</h3>
      {opportunity.recipients.map((recipient) => {
        const contacts = recipient.person.contacts.filter((item) => item.channel === "phone" || item.channel === "whatsapp");
        return <label key={recipient.id} className="mt-2 block text-sm">{recipient.person.display_name}
          <select disabled={working} aria-label={`Contact for ${recipient.person.display_name}`} className="ms-2 rounded border p-2" value={recipient.selection_status === "selected" ? recipient.contact_point_id || "" : ""}
            onChange={(event) => void act(() => updateRecipient(opportunity.id, recipient.person.id, event.target.value || null, event.target.value ? "selected" : "excluded"), "Recipient updated")}>
            <option value="">{contacts.length ? "Do not send" : "Phone or WhatsApp contact missing"}</option>
            {contacts.map((contact) => <option key={contact.id} value={contact.id}>{contact.display_value}</option>)}
          </select>
        </label>;
      })}
      {!opportunity.recipients.length && <p className="text-sm text-slate-500">No contact details found in the email.</p>}
    </section>
    <div className="mt-5 flex gap-3">
      <button disabled={working || !selected.length} onClick={() => void act(() => sendOpportunity(opportunity.id), "Message queued")} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-40">{working ? "Working…" : "Send accommodation link"}</button>
      <button disabled={working} onClick={() => void act(() => updateOpportunity(opportunity, "declined"), "Opportunity dismissed")} className="rounded-lg border px-4 py-2 text-sm">Dismiss</button>
    </div>
  </article>;
}
