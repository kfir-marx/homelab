export type OpportunityStatus =
  | "open"
  | "contacted"
  | "declined"
  | "expired"
  | "closed";

export type Contact = {
  id: string;
  channel: "phone" | "whatsapp" | "email";
  display_value: string;
  normalized_value: string;
  is_primary: boolean;
};

export type Person = {
  id: string;
  display_name: string;
  roles: string[];
  contacts: Contact[];
};

export type Ticket = {
  id: string;
  ticket_number: string;
  person_id: string;
  amount: string;
  currency: string;
  segment_ids: string[];
};

export type Segment = {
  id: string;
  airline: string | null;
  flight_number: string | null;
  origin_code: string;
  destination_code: string;
  departure_at: string;
  arrival_at: string | null;
};

export type Booking = {
  id: string;
  organization_id: string;
  assigned_agent_id: string;
  internal_reference: string | null;
  external_reference: string | null;
  status: "confirmed" | "modified" | "cancelled";
  people: Person[];
  reservations: Array<{
    id: string;
    pnr: string;
    status: string;
    segments: Segment[];
    tickets: Ticket[];
  }>;
  opportunity_ids: string[];
  created_at: string;
  updated_at: string;
};

export type MailboxScanResult = {
  provider: "gmail" | "outlook";
  messages_seen: number;
  messages_skipped: number;
  messages_analyzed: number;
  confirmations_found: number;
  flight_confirmations_found: number;
  flight_tickets_found: number;
  opportunities_created: number;
};

export type Recipient = {
  id: string;
  person: Person;
  contact_point_id: string | null;
  selection_status: "candidate" | "selected" | "excluded" | "needs_contact";
  selection_method: string;
  selection_reason: string | null;
  confidence: string | null;
  priority: number;
};

export type Opportunity = {
  flight_details: { start?: string; end?: string; booking_reference?: string; travelers?: string[]; pnrs?: string[]; segments?: Segment[] };
  id: string;
  organization_id: string;
  booking_id: string;
  assigned_agent_id: string;
  product_type: string;
  destination: string | null;
  service_start: string | null;
  service_end: string | null;
  status: OpportunityStatus;
  close_reason: string | null;
  currency: string;
  version: number;
  tickets: Ticket[];
  recipients: Recipient[];
  created_at: string;
  updated_at: string;
};

export type AgentMetric = {
  agent_id: string;
  agent_name: string;
  total_opportunities: number;
  won_opportunities: number;
  conversion_rate: number;
  won_commission_by_currency: Record<string, string>;
};

export type CurrencyMetrics = {
  currency: string;
  potential_revenue: string;
  potential_commission: string;
  won_revenue: string;
  won_commission: string;
};

export type Metrics = {
  scope: "personal" | "organization";
  total_opportunities: number;
  open_opportunities: number;
  contacted_opportunities: number;
  won_opportunities: number;
  declined_opportunities: number;
  expired_opportunities: number;
  closed_opportunities: number;
  delivery_successes: number;
  delivery_failures: number;
  conversion_rate: number;
  monetary_totals: CurrencyMetrics[];
  per_agent: AgentMetric[];
};

export type Notification = {
  id: string;
  kind: string;
  title: string;
  message: string;
  booking_id: string | null;
  opportunity_id: string | null;
  created_at: string;
  read_at: string | null;
};

export type User = {
  id: string;
  name: string;
  email: string;
  language: "en" | "he";
  active_organization_id: string;
  active_organization_role: "admin" | "agent";
  memberships: Array<{
    organization_id: string;
    organization_name: string;
    role: "admin" | "agent";
  }>;
  auth_providers: string[];
  mailboxes: Array<{
    provider: "gmail" | "outlook";
    email_address: string;
    webhook_active: boolean;
  }>;
};

export type View = "personal" | "organization" | "profile" | "settings";

export type Job = {
  id: string;
  kind: "scan" | "send" | "watch";
  status: "queued" | "running" | "retrying" | "completed" | "failed";
  attempts: number;
  progress: Record<string, number>;
  result: { deliveries?: Array<{ destination_snapshot: string; status: string; error_message: string | null }> };
  error: string | null;
};
