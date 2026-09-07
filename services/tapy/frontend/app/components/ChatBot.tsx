"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sendChatMessage, type ChatMessage, type DashboardSnapshot } from "../chat-actions";
import { useAgencyMetrics, useDemo } from "../lib/store";

type UiMessage = ChatMessage & { id: number };

export default function ChatBot() {
  const { flights, agents, t, lang } = useDemo();
  const metrics = useAgencyMetrics();
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const messageIdRef = useRef(0);
  const isRtl = lang === "he";

  const nextId = useCallback(() => {
    messageIdRef.current += 1;
    return messageIdRef.current;
  }, []);

  const snapshot = useMemo<DashboardSnapshot>(() => {
    const perAgent = agents
      .map((a) => {
        const own = flights.filter((f) => f.agentId === a.id);
        const upsold = own.filter((f) => f.status === "upsold");
        const declined = own.filter((f) => f.status === "declined");
        const past = own.filter((f) => f.status === "past");
        const open = own.filter((f) => f.status === "open");
        const handled = upsold.length + declined.length + past.length;
        return {
          name: a.name,
          totalBookings: own.length,
          upsold: upsold.length,
          closingRate: handled === 0 ? 0 : upsold.length / handled,
          revenue: upsold.reduce((s, f) => s + f.hotelCostUsd, 0) * 0.01,
          potential: open.reduce((s, f) => s + f.hotelCostUsd, 0) * 0.01,
        };
      })
      .sort((a, b) => b.revenue - a.revenue);

    const openFlights = flights
      .filter((f) => f.status === "open")
      .map((f) => ({
        bookingRef: f.bookingRef,
        passenger: f.passengerName,
        route: `${f.origin} → ${f.destination}`,
        dates: `${f.departureDate} – ${f.returnDate}`,
        partySize: f.partySize,
        hotelValueUsd: f.hotelCostUsd,
      }));

    return { metrics, perAgent, openFlights };
  }, [agents, flights, metrics]);

  useEffect(() => {
    if (open) {
      if (closeTimer.current) {
        window.clearTimeout(closeTimer.current);
        closeTimer.current = null;
      }
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          setMounted(true);
          inputRef.current?.focus();
        }),
      );
    } else if (mounted) {
      requestAnimationFrame(() => setMounted(false));
      closeTimer.current = window.setTimeout(() => {
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
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, pending, open]);

  const handleSend = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || pending) return;
      const userMsg: UiMessage = { id: nextId(), role: "user", text: trimmed };
      const nextMessages = [...messages, userMsg];
      setMessages(nextMessages);
      setDraft("");
      setPending(true);
      try {
        const history: ChatMessage[] = nextMessages.map(({ role, text }) => ({ role, text }));
        const res = await sendChatMessage(history, snapshot);
        if (res.ok) {
          setMessages((prev) => [
            ...prev,
            { id: nextId(), role: "model", text: res.reply },
          ]);
        } else {
          setMessages((prev) => [
            ...prev,
            { id: nextId(), role: "model", text: t("chat.error") },
          ]);
        }
      } catch {
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "model", text: t("chat.error") },
        ]);
      } finally {
        setPending(false);
      }
    },
    [messages, nextId, pending, snapshot, t],
  );

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    void handleSend(draft);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend(draft);
    }
  }

  const suggestions = [
    t("chat.suggest.closing"),
    t("chat.suggest.top"),
    t("chat.suggest.left"),
  ];

  const fabHidden = open || mounted;

  return (
    <>
      <button
        type="button"
        aria-label={t("chat.btn.aria")}
        onClick={() => setOpen(true)}
        className="group fixed bottom-6 z-[110] inline-flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-br from-amber-500 to-orange-500 text-white shadow-[0_18px_40px_-12px_rgba(234,88,12,0.7)] ring-2 ring-white/40 transition-all duration-300 ease-out hover:from-amber-400 hover:to-orange-400 hover:shadow-[0_22px_48px_-12px_rgba(234,88,12,0.8)] active:scale-95"
        style={{
          insetInlineEnd: "1.5rem",
          opacity: fabHidden ? 0 : 1,
          transform: fabHidden ? "scale(0.6) translateY(8px)" : "scale(1) translateY(0)",
          pointerEvents: fabHidden ? "none" : "auto",
        }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-full bg-white/10 opacity-0 transition group-hover:opacity-100"
        />
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-6 w-6">
          <path d="M21 12c0 4.418-4.03 8-9 8a9.9 9.9 0 0 1-3.5-.63L3 21l1.7-4.43A7.7 7.7 0 0 1 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8Z" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M9 11h.01M12 11h.01M15 11h.01" strokeLinecap="round" />
        </svg>
      </button>

      {(open || mounted) && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label={t("chat.title")}
          className="fixed bottom-6 z-[110] flex w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl ring-1 ring-slate-900/5 transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
          style={{
            insetInlineEnd: "1.5rem",
            maxHeight: "min(560px, calc(100vh - 6rem))",
            transformOrigin: isRtl ? "bottom left" : "bottom right",
            opacity: mounted ? 1 : 0,
            transform: mounted
              ? "translateY(0) scale(1)"
              : "translateY(12px) scale(0.92)",
          }}
        >
          <header className="relative flex items-start justify-between gap-3 border-b border-slate-100 bg-gradient-to-br from-amber-50 via-white to-white px-5 py-4">
            <span
              aria-hidden
              className="pointer-events-none absolute -top-10 h-24 w-24 rounded-full bg-amber-300/30 blur-3xl"
              style={{ insetInlineEnd: "-1.5rem" }}
            />
            <div className="relative flex items-center gap-3">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 text-white shadow-sm ring-2 ring-white">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
                  <path d="M12 2 9.5 8.5 3 11l6.5 2.5L12 20l2.5-6.5L21 11l-6.5-2.5L12 2Z" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <div>
                <p className="text-sm font-semibold tracking-tight text-slate-900">
                  {t("chat.title")}
                </p>
                <p className="text-[11px] text-slate-500">{t("chat.subtitle")}</p>
              </div>
            </div>
            <button
              type="button"
              aria-label={t("chat.close.aria")}
              onClick={() => setOpen(false)}
              className="relative rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
              </svg>
            </button>
          </header>

          <div
            ref={scrollRef}
            className="flex-1 space-y-3 overflow-y-auto bg-slate-50/40 px-4 py-4"
          >
            <Bubble role="model" text={t("chat.greeting")} isRtl={isRtl} />

            {messages.length === 0 && (
              <div className="flex flex-wrap gap-2 pt-1">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void handleSend(s)}
                    disabled={pending}
                    className="rounded-full border border-amber-200 bg-amber-50/70 px-3 py-1.5 text-[11px] font-medium text-amber-900 transition hover:border-amber-300 hover:bg-amber-100 active:scale-[0.98] disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}

            {messages.map((m) => (
              <Bubble key={m.id} role={m.role} text={m.text} isRtl={isRtl} />
            ))}

            {pending && (
              <div className="flex items-center gap-2 px-1 text-xs text-slate-500">
                <span className="inline-flex gap-1">
                  <Dot delay={0} />
                  <Dot delay={120} />
                  <Dot delay={240} />
                </span>
                {t("chat.thinking")}
              </div>
            )}
          </div>

          <form
            onSubmit={onSubmit}
            className="flex items-end gap-2 border-t border-slate-100 bg-white px-3 py-3"
          >
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={t("chat.placeholder")}
              rows={1}
              disabled={pending}
              className="max-h-32 min-h-[40px] flex-1 resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm transition placeholder:text-slate-400 focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-200/70 disabled:opacity-60"
            />
            <button
              type="submit"
              aria-label={t("chat.send.aria")}
              disabled={pending || draft.trim().length === 0}
              className="inline-flex h-10 w-10 flex-none items-center justify-center rounded-xl bg-gradient-to-br from-amber-500 to-orange-500 text-white shadow-[0_6px_18px_-8px_rgba(234,88,12,0.7)] transition hover:from-amber-400 hover:to-orange-400 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4 rtl:-scale-x-100">
                <path d="M3.4 20.4 22 12 3.4 3.6l.01 6.53L17 12 3.41 13.87l-.01 6.53Z" />
              </svg>
            </button>
          </form>
        </div>
      )}
    </>
  );
}

function Bubble({ role, text, isRtl }: { role: "user" | "model"; text: string; isRtl: boolean }) {
  const isUser = role === "user";
  const align = isUser ? "items-end" : "items-start";
  const bubble = isUser
    ? "bg-gradient-to-br from-amber-500 to-orange-500 text-white shadow-[0_6px_18px_-10px_rgba(234,88,12,0.6)]"
    : "border border-slate-200 bg-white text-slate-800 shadow-sm";
  return (
    <div className={`flex flex-col ${align}`}>
      <div
        className={`max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${bubble}`}
        dir={isRtl ? "rtl" : undefined}
      >
        {text}
      </div>
    </div>
  );
}

function Dot({ delay }: { delay: number }) {
  return (
    <span
      className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400"
      style={{ animationDelay: `${delay}ms`, animationDuration: "900ms" }}
    />
  );
}
