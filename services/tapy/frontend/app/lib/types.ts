export type FlightStatus = "open" | "upsold" | "declined" | "past";

export type Flight = {
  id: string;
  bookingRef: string;
  passengerName: string;
  partySize: number;
  email: string;
  phone: string;
  origin: string;
  originCity: string;
  destination: string;
  destinationCity: string;
  departureDate: string;
  returnDate: string;
  flightCostUsd: number;
  hotelCostUsd: number;
  agentId: string;
  status: FlightStatus;
};

export type Agent = {
  id: string;
  name: string;
  initials: string;
  email: string;
  avatarTint: string;
};

export type View = "agent" | "agency";
