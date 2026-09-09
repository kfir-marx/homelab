import type { Metadata } from "next";
import LegalDocument from "../components/LegalDocument";

export const metadata: Metadata = {
  title: "Privacy Policy — Tapy",
  description: "How Tapy collects, uses, shares, retains, and protects personal data.",
  alternates: {
    canonical: "/privacy",
  },
};

export default function PrivacyPolicy() {
  return (
    <LegalDocument
      title="Privacy Policy"
      summary="This policy explains how Tapy handles account information, connected mailbox data, and the traveler data our pilot customers entrust to the service."
    >
      <section>
        <h2>1. Who we are and who this policy covers</h2>
        <p>
          Tapy is an early-stage, invitation-based software service operated by Kfir Marx
          (&quot;Tapy,&quot; &quot;we,&quot; &quot;us,&quot; or &quot;our&quot;). Tapy helps travel
          professionals identify flight and hotel confirmations, manage hotel-upsell
          opportunities, and contact travelers.
        </p>
        <p>
          This policy applies to Tapy account holders and to personal data about travelers or
          other people that a pilot customer places in Tapy. For account, security, and service
          administration data, Tapy determines why and how the data is used. When a business
          customer supplies traveler data for Tapy to process on its behalf, that customer
          remains responsible for its relationship with those travelers and Tapy processes the
          data to provide the requested service.
        </p>
      </section>

      <section>
        <h2>2. Data we collect</h2>
        <ul>
          <li>
            <strong>Account and authentication data:</strong> name, work email address,
            language preference, password hash, session records, and Google or Microsoft sign-in
            identifiers.
          </li>
          <li>
            <strong>Connected mailbox data:</strong> mailbox address, provider account ID,
            granted scopes, an encrypted refresh token, webhook/subscription details, and provider
            message IDs used to avoid duplicate processing.
          </li>
          <li>
            <strong>Email content used for matching:</strong> the subject, sender, sent date, and
            up to 40,000 characters of plain-text content from a bounded set of Gmail or
            Outlook messages. A scan processes up to 10,000 messages by default, in pages. Messages may
            include unrelated content because classification is needed to determine whether a
            message is a relevant booking confirmation.
          </li>
          <li>
            <strong>Booking and traveler data:</strong> booking and ticket references, passenger
            names, email addresses, phone numbers, party size, routes, destinations, travel dates,
            booking costs, hotel-match details, upsell status, and related notifications. This may
            be entered by a user or extracted from a connected mailbox.
          </li>
          <li>
            <strong>Assistant and communications data:</strong> questions submitted to the Tapy
            assistant, the dashboard snapshot supplied with those questions, WhatsApp destination
            numbers and message content, delivery identifiers, and failure information.
          </li>
          <li>
            <strong>Technical data:</strong> security and operational logs, request timing,
            error information, and similar data needed to operate and protect the pilot. We do not
            use advertising cookies or third-party advertising trackers.
          </li>
        </ul>
      </section>

      <section>
        <h2>3. How we use data</h2>
        <p>We use personal data only as needed to:</p>
        <ul>
          <li>create and secure accounts and maintain authenticated sessions;</li>
          <li>connect a mailbox after the user grants read-only access;</li>
          <li>
            classify mailbox messages and extract explicit flight and hotel booking details;
          </li>
          <li>
            deduplicate and match bookings, display dashboards, and notify users about changes;
          </li>
          <li>send a user-approved WhatsApp hotel offer to a traveler;</li>
          <li>provide the optional dashboard assistant;</li>
          <li>support pilot users, prevent abuse, diagnose failures, and secure the service; and</li>
          <li>comply with law and enforce our agreements.</li>
        </ul>
        <p>
          We do not sell personal data, use it for advertising, build advertising profiles, or
          use Google Workspace data to determine creditworthiness. We do not use, or permit our
          service providers to use, Google Workspace data to train generalized AI models.
        </p>
      </section>

      <section>
        <h2>4. Google and Microsoft mailbox access</h2>
        <p>
          Mailbox connection is optional and separate from signing in. For Gmail, Tapy requests
          delegated <code>gmail.readonly</code> access. For Outlook, Tapy requests delegated
          <code>Mail.Read</code>, <code>User.Read</code>, and <code>offline_access</code>. Tapy
          cannot send, edit, or delete mailbox messages with these permissions.
        </p>
        <p>
          Tapy retrieves recent message content only to identify confirmed flight and hotel
          bookings and power the visible matching workflow. Full email bodies and provider access
          tokens are not stored in Tapy&apos;s product database. Email content can remain briefly in
          the private processing queue while a classification request is pending. Tapy stores the
          booking facts that were extracted, small match summaries, and provider message IDs so it
          can present results and avoid processing the same message twice.
        </p>
        <p>
          Tapy&apos;s use and transfer of information received from Google APIs adheres to the
          <a href="https://developers.google.com/terms/api-services-user-data-policy"> Google API Services User Data Policy</a>, including the Limited Use requirements.
        </p>
      </section>

      <section>
        <h2>5. Service providers and data sharing</h2>
        <p>We disclose data only as needed to operate Tapy:</p>
        <ul>
          <li>
            <strong>Google and Microsoft</strong> provide sign-in, mailbox authorization, mailbox
            APIs, and change notifications.
          </li>
          <li>
            <strong>Alibaba Cloud Model Studio (Qwen)</strong> receives the selected message
            subject, sender, date, and body text to classify and extract booking details. We do not
            authorize use of this content for generalized model training.
          </li>
          <li>
            <strong>Twilio and WhatsApp</strong> receive the traveler&apos;s phone number, offer text,
            and related delivery data when a Tapy user chooses to send an upsell message.
          </li>
          <li>
            <strong>Infrastructure and professional support providers</strong> may process limited
            data to host, secure, troubleshoot, or legally support the pilot under confidentiality
            and data-protection obligations.
          </li>
        </ul>
        <p>
          We may also disclose information when required by law, to protect people or the service,
          or in a business transfer subject to appropriate safeguards and notice where required.
          We do not allow humans to read connected-mailbox content except with specific consent for
          support, when necessary to investigate security or abuse, or when legally required.
        </p>
      </section>

      <section>
        <h2>6. Retention and deletion</h2>
        <p>
          We retain account and derived booking data while the pilot account is active and for as
          long as reasonably needed to provide the service, meet legal obligations, resolve
          disputes, and maintain security. We do not keep full mailbox message bodies in the Tapy
          product database. We retain extracted booking facts, source headers such as subject,
          sender and date, and decision history so matches can be explained and corrected. Operational logs are retained only for a limited troubleshooting and
          security period. Residual copies may remain in protected backups until those backups are
          overwritten through the normal backup cycle.
        </p>
        <p>
          Disconnecting a mailbox stops new mailbox access and deletes Tapy&apos;s stored connection
          credentials, webhook metadata, and duplicate-processing records for that connection. It
          does not automatically delete booking records already extracted from messages. It also
          does not revoke the grant at Google or Microsoft; the user or administrator can revoke
          that grant in the provider&apos;s account-security settings.
        </p>
        <p>
          To request deletion of an account or previously derived booking data, email
          <a href="mailto:kfir.marx@gmail.com"> kfir.marx@gmail.com</a>. We will verify the
          requester&apos;s authority and remove data from active systems without undue delay, unless
          retention is required by law. Travelers may submit a request directly, but we may refer
          it to the travel business that controls the relevant customer relationship.
        </p>
      </section>

      <section>
        <h2>7. Security and international processing</h2>
        <p>
          Tapy uses HTTPS in transit, application-level encryption for stored mailbox refresh
          tokens, salted password hashing, restricted service access, and separation between the
          public frontend and private processing services. No system is completely secure, and the
          pilot does not promise that a security incident can never occur. If an incident affects
          personal data, we will investigate and provide notices required by applicable law and
          customer agreements.
        </p>
        <p>
          Tapy and its providers may process data in countries other than the country where the
          user or traveler is located. Where required, we use contractual or other lawful transfer
          safeguards.
        </p>
      </section>

      <section>
        <h2>8. Choices and privacy rights</h2>
        <p>
          Depending on applicable law, a person may have rights to access, correct, delete, export,
          restrict, or object to processing of personal data, and to complain to a privacy
          regulator. Account holders can update their name and disconnect mailboxes in Settings.
          Other requests can be sent to <a href="mailto:kfir.marx@gmail.com">kfir.marx@gmail.com</a>.
          We may need to verify identity and authority before acting.
        </p>
      </section>

      <section>
        <h2>9. Children</h2>
        <p>
          Tapy is a business service and is not directed to children under 18. Pilot customers
          must not knowingly submit children&apos;s personal data unless it is necessary for a lawful
          travel service and they have all required authority and safeguards.
        </p>
      </section>

      <section>
        <h2>10. Changes and contact</h2>
        <p>
          We may update this policy as the pilot and its providers change. Material changes will be
          communicated through the service or directly to pilot customers, and the effective date
          above will be revised.
        </p>
        <p>
          Privacy questions and requests: <a href="mailto:kfir.marx@gmail.com">kfir.marx@gmail.com</a>.
        </p>
      </section>
    </LegalDocument>
  );
}
