"use client";

import { LOCALES } from "../lib/i18n";
import { useDemo } from "../lib/store";
import type { Job } from "../lib/types";

const LABELS: Record<Job["kind"], string> = {
  scan: "Mailbox scan",
  send: "Accommodation message",
  watch: "Email notifications",
  gmail_history: "New email check",
  incoming_email: "Incoming email",
};

function statusLabel(job: Job) {
  if (job.status === "retrying") return `Retrying automatically · attempt ${job.attempts}`;
  return job.status.charAt(0).toUpperCase() + job.status.slice(1);
}

function JobIcon({ status }: { status: Job["status"] }) {
  if (status === "completed") {
    return <span className="grid h-8 w-8 flex-none place-items-center rounded-full bg-emerald-100 text-sm font-bold text-emerald-700" aria-hidden="true">✓</span>;
  }
  if (status === "failed") {
    return <span className="grid h-8 w-8 flex-none place-items-center rounded-full bg-rose-100 text-sm font-bold text-rose-700" aria-hidden="true">!</span>;
  }
  return (
    <span className="grid h-8 w-8 flex-none place-items-center rounded-full bg-indigo-100 text-indigo-700" aria-hidden="true">
      <svg viewBox="0 0 24 24" className="h-4 w-4 animate-spin" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M20 12a8 8 0 1 1-2.35-5.65" strokeLinecap="round" />
      </svg>
    </span>
  );
}

function Progress({ job }: { job: Job }) {
  const seen = job.progress.messages_seen ?? 0;
  const analyzed = job.progress.messages_analyzed ?? 0;
  const skipped = job.progress.messages_skipped ?? 0;
  const processed = analyzed + skipped;
  const active = job.status === "queued" || job.status === "running" || job.status === "retrying";
  const failedPercent = seen > 0 ? Math.min(100, Math.round((processed / seen) * 100)) : 0;
  const percent = job.status === "completed" ? 100 : failedPercent;
  const text = active
    ? seen > 0 ? `${processed} of ${seen} discovered messages processed` : "Waiting for mailbox progress"
    : `${processed} messages processed`;

  return (
    <div className="mt-2.5">
      <div
        role="progressbar"
        aria-label={`${LABELS[job.kind]} progress`}
        aria-valuemin={active ? undefined : 0}
        aria-valuemax={active ? undefined : 100}
        aria-valuenow={active ? undefined : percent}
        aria-valuetext={text}
        className="h-1.5 overflow-hidden rounded-full bg-slate-200/70"
      >
        {active ? (
          <span className="activity-progress block h-full w-2/5 rounded-full bg-indigo-500" />
        ) : (
          <span
            className={`block h-full rounded-full transition-[width] duration-500 ${job.status === "failed" ? "bg-rose-500" : "bg-emerald-500"}`}
            style={{ width: `${percent}%` }}
          />
        )}
      </div>
      {(seen > 0 || job.kind === "scan" || job.kind === "incoming_email") && (
        <p className="mt-1.5 text-[11px] text-slate-500">
          {seen} seen · {analyzed} analyzed · {skipped} already processed
        </p>
      )}
    </div>
  );
}

export default function JobPanel() {
  const { jobs, retryJob, pushToast, lang } = useDemo();
  return (
    <section aria-label="Background activity" aria-live="polite">
      {jobs.slice(0, 6).map((job) => (
        <article key={job.id} className="border-b border-slate-100 px-4 py-3.5 last:border-0">
          <div className="flex gap-3">
            <JobIcon status={job.status} />
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold text-slate-900">{LABELS[job.kind]}</p>
                  <p className={`mt-0.5 text-[11px] font-medium ${job.status === "failed" ? "text-rose-600" : job.status === "completed" ? "text-emerald-700" : "text-indigo-600"}`}>
                    {statusLabel(job)}
                  </p>
                </div>
                {job.created_at && (
                  <time className="flex-none text-[10px] text-slate-400">
                    {new Intl.DateTimeFormat(LOCALES[lang], { timeStyle: "short" }).format(new Date(job.created_at))}
                  </time>
                )}
              </div>
              <Progress job={job} />
              {job.error && (
                <p role="alert" className="mt-2 rounded-lg bg-rose-50 px-2.5 py-2 text-[11px] leading-relaxed text-rose-700">
                  {job.error}{job.kind === "send" ? ". Check delivery records before sending again." : ""}
                </p>
              )}
              {job.result.deliveries?.map((delivery, index) => (
                <p key={index} className="mt-1.5 text-[11px] text-slate-600">
                  {delivery.destination_snapshot}: {delivery.status}{delivery.error_message ? ` · ${delivery.error_message}` : ""}
                </p>
              ))}
              {job.status === "failed" && job.kind !== "send" && (
                <button
                  type="button"
                  className="mt-2 text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                  onClick={() => void retryJob(job.id).catch((error) => pushToast({ tone: "error", title: "Retry failed", body: error.message }))}
                >
                  Retry job
                </button>
              )}
            </div>
          </div>
        </article>
      ))}
    </section>
  );
}
