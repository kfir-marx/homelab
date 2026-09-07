"use server";

export type UpsellInput = {
  flightId: string;
  bookingRef: string;
  passengerName: string;
  customerEmail: string;
  customerPhone: string;
  destinationCity: string;
  departureDate: string;
  returnDate: string;
};

export type UpsellResult =
  | { ok: true; sid: string }
  | { ok: false; error: string };

const TWILIO_FROM = "whatsapp:+14155238886";
export async function sendUpsellWhatsApp(input: UpsellInput): Promise<UpsellResult> {
  const accountSid = process.env.TWILIO_ACCOUNT_SID;
  const authToken = process.env.TWILIO_AUTH_TOKEN;
  const agentPhoneNumber = process.env.AGENT_PHONE_NUMBER;

  if (!accountSid) {
    return { ok: false, error: "Missing TWILIO_ACCOUNT_SID environment variable." };
  }
  if (!authToken) {
    return { ok: false, error: "Missing TWILIO_AUTH_TOKEN environment variable." };
  }
  if (!agentPhoneNumber) {
    return { ok: false, error: "Missing AGENT_PHONE_NUMBER environment variable." };
  }

  // const landingUrl = `${LANDING_PAGE_BASE}/${input.bookingRef}`;
  const landingUrl = `https://app.letstay.co.il/results?place_id=ChIJOwg_06VPwokRYv534QaPC8g&check_in=260530&check_out=260621&guests=2&rooms=1&private_travel=true&utm_source=letstay`;
  const messageBody =
    `היי ${input.passengerName.split(/[&,]/)[0].trim()}! ✈️\n\n` +
    `הנסיעה שלך עם Tapy ל-${input.destinationCity} (${input.departureDate} - ${input.returnDate}) אושרה.\n\n` +
    `מצאנו עבורך 3 מלונות במחירים בלעדיים לתאריכים שלך. ` +
    `ניתן לשריין כל אחד מהם בלחיצה אחת — ללא חיוב עד הצ'ק-אין:\n\n` +
    `👉 ${landingUrl}\n\n` +
    `להסרה השב STOP.`;

  const body = new URLSearchParams({
    To: `whatsapp:${agentPhoneNumber}`,
    From: TWILIO_FROM,
    Body: messageBody,
  });

  const credentials = Buffer.from(`${accountSid}:${authToken}`).toString("base64");

  try {
    const response = await fetch(
      `https://api.twilio.com/2010-04-01/Accounts/${accountSid}/Messages.json`,
      {
        method: "POST",
        headers: {
          Authorization: `Basic ${credentials}`,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: body.toString(),
      },
    );

    if (!response.ok) {
      const errorText = await response.text();
      return {
        ok: false,
        error: `Twilio responded with ${response.status}: ${errorText}`,
      };
    }

    const data = (await response.json()) as { sid: string };
    return { ok: true, sid: data.sid };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return { ok: false, error: `Failed to reach Twilio: ${message}` };
  }
}
