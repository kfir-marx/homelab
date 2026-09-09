"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { DICTIONARIES, LOCALES, type Lang, tr } from "./i18n";
import type { Booking, Metrics, Notification, Opportunity, OpportunityStatus, User, View } from "./types";

type Toast = { id: number; title: string; body: string; tone: "success" | "info" | "error" };
type NewBooking = {
  reference: string;
  passenger: string;
  phone: string;
  email: string;
  pnr: string;
  ticketNumber: string;
  origin: string;
  destination: string;
  departureAt: string;
  arrivalAt: string;
  amount: number;
  currency: string;
};

const EMPTY_METRICS: Metrics = {
  scope: "personal", total_opportunities: 0, open_opportunities: 0,
  contacted_opportunities: 0, won_opportunities: 0, declined_opportunities: 0,
  expired_opportunities: 0, closed_opportunities: 0, delivery_successes: 0,
  delivery_failures: 0, conversion_rate: 0, monetary_totals: [], per_agent: [],
};

type Store = {
  loading: boolean;
  user: User | null;
  bookings: Booking[];
  opportunities: Opportunity[];
  metrics: Metrics;
  notifications: Notification[];
  unreadNotificationCount: number;
  view: View;
  setView: (view: View) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  socialLogin: (provider: "google" | "microsoft") => Promise<void>;
  logout: () => Promise<void>;
  updateProfile: (input: { name?: string; language?: Lang; active_organization_id?: string }) => Promise<void>;
  connectMailbox: (provider: "gmail" | "outlook") => Promise<void>;
  disconnectMailbox: (provider: "gmail" | "outlook") => Promise<void>;
  addBooking: (input: NewBooking) => Promise<void>;
  updateOpportunity: (opportunity: Opportunity, status: OpportunityStatus) => Promise<void>;
  updateRecipient: (opportunityId: string, personId: string, contactId: string | null, selectionStatus: string) => Promise<void>;
  sendOpportunity: (opportunityId: string) => Promise<void>;
  refresh: () => Promise<void>;
  markNotificationsRead: () => Promise<void>;
  pushToast: (toast: Omit<Toast, "id">) => void;
  toasts: Toast[];
  dismissToast: (id: number) => void;
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
  money: (value: string | number, currency?: string) => string;
};

