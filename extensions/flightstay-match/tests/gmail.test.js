import test from "node:test";
import assert from "node:assert/strict";

import { decodeBase64Url, htmlToText, messageForAnalysis } from "../gmail.js";

function encoded(value) {
  return Buffer.from(value).toString("base64url");
}

test("decodes Unicode base64url bodies", () => {
  assert.equal(decodeBase64Url(encoded("Hotel שלום")), "Hotel שלום");
});

test("extracts inline text but never attachment bodies", () => {
  const result = messageForAnalysis({
    id: "m1",
    threadId: "t1",
    payload: {
      headers: [
        { name: "Subject", value: "Confirmation" },
        { name: "From", value: "hotel@example.com" },
      ],
      parts: [
        { mimeType: "text/plain", body: { data: encoded("London reservation") } },
        { mimeType: "application/pdf", body: { attachmentId: "secret" } },
      ],
    },
  });
  assert.equal(result.body_text, "London reservation");
  assert.equal(result.subject, "Confirmation");
  assert.equal(JSON.stringify(result).includes("secret"), false);
});

test("converts simple HTML without scripts", () => {
  assert.equal(htmlToText("<p>Hotel &amp; Spa</p><script>steal()</script>London"), "Hotel & Spa\nLondon");
});
