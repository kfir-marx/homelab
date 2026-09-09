"use client";
import { useDemo } from "../lib/store";

export default function JobPanel() {
  const { jobs, retryJob, pushToast } = useDemo();
  return <section aria-label="Background activity" aria-live="polite" className="space-y-2">
    {jobs.slice(0, 5).map((job) => <div key={job.id} className="rounded-lg bg-slate-50 p-3 text-sm">
      <strong>{job.kind === "scan" ? "Mailbox scan" : job.kind === "send" ? "Accommodation message" : "Email notifications"}</strong>: {job.status}
      {job.progress.messages_seen !== undefined && <span> · {job.progress.messages_seen} seen, {job.progress.messages_analyzed} analyzed, {job.progress.messages_skipped} already processed</span>}
      {job.status === "retrying" && <span> · retry {job.attempts}, retrying automatically</span>}
      {job.error && <p role="alert">{job.error}{job.kind === "send" ? ". Check delivery records before sending again." : ""}</p>}
      {job.result.deliveries?.map((delivery, index) => <p key={index}>{delivery.destination_snapshot}: {delivery.status}{delivery.error_message ? ` · ${delivery.error_message}` : ""}</p>)}
      {job.status === "failed" && job.kind === "scan" && <button className="ms-2 text-indigo-600" onClick={() => void retryJob(job.id).catch((error) => pushToast({ tone: "error", title: "Retry failed", body: error.message }))}>Retry scan</button>}
    </div>)}
  </section>;
}