const Context = createContext<Store | null>(null);

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = ((await response.json()) as { detail?: string }).detail || message; } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [metrics, setMetrics] = useState<Metrics>(EMPTY_METRICS);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [view, setViewState] = useState<View>("personal");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [lang, setLangState] = useState<Lang>("en");

  const load = useCallback(async (requestedView?: View) => {
    try {
      const me = await api<User>("/v1/users/me");
      const nextView = requestedView ?? view;
      const scope = nextView === "organization" && me.active_organization_role === "admin" ? "organization" : "personal";
      const [nextBookings, nextOpportunities, nextMetrics, nextNotifications] = await Promise.all([
        api<Booking[]>(`/v1/bookings?scope=${scope}&limit=100`),
        api<Opportunity[]>(`/v1/opportunities?scope=${scope}&limit=100`),
        api<Metrics>(`/v1/metrics?scope=${scope}`),
        api<Notification[]>("/v1/notifications"),
      ]);
      setUser(me); setLangState(me.language); setBookings(nextBookings);
      setOpportunities(nextOpportunities); setMetrics(nextMetrics); setNotifications(nextNotifications);
    } catch (error) {
      if (error instanceof Error && /(authentication|session)/.test(error.message)) {
        setUser(null); setBookings([]); setOpportunities([]); setMetrics(EMPTY_METRICS); setNotifications([]);
      } else { throw error; }
    } finally { setLoading(false); }
  }, [view]);

  const refresh = useCallback(() => load(), [load]);
  useEffect(() => { const timer = window.setTimeout(() => void load().catch(() => setLoading(false)), 0); return () => clearTimeout(timer); }, [load]);
  useEffect(() => {
    if (!user) return;
    const source = new EventSource("/v1/events");
    source.addEventListener("refresh", () => void load());
    const timer = window.setInterval(() => void load(), 30_000);
    return () => { source.close(); clearInterval(timer); };
  }, [load, user]);
  useEffect(() => { document.documentElement.lang = lang; document.documentElement.dir = lang === "he" ? "rtl" : "ltr"; }, [lang]);
  useEffect(() => {
    const connected = (event: MessageEvent) => { if (event.origin === location.origin && event.data === "tapy-mailbox-connected") void load(); };
    addEventListener("message", connected); return () => removeEventListener("message", connected);
  }, [load]);

  const setView = useCallback((next: View) => {
    if (next === "organization" && user?.active_organization_role !== "admin") return;
    setViewState(next); void load(next);
  }, [load, user]);
  const login = useCallback(async (email: string, password: string) => { await api("/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }); await load("personal"); }, [load]);
  const register = useCallback(async (name: string, email: string, password: string) => { await api("/v1/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) }); await load("personal"); }, [load]);
  const socialLogin = useCallback(async (provider: "google" | "microsoft") => { const value = await api<{ authorization_url: string }>(`/v1/auth/${provider}/authorization`); location.assign(value.authorization_url); }, []);
  const logout = useCallback(async () => { await api("/v1/auth/logout", { method: "POST" }); setUser(null); setBookings([]); setOpportunities([]); setViewState("personal"); }, []);
  const updateProfile = useCallback(async (input: { name?: string; language?: Lang; active_organization_id?: string }) => { const me = await api<User>("/v1/users/me", { method: "PATCH", body: JSON.stringify(input) }); setUser(me); setLangState(me.language); await load("personal"); }, [load]);
  const setLang = useCallback((next: Lang) => { setLangState(next); if (user) void updateProfile({ language: next }); }, [updateProfile, user]);
  const connectMailbox = useCallback(async (provider: "gmail" | "outlook") => { const value = await api<{ authorization_url: string }>(`/v1/mailboxes/${provider}/authorization`, { method: "POST" }); const popup = open(value.authorization_url, `tapy-${provider}`, "popup,width=560,height=720"); if (!popup) location.assign(value.authorization_url); }, []);
  const disconnectMailbox = useCallback(async (provider: "gmail" | "outlook") => { await api(`/v1/mailboxes/${provider}`, { method: "DELETE" }); await load(); }, [load]);
  const addBooking = useCallback(async (input: NewBooking) => {
    const booking = await api<Booking>("/v1/bookings", { method: "POST", body: JSON.stringify({ external_reference: input.reference || null, people: [{ display_name: input.passenger, roles: [{ role: "traveler", source_method: "manual" }], contacts: [input.phone ? { channel: "whatsapp", value: input.phone, is_primary: true } : { channel: "email", value: input.email, is_primary: true }] }], reservations: [{ pnr: input.pnr, segments: [{ origin_code: input.origin, destination_code: input.destination, departure_at: input.departureAt, arrival_at: input.arrivalAt || null }] }] }) });
    const person = booking.people[0]; const reservation = booking.reservations[0];
    const withTicket = await api<Booking>(`/v1/bookings/${booking.id}/tickets`, { method: "POST", body: JSON.stringify({ reservation_id: reservation.id, person_id: person.id, ticket_number: input.ticketNumber, segment_ids: reservation.segments.map((item) => item.id), amount: input.amount, currency: input.currency }) });
    const ticket = withTicket.reservations[0].tickets[0];
    await api("/v1/opportunities", { method: "POST", body: JSON.stringify({ booking_id: booking.id, ticket_ids: [ticket.id], destination: input.destination, service_start: input.departureAt, service_end: input.arrivalAt || input.departureAt, currency: input.currency }) });
    await load();
  }, [load]);
  const updateOpportunity = useCallback(async (item: Opportunity, status: OpportunityStatus) => { await api(`/v1/opportunities/${item.id}`, { method: "PATCH", body: JSON.stringify({ status, version: item.version }) }); await load(); }, [load]);
  const updateRecipient = useCallback(async (opportunityId: string, personId: string, contactId: string | null, selectionStatus: string) => { await api(`/v1/opportunities/${opportunityId}/recipients/${personId}`, { method: "PUT", body: JSON.stringify({ person_id: personId, contact_point_id: contactId, selection_status: selectionStatus, selection_method: "manual", selection_reason: "Selected by the assigned travel agent" }) }); await load(); }, [load]);
  const sendOpportunity = useCallback(async (id: string) => { await api(`/v1/opportunities/${id}/send`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } }); await load(); }, [load]);
  const markNotificationsRead = useCallback(async () => { const ids = notifications.filter((item) => !item.read_at).map((item) => item.id); if (!ids.length) return; await api("/v1/notifications/read", { method: "POST", body: JSON.stringify({ notification_ids: ids }) }); await load(); }, [load, notifications]);
  const pushToast = useCallback((toast: Omit<Toast, "id">) => { const id = Date.now() + Math.random(); setToasts((items) => [...items, { ...toast, id }]); setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 4200); }, []);
  const dismissToast = useCallback((id: number) => setToasts((items) => items.filter((item) => item.id !== id)), []);
  const t = useCallback((key: string, vars?: Record<string, string | number>) => tr(DICTIONARIES[lang], key, vars), [lang]);
  const money = useCallback((value: string | number, currency = "USD") => new Intl.NumberFormat(LOCALES[lang], { style: "currency", currency }).format(Number(value)), [lang]);
  const unreadNotificationCount = notifications.filter((item) => !item.read_at).length;
  const value = useMemo<Store>(() => ({ loading, user, bookings, opportunities, metrics, notifications, unreadNotificationCount, view, setView, login, register, socialLogin, logout, updateProfile, connectMailbox, disconnectMailbox, addBooking, updateOpportunity, updateRecipient, sendOpportunity, refresh, markNotificationsRead, pushToast, toasts, dismissToast, lang, setLang, t, money }), [loading, user, bookings, opportunities, metrics, notifications, unreadNotificationCount, view, setView, login, register, socialLogin, logout, updateProfile, connectMailbox, disconnectMailbox, addBooking, updateOpportunity, updateRecipient, sendOpportunity, refresh, markNotificationsRead, pushToast, toasts, dismissToast, lang, setLang, t, money]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useDemo(): Store {
  const value = useContext(Context);
  if (!value) throw new Error("useDemo must be inside DemoProvider");
  return value;
}
