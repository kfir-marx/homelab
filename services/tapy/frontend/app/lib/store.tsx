"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Agent, Flight, FlightStatus, Notification, User, View } from "./types";
import { DICTIONARIES, LOCALES, type Lang, tr } from "./i18n";
import {
  formatDate as fmtDate,
  formatDateRange as fmtDateRange,
  formatNumber as fmtNumber,
  formatPercent as fmtPercent,
  formatUsd as fmtUsd,
} from "./format";

type Toast = {
  id: number;
  title: string;
  body: string;
  tone: "success" | "info" | "error";
};

type Formatters = {
  usd: (n: number, opts?: { decimals?: boolean }) => string;
  date: (iso: string) => string;
  dateRange: (start: string, end: string) => string;
  percent: (n: number, digits?: number) => string;
  number: (n: number) => string;
};

export type NewFlightInput = {
  bookingRef?: string;
  passengerName: string;
  partySize: number;
  origin: string;
  originCity: string;
  destination: string;
  destinationCity: string;
  destinationCountry: string;
  departureDate: string;
  returnDate: string;
  email: string;
  phone: string;
  flightCostUsd: number;
  hotelCostUsd: number;
};

export type AgencyMetrics = {
  totalFlights: number;
  upsoldCount: number;
  openCount: number;
  declinedCount: number;
  pastCount: number;
  closingRate: number;
  netProfit: number;
  potentialProfit: number;
  totalHotelRevenue: number;
};

const EMPTY_METRICS: AgencyMetrics = {
  totalFlights: 0,
  upsoldCount: 0,
  openCount: 0,
  declinedCount: 0,
  pastCount: 0,
  closingRate: 0,
  netProfit: 0,
  potentialProfit: 0,
  totalHotelRevenue: 0,
};

type ApiUser = {
  id: string;
  name: string;
  email: string;
  language: Lang;
  auth_providers: string[];
  mailboxes: Array<{
    provider: "gmail" | "outlook";
    email_address: string;
    webhook_active: boolean;
  }>;
};

type ApiFlight = {
  id: string;
  booking_ref: string;
  passenger_name: string;
  party_size: number;
  email: string;
  phone: string;
  origin: string;
  origin_city: string;
  destination_code: string;
  destination: { city: string };
  arrival_date: string;
  departure_date: string;
  flight_cost_usd: number;
  hotel_cost_usd: number;
  user_id: string;
  status: FlightStatus;
  closed_reason: string | null;
  matched_hotel: Record<string, unknown> | null;
};

type ApiMetrics = {
  total_flights: number;
  upsold_count: number;
  open_count: number;
  declined_count: number;
  hotel_on_file_count: number;
  closing_rate: number;
  net_profit: number;
  potential_profit: number;
  total_hotel_revenue: number;
};

type ApiNotification = {
  id: string;
  kind: Notification["kind"];
  title: string;
  message: string;
  flight_id: string | null;
  created_at: string;
  read_at: string | null;
};

type TapyStore = {
  loading: boolean;
  user: User | null;
  flights: Flight[];
  agents: Agent[];
  activeAgentId: string;
  metrics: AgencyMetrics;
  notifications: Notification[];
  unreadNotificationCount: number;
  view: View;
  setView: (view: View) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  socialLogin: (provider: "google" | "microsoft") => Promise<void>;
  logout: () => Promise<void>;
  updateProfile: (input: { name?: string; language?: Lang }) => Promise<void>;
  connectMailbox: (provider: "gmail" | "outlook") => Promise<void>;
  disconnectMailbox: (provider: "gmail" | "outlook") => Promise<void>;
  sendUpsell: (flightId: string) => Promise<void>;
  markDeclined: (flightId: string) => Promise<void>;
  markOpen: (flightId: string) => Promise<void>;
  addFlight: (input: NewFlightInput) => Promise<Flight>;
  refresh: () => Promise<void>;
  markNotificationsRead: () => Promise<void>;
  pushToast: (toast: Omit<Toast, "id">) => void;
  toasts: Toast[];
  dismissToast: (id: number) => void;
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
  fmt: Formatters;
};

const TapyContext = createContext<TapyStore | null>(null);

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {}
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function mapUser(value: ApiUser): User {
  return {
    id: value.id,
    name: value.name,
    email: value.email,
    language: value.language,
    authProviders: value.auth_providers,
    mailboxes: value.mailboxes.map((mailbox) => ({
      provider: mailbox.provider,
      emailAddress: mailbox.email_address,
      webhookActive: mailbox.webhook_active,
    })),
  };
}

