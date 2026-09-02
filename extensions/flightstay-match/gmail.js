const MAX_BODY_CHARS = 40_000;

export function decodeBase64Url(value) {
  if (!value) return "";
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padding = "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(normalized + padding);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
}

function decodeEntity(entity) {
  const named = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
  if (named[entity]) return named[entity];
  if (entity.startsWith("#x")) return String.fromCodePoint(Number.parseInt(entity.slice(2), 16));
  if (entity.startsWith("#")) return String.fromCodePoint(Number.parseInt(entity.slice(1), 10));
  return `&${entity};`;
}

export function htmlToText(html) {
  return html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<(br|\/p|\/div|\/li|\/tr|\/h[1-6])\b[^>]*>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (_, entity) => decodeEntity(entity.toLowerCase()))
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s+/g, "\n")
    .trim();
}

function inlineParts(part, mimeType, output) {
  if (part.body?.attachmentId) return;
  if (part.body?.data && (part.mimeType === mimeType || (!part.mimeType && mimeType === "text/plain"))) {
    output.push(decodeBase64Url(part.body.data));
  }
  for (const child of part.parts || []) inlineParts(child, mimeType, output);
}

export function messageForAnalysis(message) {
  const headers = new Map(
    (message.payload?.headers || []).map((header) => [header.name.toLowerCase(), header.value]),
  );
  const plain = [];
  inlineParts(message.payload || {}, "text/plain", plain);
  const html = [];
  if (plain.length === 0) inlineParts(message.payload || {}, "text/html", html);
  const body = plain.length > 0 ? plain.join("\n") : htmlToText(html.join("\n"));
  const compact = body.replace(/\u0000/g, "").trim().slice(0, MAX_BODY_CHARS);
  if (!compact) return null;
  return {
    message_id: message.id,
    thread_id: message.threadId || null,
    subject: (headers.get("subject") || "").slice(0, 500),
    sender: (headers.get("from") || "").slice(0, 500),
    sent_at: (headers.get("date") || "").slice(0, 100) || null,
    body_text: compact,
  };
}
