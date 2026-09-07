"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { ACTIVE_AGENT_ID, AGENTS, FLIGHTS, HOTEL_COMMISSION_RATE } from "./mockData";
import type { Agent, Flight, FlightStatus, View } from "./types";
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
  passengerName: string;
  originCity: string;
  destinationCity: string;
  departureDate: string;
  returnDate: string;
  email: string;
  phone: string;
};

type DemoStore = {
  flights: Flight[];
  agents: Agent[];
  activeAgentId: string;
  view: View;
  setView: (view: View) => void;
  markUpsold: (flightId: string) => void;
  markDeclined: (flightId: string) => void;
  markOpen: (flightId: string) => void;
  addFlight: (input: NewFlightInput) => Flight;
  pushToast: (toast: Omit<Toast, "id">) => void;
  toasts: Toast[];
  dismissToast: (id: number) => void;
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
  fmt: Formatters;
};

const CITY_AIRPORT: Record<string, string> = {
  "new york": "JFK",
  "newark": "EWR",
  "san francisco": "SFO",
  "los angeles": "LAX",
  "chicago": "ORD",
  "boston": "BOS",
  "seattle": "SEA",
  "atlanta": "ATL",
  "miami": "MIA",
  "cancun": "CUN",
  "cancún": "CUN",
  "mexico city": "MEX",
  "lima": "LIM",
  "london": "LHR",
  "paris": "CDG",
  "frankfurt": "FRA",
  "amsterdam": "AMS",
  "madrid": "MAD",
  "barcelona": "BCN",
  "rome": "FCO",
  "milan": "MXP",
  "lisbon": "LIS",
  "vienna": "VIE",
  "berlin": "BER",
  "munich": "MUC",
  "zurich": "ZRH",
  "stockholm": "ARN",
  "oslo": "OSL",
  "copenhagen": "CPH",
  "dublin": "DUB",
  "reykjavik": "KEF",
  "reykjavík": "KEF",
  "athens": "ATH",
  "istanbul": "IST",
  "dubai": "DXB",
  "tel aviv": "TLV",
  "tokyo": "HND",
  "singapore": "SIN",
  "bangkok": "BKK",
  "hong kong": "HKG",
  "shanghai": "PVG",
  "seoul": "ICN",
  "mumbai": "BOM",
  "delhi": "DEL",
  "bali": "DPS",
  "sydney": "SYD",
  "auckland": "AKL",
  "cape town": "CPT",
  "johannesburg": "JNB",
  "dakar": "DKR",
  "sao paulo": "GRU",
  "rio de janeiro": "GIG",
  "buenos aires": "EZE",
};

function cityToCode(city: string): string {
  const key = city.trim().toLowerCase();
  if (CITY_AIRPORT[key]) return CITY_AIRPORT[key];
  const clean = city.replace(/[^a-zA-Z]/g, "").toUpperCase();
  return (clean + "XXX").slice(0, 3);
}

const DemoContext = createContext<DemoStore | null>(null);

export function DemoProvider({ children }: { children: React.ReactNode }) {
  const [flights, setFlights] = useState<Flight[]>(FLIGHTS);
  const [view, setView] = useState<View>("agent");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [lang, setLang] = useState<Lang>("en");

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
  }, [lang]);

  const updateStatus = useCallback((flightId: string, status: FlightStatus) => {
    setFlights((prev) =>
      prev.map((f) => (f.id === flightId ? { ...f, status } : f)),
    );
  }, []);

  const pushToast = useCallback((toast: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { ...toast, id }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const markUpsold = useCallback(
    (flightId: string) => updateStatus(flightId, "upsold"),
    [updateStatus],
  );
  const markDeclined = useCallback(
    (flightId: string) => updateStatus(flightId, "declined"),
    [updateStatus],
  );
  const markOpen = useCallback(
    (flightId: string) => updateStatus(flightId, "open"),
    [updateStatus],
  );

  const addFlight = useCallback((input: NewFlightInput): Flight => {
    let created!: Flight;
    setFlights((prev) => {
      const maxNum = prev.reduce((m, f) => {
        const n = parseInt(f.id.replace("FL-", ""), 10);
        return Number.isFinite(n) ? Math.max(m, n) : m;
      }, 9000);
      const num = maxNum + 1;
      const partySize = 1 + Math.floor(Math.random() * 3);
      const flightCostUsd = (800 + Math.floor(Math.random() * 130) * 20);
      const hotelCostUsd = (1500 + Math.floor(Math.random() * 150) * 20);
      created = {
        id: `FL-${num}`,
        bookingRef: `TPY-${num}`,
        passengerName: input.passengerName,
        partySize,
        email: input.email,
        phone: input.phone,
        origin: cityToCode(input.originCity),
        originCity: input.originCity,
        destination: cityToCode(input.destinationCity),
        destinationCity: input.destinationCity,
        departureDate: input.departureDate,
        returnDate: input.returnDate,
        flightCostUsd,
        hotelCostUsd,
        agentId: ACTIVE_AGENT_ID,
        status: "open",
      };
      return [...prev, created];
    });
    return created;
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) =>
      tr(DICTIONARIES[lang], key, vars),
    [lang],
  );

  const fmt = useMemo<Formatters>(() => {
    const locale = LOCALES[lang];
    return {
      usd: (n, opts) => fmtUsd(n, { ...opts, locale }),
      date: (iso) => fmtDate(iso, locale),
      dateRange: (s, e) => fmtDateRange(s, e, locale),
      percent: (n, digits) => fmtPercent(n, digits, locale),
      number: (n) => fmtNumber(n, locale),
    };
  }, [lang]);

  const value = useMemo<DemoStore>(
    () => ({
      flights,
      agents: AGENTS,
      activeAgentId: ACTIVE_AGENT_ID,
      view,
      setView,
      markUpsold,
      markDeclined,
      markOpen,
      addFlight,
      pushToast,
      toasts,
      dismissToast,
      lang,
      setLang,
      t,
      fmt,
    }),
    [flights, view, markUpsold, markDeclined, markOpen, addFlight, pushToast, toasts, dismissToast, lang, t, fmt],
  );

  return <DemoContext.Provider value={value}>{children}</DemoContext.Provider>;
}

export function useDemo(): DemoStore {
  const ctx = useContext(DemoContext);
  if (!ctx) throw new Error("useDemo must be used inside <DemoProvider>");
  return ctx;
}

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

export function useAgencyMetrics(): AgencyMetrics {
  const { flights } = useDemo();
  return useMemo(() => {
    const totalFlights = flights.length;
    let upsoldCount = 0;
    let openCount = 0;
    let declinedCount = 0;
    let pastCount = 0;
    let upsoldHotelTotal = 0;
    let openHotelTotal = 0;

    for (const f of flights) {
      switch (f.status) {
        case "upsold":
          upsoldCount += 1;
          upsoldHotelTotal += f.hotelCostUsd;
          break;
        case "open":
          openCount += 1;
          openHotelTotal += f.hotelCostUsd;
          break;
        case "declined":
          declinedCount += 1;
          break;
        case "past":
          pastCount += 1;
          break;
      }
    }

    const closingRate = totalFlights === 0 ? 0 : upsoldCount / totalFlights;
    const netProfit = upsoldHotelTotal * HOTEL_COMMISSION_RATE;
    const potentialProfit = openHotelTotal * HOTEL_COMMISSION_RATE;

    return {
      totalFlights,
      upsoldCount,
      openCount,
      declinedCount,
      pastCount,
      closingRate,
      netProfit,
      potentialProfit,
      totalHotelRevenue: upsoldHotelTotal,
    };
  }, [flights]);
}
