# Tapy Google OAuth verification packet

This packet prepares the Google Cloud data-access submission for Tapy. It is
not proof of approval and does not replace Google's OAuth review or the annual
security assessment for server-side restricted-scope data. Recheck the linked
Google requirements immediately before submission.

## Submission values

Use these values in the dedicated production Google Cloud project:

| Field | Value |
| --- | --- |
| App name | `Tapy` |
| Application home page | `https://tapy.547600.xyz/` |
| Privacy policy | `https://tapy.547600.xyz/privacy` |
| Terms of service | `https://tapy.547600.xyz/terms` |
| Authorized domain | `547600.xyz` |
| Web redirect URI | `https://tapy.547600.xyz/v1/oauth/gmail/callback` |

Declare exactly these Google scopes under **Google Auth Platform > Data
Access**:

| Flow | Scope | Purpose |
| --- | --- | --- |
| Google sign-in | `openid` | Associate the authenticated Google subject with a Tapy account. |
| Google sign-in | `https://www.googleapis.com/auth/userinfo.email` | Obtain the user's verified email address for their Tapy identity. |
| Google sign-in | `https://www.googleapis.com/auth/userinfo.profile` | Obtain the user's display name for their Tapy profile. |
| Gmail connection | `https://www.googleapis.com/auth/gmail.readonly` | Read bounded recent messages to identify flight and hotel confirmations. |

The source uses the aliases `openid email profile` for sign-in and requests
only `gmail.readonly` in the separate mailbox connection flow. Do not declare
or request `gmail.modify`, `mail.google.com`, send, compose, settings, or any
other Gmail scope.

Before submitting, confirm that the client ID in the production deployment is
the client from the production project. Do not copy the client ID currently in
the repository blindly: the production-registration step may replace it. Keep
only the production Web client in this project when practical. Google's video
requirements cover every OAuth client assigned to the project, so remove unused
clients or demonstrate each remaining client.

## Paste-ready scope justification

Use this for `gmail.readonly`, adjusting only if the deployed behavior changes:

> Tapy is a workspace for travel professionals. After an authenticated user
> opens Settings, reads the mailbox-processing disclosure, and selects “Grant
> read access,” Tapy uses `gmail.readonly` to list at most 20 recent messages
> matching `newer_than:365d` per scan and retrieve their full payloads. Tapy
> processes each selected message's subject, sender, date, and up to 40,000
> characters of plain-text body to identify flight and hotel confirmations.
> It sends that content to Alibaba Cloud Model Studio (Qwen) solely for
> classification and booking-fact extraction, then displays derived bookings
> and hotel opportunities to the user. Tapy does not send, edit, or delete
> Gmail messages, does not store full message bodies or access tokens in its
> product database, and does not use Google data for advertising, credit
> decisions, sale, or generalized AI training. Read access is required because
> the user-facing feature must inspect message bodies. `gmail.metadata` cannot
> provide those bodies, while write-capable scopes are broader than necessary.

If the form asks for the permitted application type, select the option closest
to **reporting or monitoring that improves the email experience**, specifically
automated travel itineraries or flight tracking. Do not describe Tapy as a
generic data-export or AI-training tool.

For the identity scopes, use:

> Tapy offers optional Google sign-in. `openid`, `userinfo.email`, and
> `userinfo.profile` are used only to authenticate the user, associate the
> stable Google subject with a Tapy account, verify the account email, and
> initialize the display name. Google sign-in alone does not connect or read
> the user's mailbox; Gmail access is requested later, separately, and only
> after the in-product mailbox disclosure.

## Paste-ready reviewer instructions

Put the actual reviewer account credentials only in Google's protected review
field or a direct reply requested by the review team. Never add them to this
repository, the video, or an ordinary support document.

> 1. Open `https://tapy.547600.xyz/`. The home page and legal pages are public.
> 2. Sign in with the Tapy reviewer email/password supplied in the protected
>    review field. Google sign-in is also supported, but is not required to
>    reach the mailbox feature.
> 3. Select **Settings**. Under **Email permissions**, read the disclosure that
>    explains the bounded read-only scan and transfer to Alibaba Cloud Qwen.
> 4. In the Gmail row, select **Grant read access**, choose a Google account
>    controlled by the reviewer, review the requested permission, and grant it.
>    The callback returns to Tapy and the Gmail row shows the connected address.
> 5. For a deterministic test, place the synthetic flight-confirmation email
>    from this packet in that Gmail inbox, then select **Scan now**. Tapy reads a
>    maximum of 20 recent messages and returns to **My data**. A successful scan
>    displays a new flight-derived hotel opportunity and a completion notice.
> 6. Return to **Settings** and select **Disconnect**. This deletes Tapy's stored
>    Gmail connection credentials, webhook metadata, and duplicate-processing
>    records. To revoke the Google grant itself, also remove Tapy under the
>    Google Account's third-party connections page.
> 7. Privacy questions or deletion requests can be sent to the support address
>    published in `https://tapy.547600.xyz/privacy`.

