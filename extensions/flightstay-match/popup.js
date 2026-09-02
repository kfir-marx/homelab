const form = document.querySelector("#scan-form");
const scanButton = document.querySelector("#scan");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const signOut = document.querySelector("#sign-out");

function probability(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function render(items) {
  results.replaceChildren();
  const sorted = [...items].sort((left, right) => right.best_probability - left.best_probability);
  for (const item of sorted) {
    const row = document.createElement("li");
    const heading = document.createElement("strong");
    heading.textContent = item.subject;
    const details = document.createElement("p");
    const match = item.matches?.[0];
    details.textContent = item.booking.is_hotel_booking
      ? `${item.booking.hotel_name || "Hotel booking"} · ${match?.flight_label || "No flight"} · ${probability(item.best_probability)}`
      : "Not identified as a hotel booking";
    row.className = match?.related ? "related" : "not-related";
    row.append(heading, details);
    results.append(row);
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "scan-progress") {
    status.textContent = `Analyzing ${message.completed} of ${message.total}…`;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  scanButton.disabled = true;
  results.replaceChildren();
  status.textContent = "Waiting for Gmail permission…";
  const query = document.querySelector("#query").value.trim();
  const maxResults = Number.parseInt(document.querySelector("#max-results").value, 10);
  await chrome.storage.local.set({ query, maxResults });
  try {
    const response = await chrome.runtime.sendMessage({ type: "scan", query, maxResults });
    if (!response?.ok) throw new Error(response?.error || "Scan failed.");
    render(response.results);
    status.textContent = `Finished. ${response.results.length} messages contained analyzable text.`;
  } catch (error) {
    status.textContent = String(error.message || error);
  } finally {
    scanButton.disabled = false;
  }
});

signOut.addEventListener("click", async () => {
  const response = await chrome.runtime.sendMessage({ type: "sign-out" });
  status.textContent = response?.ok
    ? "Disconnected in Chrome. Revoke the app in your Google Account to remove its grant."
    : response?.error;
  results.replaceChildren();
});

const saved = await chrome.storage.local.get(["query", "maxResults"]);
if (saved.query) document.querySelector("#query").value = saved.query;
if (saved.maxResults) document.querySelector("#max-results").value = saved.maxResults;
