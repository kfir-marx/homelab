import { messageForAnalysis } from "./gmail.js";

const GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me";
const MATCHER_API = "https://staymatch.547600.xyz";

chrome.action.onClicked.addListener(() => {
  chrome.tabs.create({ url: chrome.runtime.getURL("popup.html") });
});

async function accessToken(interactive) {
  const result = await chrome.identity.getAuthToken({ interactive });
  const token = typeof result === "string" ? result : result?.token;
  if (!token) throw new Error("Google authorization did not return a token.");
  return token;
}

async function gmailRequest(path, token) {
  const response = await fetch(`${GMAIL_API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401) {
    await chrome.identity.removeCachedAuthToken({ token });
    throw new Error("Google authorization expired. Connect Gmail again.");
  }
  if (!response.ok) throw new Error(`Gmail request failed (${response.status}).`);
  return response.json();
}

async function analyzeEmail(email, token) {
  const response = await fetch(`${MATCHER_API}/v1/analyze`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(email),
  });
  if (response.status === 401) {
    await chrome.identity.removeCachedAuthToken({ token });
    throw new Error("The service rejected Google authorization. Connect Gmail again.");
  }
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail || "";
    } catch {
      detail = "";
    }
    throw new Error(detail || `Analysis failed (${response.status}).`);
  }
  return response.json();
}

async function notifyProgress(completed, total) {
  try {
    await chrome.runtime.sendMessage({ type: "scan-progress", completed, total });
  } catch {
    // The popup may close during a scan; no message content is persisted.
  }
}

async function scan(query, maxResults) {
  const token = await accessToken(true);
  const params = new URLSearchParams({
    maxResults: String(maxResults),
    q: query,
    includeSpamTrash: "false",
  });
  const listing = await gmailRequest(`/messages?${params}`, token);
  const references = listing.messages || [];
  const results = [];
  for (let index = 0; index < references.length; index += 1) {
    const message = await gmailRequest(`/messages/${encodeURIComponent(references[index].id)}?format=full`, token);
    const email = messageForAnalysis(message);
    if (email) {
      const analysis = await analyzeEmail(email, token);
      results.push({
        subject: email.subject || "(no subject)",
        sender: email.sender,
        ...analysis,
      });
    }
    await notifyProgress(index + 1, references.length);
  }
  return results;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "scan") {
    scan(message.query, message.maxResults)
      .then((results) => sendResponse({ ok: true, results }))
      .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }
  if (message?.type === "sign-out") {
    chrome.identity
      .clearAllCachedAuthTokens()
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
    return true;
  }
  return false;
});
