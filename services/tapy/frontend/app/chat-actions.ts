"use server";

import { readFile } from "node:fs/promises";
import path from "node:path";
import { GoogleGenAI } from "@google/genai";

const MODEL = "gemini-2.5-flash";

export type ChatRole = "user" | "model";
export type ChatMessage = { role: ChatRole; text: string };

export type DashboardSnapshot = {
  metrics: {
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
  perAgent: Array<{
    name: string;
    totalBookings: number;
    upsold: number;
    closingRate: number;
    revenue: number;
    potential: number;
  }>;
  openFlights: Array<{
    bookingRef: string;
    passenger: string;
    route: string;
    dates: string;
    partySize: number;
    hotelValueUsd: number;
  }>;
};

export type ChatResult =
  | { ok: true; reply: string }
  | { ok: false; error: string };

let cachedInstructions: string | null = null;
async function loadInstructions(): Promise<string> {
  if (cachedInstructions) return cachedInstructions;
  const file = path.join(process.cwd(), "instructions.txt");
  cachedInstructions = await readFile(file, "utf-8");
  return cachedInstructions;
}

export async function sendChatMessage(
  history: ChatMessage[],
  snapshot: DashboardSnapshot,
): Promise<ChatResult> {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return { ok: false, error: "Missing GEMINI_API_KEY environment variable." };
  }
  if (history.length === 0) {
    return { ok: false, error: "No message to send." };
  }

  const lastUser = history[history.length - 1];
  if (lastUser.role !== "user") {
    return { ok: false, error: "Last message must come from the user." };
  }

  const base = await loadInstructions();
  const snapshotBlock = `\n\nCURRENT_DASHBOARD_SNAPSHOT (live, ground truth for "right now"):\n${JSON.stringify(
    snapshot,
    null,
    2,
  )}`;
  const systemInstruction = base + snapshotBlock;

  const contents = history.map((m) => ({
    role: m.role,
    parts: [{ text: m.text }],
  }));

  try {
    const ai = new GoogleGenAI({ apiKey });
    const response = await ai.models.generateContent({
      model: MODEL,
      contents,
      config: {
        systemInstruction,
        temperature: 0.4,
      },
    });
    const reply = response.text?.trim() ?? "";
    if (!reply) {
      return { ok: false, error: "Empty response from the assistant." };
    }
    return { ok: true, reply };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return { ok: false, error: `Gemini request failed: ${message}` };
  }
}