### Synthetic Gmail message

Send this from another test account to the Gmail account used in the demo. Use
only invented data. Keep it among the newest 20 messages and send it after any
earlier Tapy test of the same mailbox so its provider message ID is new.

```text
Subject: Confirmed flight - Tapy verification demo TAPY-DEMO-2026

This is a synthetic booking confirmation created only for the Tapy OAuth review.

Status: CONFIRMED
Booking reference / PNR: DEMO42
Passenger: Ada Demo
Passenger email: ada.demo@example.com
Passenger phone: +1 202 555 0147
Ticket number: 9990000000420
Airline: Example Air
Flight number: EX742
From: TLV
To: LHR
Departure: 2026-11-15 09:00 Asia/Jerusalem (UTC+02:00)
Arrival: 2026-11-15 12:15 Europe/London (UTC+00:00)
Ticket price: EUR 450.00

No hotel is included in this synthetic booking.
```

If recording after these dates are no longer future dates, replace both travel
dates with dates at least 30 days after the recording date while preserving the
time-zone offsets.

## Privacy-policy evidence

The production policy is public at `https://tapy.547600.xyz/privacy`. Confirm
the deployed page still matches the source immediately before submission.

| Required disclosure | Published evidence | Implementation evidence |
| --- | --- | --- |
| Access | Sections 2 and 4 identify `gmail.readonly`, mailbox identifiers, and the bounded message fields. | `GMAIL_SCOPE` and `OAuthService._authorization_url` in `services/tapy/backend/src/tapy/oauth.py`; `GmailReader.messages` in `mailboxes.py`. |
| Processing and purpose | Sections 3 and 4 limit use to classifying confirmations, extracting booking facts, matching, and the visible workflow. | `scan` in `services/tapy/backend/src/tapy/api.py`; the strict extraction schema in `models.py`. |
| Sharing and transfer | Section 5 names Alibaba Cloud Model Studio (Qwen), the transferred fields, and the no-generalized-training restriction. | The external AI queue is selected in `kubernetes/system/tapy/overlays/homelab/config-patch.yaml`. |
| Storage and retention | Sections 2, 4, and 6 distinguish encrypted refresh tokens and derived facts from transient bodies and access tokens. | `MailboxConnection` and `ProcessedMessage` in `database.py`; bodies are passed to extraction but are not database columns. |
| Disconnection and revocation | Section 6 explains both Tapy disconnection and separate provider revocation. | `DELETE /v1/mailboxes/{provider}` in `api.py`; **Disconnect** in `UserPages.tsx`. |
| Deletion and user rights | Sections 6 and 8 give the deletion channel and describe access, correction, deletion, export, restriction, and objection rights. | The published support email is the operational deletion channel; account/derived-data deletion is currently handled manually. |
| Limited Use | Sections 3 and 4 state the prohibited uses and affirmative Google Limited Use commitment. | The public home page repeats the data purpose, no-sale, no-advertising, no-credit, and no-generalized-training statements. |

The legal page is evidence of the disclosure, not by itself evidence that every
processor term and operational control complies with Google's policy.

## External processor evidence still required

Alibaba Cloud's current public Model Studio documentation says customer data is
not used for model training without consent and describes encryption, but those
statements do not fully establish retention, human access, deletion, and onward
transfer for the exact account, region, endpoint, and model used by Tapy.

Before submission, the operator must retain an evidence bundle containing:

- the applicable Alibaba Cloud international product terms and data-processing
  agreement for the production account and region;
- the exact Model Studio region, endpoint, workspace, model, and logging/data
  controls used by the deployed worker;
- written confirmation of inference input/output retention and deletion;
- the training-use rule, including whether any opt-in, log-backflow, evaluation,
  or fine-tuning setting can override the default;
- the permitted human-access cases and confidentiality controls; and
- all subprocessors, processing locations, and onward-transfer safeguards.

If the evidence conflicts with Tapy's public disclosure or Google Workspace's
Limited Use rules, stop the Gmail production submission and change the data flow
or processor terms first.

## Demo video preparation