function mapFlight(value: ApiFlight): Flight {
  return {
    id: value.id,
    bookingRef: value.booking_ref,
    passengerName: value.passenger_name,
    partySize: value.party_size,
    email: value.email,
    phone: value.phone,
    origin: value.origin,
    originCity: value.origin_city,
    destination: value.destination_code,
    destinationCity: value.destination.city,
    departureDate: value.arrival_date,
    returnDate: value.departure_date,
    flightCostUsd: value.flight_cost_usd,
    hotelCostUsd: value.hotel_cost_usd,
    agentId: value.user_id,
    status: value.status,
    closedReason: value.closed_reason,
    matchedHotel: value.matched_hotel,
  };
}

function mapMetrics(value: ApiMetrics): AgencyMetrics {
  return {
    totalFlights: value.total_flights,
    upsoldCount: value.upsold_count,
    openCount: value.open_count,
    declinedCount: value.declined_count,
    pastCount: value.hotel_on_file_count,
    closingRate: value.closing_rate,
    netProfit: value.net_profit,
    potentialProfit: value.potential_profit,
    totalHotelRevenue: value.total_hotel_revenue,
  };
}

function mapNotification(value: ApiNotification): Notification {
  return {
    id: value.id,
    kind: value.kind,
    title: value.title,
    message: value.message,
    flightId: value.flight_id,
    createdAt: value.created_at,
    readAt: value.read_at,
  };
}

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [flights, setFlights] = useState<Flight[]>([]);
  const [metrics, setMetrics] = useState<AgencyMetrics>(EMPTY_METRICS);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [view, setView] = useState<View>("agent");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [lang, setLangState] = useState<Lang>("en");

  const refresh = useCallback(async () => {
    try {
      const [nextUser, nextFlights, nextMetrics, nextNotifications] = await Promise.all([
        api<ApiUser>("/v1/users/me"),
        api<ApiFlight[]>("/v1/flights"),
        api<ApiMetrics>("/v1/metrics"),
        api<ApiNotification[]>("/v1/notifications"),
      ]);
      const mapped = mapUser(nextUser);
      setUser(mapped);
      setLangState(mapped.language);
      setFlights(nextFlights.map(mapFlight));
      setMetrics(mapMetrics(nextMetrics));
      setNotifications(nextNotifications.map(mapNotification));
    } catch (error) {
      if (error instanceof Error && error.message.includes("authentication")) {
        setUser(null);
        setFlights([]);
        setMetrics(EMPTY_METRICS);
        setNotifications([]);
      } else if (error instanceof Error && error.message.includes("session")) {
        setUser(null);
        setFlights([]);
        setMetrics(EMPTY_METRICS);
        setNotifications([]);
      } else {
        throw error;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh().catch(() => setLoading(false)), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const userId = user?.id;
  useEffect(() => {
    if (!userId) return;
    const events = new EventSource("/v1/events");
    events.addEventListener("refresh", () => void refresh());
    const interval = window.setInterval(() => void refresh(), 30_000);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      events.close();
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refresh, userId]);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
  }, [lang]);

  useEffect(() => {
    const connected = (event: MessageEvent) => {
      if (event.origin === window.location.origin && event.data === "tapy-mailbox-connected") {
        void refresh();
      }
    };
    window.addEventListener("message", connected);
    return () => window.removeEventListener("message", connected);
  }, [refresh]);

  const pushToast = useCallback((toast: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((previous) => [...previous, { ...toast, id }]);
    window.setTimeout(
      () => setToasts((previous) => previous.filter((item) => item.id !== id)),
      4200,
    );
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((previous) => previous.filter((item) => item.id !== id));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    await api("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    await refresh();
  }, [refresh]);

  const register = useCallback(async (name: string, email: string, password: string) => {
    await api("/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ name, email, password }),
    });
    await refresh();
  }, [refresh]);

  const socialLogin = useCallback(async (provider: "google" | "microsoft") => {
    const result = await api<{ authorization_url: string }>(
      `/v1/auth/${provider}/authorization`,
    );
    window.location.assign(result.authorization_url);
  }, []);

  const logout = useCallback(async () => {
    await api("/v1/auth/logout", { method: "POST" });
    setUser(null);
    setFlights([]);
    setMetrics(EMPTY_METRICS);
    setNotifications([]);
    setView("agent");
  }, []);

  const updateProfile = useCallback(async (input: { name?: string; language?: Lang }) => {
    const next = await api<ApiUser>("/v1/users/me", {
      method: "PATCH",
      body: JSON.stringify(input),
    });
    const mapped = mapUser(next);
    setUser(mapped);
    setLangState(mapped.language);
  }, []);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    if (user) void updateProfile({ language: next });
  }, [updateProfile, user]);

  const connectMailbox = useCallback(async (provider: "gmail" | "outlook") => {
    const result = await api<{ authorization_url: string }>(
      `/v1/mailboxes/${provider}/authorization`,
      { method: "POST" },
    );
    const popup = window.open(result.authorization_url, `tapy-${provider}`, "popup,width=560,height=720");
    if (!popup) window.location.assign(result.authorization_url);
  }, []);

  const disconnectMailbox = useCallback(async (provider: "gmail" | "outlook") => {
    await api(`/v1/mailboxes/${provider}`, { method: "DELETE" });
    await refresh();
  }, [refresh]);

  const updateStatus = useCallback(async (flightId: string, status: "open" | "declined") => {
    const previous = flights;
    setFlights((items) => items.map((item) => item.id === flightId ? { ...item, status } : item));
    try {
      await api(`/v1/flights/${flightId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await refresh();
    } catch (error) {
      setFlights(previous);
      throw error;
    }
  }, [flights, refresh]);

  const sendUpsell = useCallback(async (flightId: string) => {
    await api(`/v1/flights/${flightId}/send-upsell`, { method: "POST" });
    await refresh();
  }, [refresh]);

  const markNotificationsRead = useCallback(async () => {
    const unreadIds = notifications.filter((item) => !item.readAt).map((item) => item.id);
    if (unreadIds.length === 0) return;
    const readAt = new Date().toISOString();
    const unreadSet = new Set(unreadIds);
    setNotifications((items) => items.map((item) => unreadSet.has(item.id) ? { ...item, readAt } : item));
    try {
      await api("/v1/notifications/read", {
        method: "POST",
        body: JSON.stringify({ notification_ids: unreadIds }),
      });
    } catch (error) {
      await refresh();
      throw error;
    }
  }, [notifications, refresh]);

  const addFlight = useCallback(async (input: NewFlightInput): Promise<Flight> => {
    const value = await api<ApiFlight>("/v1/flights", {
      method: "POST",
      body: JSON.stringify({
        booking_ref: input.bookingRef || null,
        passenger_name: input.passengerName,
        party_size: input.partySize,
        email: input.email,
        phone: input.phone,
        origin: input.origin,
        origin_city: input.originCity,
        destination_code: input.destination,
        destination_city: input.destinationCity,
        destination_country: input.destinationCountry,
        arrival_date: input.departureDate,
        departure_date: input.returnDate,
        flight_cost_usd: input.flightCostUsd,
        hotel_cost_usd: input.hotelCostUsd,
      }),
    });
    const created = mapFlight(value);
    setFlights((items) => [...items, created]);
    await refresh();
    return created;
  }, [refresh]);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => tr(DICTIONARIES[lang], key, vars),
    [lang],
  );

  const fmt = useMemo<Formatters>(() => {
    const locale = LOCALES[lang];
    return {
      usd: (n, opts) => fmtUsd(n, { ...opts, locale }),
      date: (iso) => fmtDate(iso, locale),
      dateRange: (start, end) => fmtDateRange(start, end, locale),
      percent: (n, digits) => fmtPercent(n, digits, locale),
      number: (n) => fmtNumber(n, locale),
    };
  }, [lang]);

  const agents = useMemo<Agent[]>(() => user ? [{
    id: user.id,
    name: user.name,
    email: user.email,
    initials: user.name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase(),
    avatarTint: "from-indigo-500 to-violet-500",
  }] : [], [user]);

  const unreadNotificationCount = useMemo(
    () => notifications.filter((notification) => !notification.readAt).length,
    [notifications],
  );

  const value = useMemo<TapyStore>(() => ({
    loading, user, flights, agents, activeAgentId: user?.id ?? "", metrics,
    notifications, unreadNotificationCount, view, setView,
    login, register, socialLogin, logout, updateProfile, connectMailbox, disconnectMailbox,
    sendUpsell,
    markDeclined: (id) => updateStatus(id, "declined"),
    markOpen: (id) => updateStatus(id, "open"),
    addFlight, refresh, markNotificationsRead, pushToast, toasts, dismissToast,
    lang, setLang, t, fmt,
  }), [loading, user, flights, agents, metrics, view, login, register, socialLogin, logout,
    updateProfile, connectMailbox, disconnectMailbox, updateStatus, sendUpsell, addFlight, refresh,
    markNotificationsRead, pushToast, toasts, dismissToast, lang, setLang, t, fmt,
    notifications, unreadNotificationCount]);

  return <TapyContext.Provider value={value}>{children}</TapyContext.Provider>;
}

export function useDemo(): TapyStore {
  const context = useContext(TapyContext);
  if (!context) throw new Error("useDemo must be used inside <DemoProvider>");
  return context;
}

export function useAgencyMetrics(): AgencyMetrics {
  return useDemo().metrics;
}
