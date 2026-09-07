"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useDemo } from "../lib/store";

type Props = {
  title: string;
  children: React.ReactNode;
  ariaLabel?: string;
  align?: "start" | "end";
};

export default function InfoTip({ title, children, ariaLabel, align = "start" }: Props) {
  const { t } = useDemo();
  const [open, setOpen] = useState(false);
  const popoverId = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    }
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node;
      if (
        btnRef.current?.contains(target) ||
        popRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDocClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDocClick);
    };
  }, [open]);

  return (
    <span className="relative inline-flex">
      <button
        ref={btnRef}
        type="button"
        aria-label={ariaLabel ?? t("tip.aria")}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={(e) => {
          // Close when focus moves outside this widget.
          if (!popRef.current?.contains(e.relatedTarget as Node)) {
            setOpen(false);
          }
        }}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 bg-white text-[10px] font-bold text-slate-500 transition hover:border-slate-400 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-300"
      >
        <span aria-hidden>i</span>
      </button>
      <span
        ref={popRef}
        id={popoverId}
        role="tooltip"
        aria-hidden={!open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="absolute top-full z-50 mt-2 w-64 rounded-xl border border-slate-200 bg-white p-3 text-start shadow-xl ring-1 ring-slate-900/5 transition-all duration-150"
        style={{
          insetInlineStart: align === "start" ? 0 : "auto",
          insetInlineEnd: align === "end" ? 0 : "auto",
          opacity: open ? 1 : 0,
          transform: open ? "translateY(0) scale(1)" : "translateY(-4px) scale(0.98)",
          pointerEvents: open ? "auto" : "none",
        }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
          {title}
        </p>
        <div className="mt-1.5 text-xs leading-relaxed text-slate-600">{children}</div>
      </span>
    </span>
  );
}