Record only after the production branding, production OAuth client, scope list,
redirect URI, and the new **Scan now** UI are deployed. Google's current
[restricted-scope guide](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
requires an unlisted YouTube video showing the English OAuth grant process, the
correct app name, the OAuth client ID in the consent-page address bar, and the
functionality enabled by every requested scope. Google's
[demo-video checklist](https://support.google.com/cloud/answer/13804565) also
requires the complete consent screen and the same app, branding, and scopes as
the submission.

Prepare the recording as follows:

1. Create a dedicated Tapy reviewer account and a dedicated Gmail test mailbox.
   Put only synthetic messages in both. Do not record real customer, traveler,
   project, billing, API-key, or OAuth-secret data.
2. In Tapy, disconnect Gmail. Then remove Tapy from the test Google Account's
   third-party connections so both consent sequences appear from a clean state.
3. Use a clean browser profile. Set the Google consent page's language selector
   to **English**, browser zoom to a readable value, and recording to at least
   1080p. Disable notifications and extensions that expose personal data.
4. Put the synthetic email in the Gmail inbox. It must be recent, unprocessed,
   and within the scan's newest 20 matching messages.
5. Test the complete flow once with a different synthetic message, then reset
   both the Tapy connection and Google grant again before the real take.
6. Prefer one continuous take for the authorization sequence. If processing
   needs a long wait, visibly caption the time cut; never cut away a warning,
   consent choice, requested permission, or error.

### Recording script and shot list

Aim for roughly five minutes. The narration can be spoken or added as captions.

1. **Public identity, 0:00-0:35.** Show the full production URL and Tapy home
   page. Scroll through **How it works** and **Why Tapy requests Google user
   data**. Say: “This is Tapy, an invitation-based workspace for travel
   professionals. Gmail connection is optional and separate from sign-in.”
2. **Public policy, 0:35-1:05.** Open the Privacy Policy from the home page.
   Briefly show sections 2, 4, 5, 6, and 8. Say: “The policy identifies the
   Gmail permission, the data processed, the Qwen transfer, retention,
   disconnection, provider revocation, and deletion channel.”
3. **Google sign-in, 1:05-1:55.** Return home and select **Continue with
   Google**. Show the entire English Google flow. Make `Tapy` visible on the
   consent screen. Expand permission details. Maximize the window if needed,
   click the address bar, and visibly show that its `client_id` is the production
   client submitted for review. Say: “These identity permissions authenticate
   the user and initialize email and display name. They do not read Gmail.”
4. **Contextual mailbox consent, 1:55-2:35.** Open **Settings** and pause on the
   **Email permissions** disclosure long enough to read it. Select **Grant read
   access** for Gmail. Say: “The user initiates mailbox access here, after Tapy
   explains the bounded scan and transfer to Alibaba Cloud Qwen.”
5. **Restricted permission, 2:35-3:20.** Show the complete English consent
   screen without cropping. Expand all details so the Gmail read permission is
   visible. Again show `Tapy` and the production OAuth client ID in the address
   bar, then grant access. Do not edit out an unverified-app warning if one is
   present. Say: “Tapy requests only `gmail.readonly`; it cannot send, edit, or
   delete messages.”
6. **Scope in use, 3:20-4:25.** Show the connected Gmail address in Settings and
   select **Scan now**. Keep the scan-complete notice visible, then show the new
   opportunity in **My data** with the synthetic passenger, flight, destination,
   and date. Say: “Tapy read the synthetic message body, classified it as a
   flight confirmation, extracted booking facts, and displayed the resulting
   hotel opportunity. Full message bodies are not stored in Tapy's product
   database.”
7. **User control, 4:25-4:55.** Return to Settings and select **Disconnect**.
   Show that Gmail is no longer connected. Say: “Disconnecting deletes Tapy's
   stored mailbox credential and watch metadata and stops new access. The
   Privacy Policy explains separate Google-account revocation and how to request
   deletion of derived records.”

Before submitting the link, open it in a signed-out/incognito browser and prove
that it plays without an access request. Use **Unlisted**, not **Private**, in
YouTube Studio. Keep the source recording and the exact production revision
shown in it until the review is complete.

## Final operator checklist

- [ ] Dedicated production project and Web OAuth client are in use.
- [ ] Every OAuth client left in the production project is shown in the video.
- [ ] Production branding is published and shows `Tapy`.
- [ ] Home, privacy, terms, authorized domain, and redirect URI match this packet.
- [ ] Only the four documented scopes are declared and requested.
- [ ] Search Console domain ownership is verified by a project Owner or Editor.
- [ ] Public pages work when signed out and match deployed behavior.
- [ ] Reviewer credentials are supplied only through Google's protected channel.
- [ ] Demo video shows both Google sign-in and Gmail connection consent flows.
- [ ] Video shows the production client ID, English consent screen, and full scope details.
- [ ] Video visibly demonstrates **Scan now** producing a user-facing result.
- [ ] External processor evidence is complete and policy-compatible.
- [ ] Required restricted-scope security assessment work is scheduled or complete.
- [ ] The unlisted video link works in a signed-out browser.

Official references: [submission procedure](https://support.google.com/cloud/answer/13461325),
[restricted-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification),
[Google Workspace user data policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy),
and [Alibaba Cloud Model Studio privacy information](https://www.alibabacloud.com/help/en/model-studio/privacy-notice).
