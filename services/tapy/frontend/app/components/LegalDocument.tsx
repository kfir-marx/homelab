import Link from "next/link";
import type { ReactNode } from "react";

export default function LegalDocument({
  title,
  summary,
  children,
}: {
  title: string;
  summary: string;
  children: ReactNode;
}) {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-800 sm:px-6 sm:py-12">
      <article className="mx-auto max-w-3xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl shadow-slate-200/50">
        <header className="border-b border-slate-200 bg-slate-950 px-6 py-8 text-white sm:px-10 sm:py-10">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-white text-sm font-bold text-slate-950">
              T
            </span>
            Tapy
          </Link>
          <h1 className="mt-7 text-3xl font-semibold tracking-tight sm:text-4xl">
            {title}
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300 sm:text-base">
            {summary}
          </p>
          <p className="mt-5 text-xs font-medium uppercase tracking-[0.16em] text-slate-400">
            Effective September 8, 2026
          </p>
        </header>

        <div className="legal-document px-6 py-8 sm:px-10 sm:py-10">{children}</div>

        <footer className="flex flex-wrap items-center justify-between gap-4 border-t border-slate-200 bg-slate-50 px-6 py-5 text-sm sm:px-10">
          <nav aria-label="Legal pages" className="flex gap-5 font-medium text-slate-600">
            <Link href="/privacy" className="hover:text-slate-950">
              Privacy
            </Link>
            <Link href="/terms" className="hover:text-slate-950">
              Terms
            </Link>
          </nav>
          <Link href="/" className="font-semibold text-indigo-600 hover:text-indigo-500">
            Return to Tapy
          </Link>
        </footer>
      </article>
    </main>
  );
}
