"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useDemo } from "../lib/store";

type Props = {
  open: boolean;
  onClose: () => void;
};

const INPUT_CLASS =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 shadow-sm transition placeholder:text-slate-400 focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-200/70";

export default function AddFlightModal({ open, onClose }: Props) {
  const { addFlight, pushToast, t } = useDemo();
  const [mounted, setMounted] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const firstInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [origin, setOrigin] = useState("");
  const [destination, setDestination] = useState("");
  const [depart, setDepart] = useState("");
  const [ret, setRet] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");

  function resetForm() {
    setName("");
    setOrigin("");
    setDestination("");
    setDepart("");
    setRet("");
    setEmail("");
    setPhone("");
  }

  useEffect(() => {
    if (open) {
      if (closeTimer.current) {
        window.clearTimeout(closeTimer.current);
        closeTimer.current = null;
      }
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          setMounted(true);
          firstInputRef.current?.focus();
        }),
      );
    } else if (mounted) {
      requestAnimationFrame(() => setMounted(false));
      closeTimer.current = window.setTimeout(() => {
        resetForm();
        closeTimer.current = null;
      }, 280);
    }
    return () => {
      if (closeTimer.current) {
        window.clearTimeout(closeTimer.current);
        closeTimer.current = null;
      }
    };
  }, [open, mounted]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const canSubmit = useMemo(
    () =>
      name.trim().length > 0 &&
      origin.trim().length > 0 &&
      destination.trim().length > 0 &&
      depart.length > 0 &&
      ret.length > 0 &&
      depart <= ret &&
      email.trim().includes("@") &&
      phone.trim().length > 0,
    [name, origin, destination, depart, ret, email, phone],
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    const f = addFlight({
      passengerName: name.trim(),
      originCity: origin.trim(),
      destinationCity: destination.trim(),
      departureDate: depart,
      returnDate: ret,
      email: email.trim(),
      phone: phone.trim(),
    });
    pushToast({
      tone: "success",
      title: t("newFlight.toast.title"),
      body: t("newFlight.toast.body", {
        name: f.passengerName,
        origin: f.originCity,
        destination: f.destinationCity,
      }),
    });
    onClose();
  }

  if (!open && !mounted) return null;

  return (
    <div
      aria-hidden={!mounted}
      className="fixed inset-0 z-50 flex items-center justify-center px-4 py-8"
      onClick={onClose}
    >
      <div
        className={`absolute inset-0 bg-slate-900/45 backdrop-blur-sm transition-opacity duration-300 ${
          mounted ? "opacity-100" : "opacity-0"
        }`}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("newFlight.modal.title")}
        onClick={(e) => e.stopPropagation()}
        className="relative flex max-h-[min(92vh,46rem)] w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{
          opacity: mounted ? 1 : 0,
          transform: mounted
            ? "translateY(0) scale(1)"
            : "translateY(10px) scale(0.97)",
        }}
      >
        <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
          <header className="relative flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-5 sm:px-7 sm:py-6">
            <span
              aria-hidden
              className="pointer-events-none absolute -top-12 h-32 w-32 rounded-full bg-amber-300/25 blur-3xl"
              style={{ insetInlineEnd: "-2rem" }}
            />
            <div className="relative">
              <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-amber-700">
                {t("newFlight.modal.eyebrow")}
              </p>
              <h2 className="mt-1.5 text-xl font-semibold tracking-tight text-slate-900">
                {t("newFlight.modal.title")}
              </h2>
              <p className="mt-1.5 max-w-md text-xs leading-relaxed text-slate-500">
                {t("newFlight.modal.subtitle")}
              </p>
            </div>
            <button
              type="button"
              aria-label={t("newFlight.aria.close")}
              onClick={onClose}
              className="relative rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <svg
                viewBox="0 0 20 20"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
              </svg>
            </button>
          </header>

          <div className="flex-1 overflow-y-auto px-5 py-5 sm:px-7 sm:py-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label={t("newFlight.field.name")} className="sm:col-span-2">
                <input
                  ref={firstInputRef}
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("newFlight.placeholder.name")}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("newFlight.field.from")}>
                <input
                  type="text"
                  required
                  value={origin}
                  onChange={(e) => setOrigin(e.target.value)}
                  placeholder={t("newFlight.placeholder.from")}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("newFlight.field.to")}>
                <input
                  type="text"
                  required
                  value={destination}
                  onChange={(e) => setDestination(e.target.value)}
                  placeholder={t("newFlight.placeholder.to")}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("newFlight.field.depart")}>
                <input
                  type="date"
                  required
                  value={depart}
                  onChange={(e) => setDepart(e.target.value)}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("newFlight.field.return")}>
                <input
                  type="date"
                  required
                  value={ret}
                  min={depart || undefined}
                  onChange={(e) => setRet(e.target.value)}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("newFlight.field.email")}>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t("newFlight.placeholder.email")}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("newFlight.field.phone")}>
                <input
                  type="tel"
                  required
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder={t("newFlight.placeholder.phone")}
                  className={INPUT_CLASS}
                />
              </Field>
            </div>
          </div>

          <footer className="border-t border-slate-100 bg-slate-50/60 px-5 py-4 sm:px-7 sm:py-5">
            <div className="flex gap-3">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:border-slate-300 hover:bg-slate-50 active:scale-[0.99]"
              >
                {t("newFlight.btn.cancel")}
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="flex-[1.6] rounded-xl bg-gradient-to-r from-amber-500 to-orange-500 px-4 py-3 text-sm font-semibold text-white shadow-[0_8px_24px_-8px_rgba(234,88,12,0.6)] transition hover:from-amber-400 hover:to-orange-400 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none disabled:hover:from-amber-500 disabled:hover:to-orange-500"
              >
                <span className="inline-flex items-center justify-center gap-2">
                  <svg
                    viewBox="0 0 20 20"
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                  >
                    <path d="M10 4v12M4 10h12" strokeLinecap="round" />
                  </svg>
                  {t("newFlight.btn.submit")}
                </span>
              </button>
            </div>
          </footer>
        </form>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`flex flex-col gap-1.5 ${className ?? ""}`}>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </span>
      {children}
    </label>
  );
}
