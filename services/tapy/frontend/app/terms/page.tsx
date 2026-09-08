import type { Metadata } from "next";
import LegalDocument from "../components/LegalDocument";

export const metadata: Metadata = {
  title: "Terms of Service — Tapy",
  description: "Terms governing use of the Tapy pilot service.",
  alternates: {
    canonical: "/terms",
  },
};

export default function TermsOfService() {
  return (
    <LegalDocument
      title="Terms of Service"
      summary="These terms govern access to Tapy’s invitation-based pilot service, including use with real traveler and booking data."
    >
      <section>
        <h2>1. Agreement and the pilot</h2>
        <p>
          These Terms of Service (&quot;Terms&quot;) are an agreement between Kfir Marx, operator
          of Tapy (&quot;Tapy,&quot; &quot;we,&quot; &quot;us,&quot; or &quot;our&quot;), and the
          person or organization using the service (&quot;Customer&quot; or &quot;you&quot;). By
          creating an account, accepting a pilot invitation, or using Tapy, you agree to these
          Terms. If you use Tapy for an organization, you represent that you can bind it to these
          Terms.
        </p>
        <p>
          Tapy is an early-stage pilot. Although the pilot may process real production data, it is
          still being evaluated and may contain errors, change materially, or experience downtime.
          There is no service-level commitment unless we agree to one in writing.
        </p>
      </section>

      <section>
        <h2>2. The service</h2>
        <p>
          Tapy helps travel professionals identify flight and hotel confirmations, extract and
          match booking details, manage hotel-upsell opportunities, and send user-approved traveler
          communications. Tapy is a workflow aid, not a travel agent, airline, hotel, reservation
          system, payment processor, or emergency service. Tapy does not itself make, modify, or
          cancel a booking.
        </p>
      </section>

      <section>
        <h2>3. Accounts and authorized users</h2>
        <ul>
          <li>You must be at least 18 and provide accurate account information.</li>
          <li>
            You are responsible for authorized users, account credentials, and activity under your
            account.
          </li>
          <li>
            You must promptly report suspected unauthorized access and must not share accounts
            outside your organization without our approval.
          </li>
          <li>
            Access is invitation-based during the pilot. We may limit users, usage, integrations,
            or features to keep the pilot safe and reliable.
          </li>
        </ul>
      </section>

      <section>
        <h2>4. Customer and traveler data</h2>
        <p>
          As between the parties, Customer retains its rights in data submitted to or retrieved by
          Tapy on Customer&apos;s behalf (&quot;Customer Data&quot;). Customer instructs Tapy to
          process Customer Data only to provide, secure, support, and improve the Customer-facing
          service as described in the Privacy Policy and any written pilot agreement.
        </p>
        <p>Customer represents and warrants that it:</p>
        <ul>
          <li>
            has a lawful basis and all necessary rights, notices, consents, and instructions to
            provide Customer Data, including its customers&apos; production data, to Tapy;
          </li>
          <li>
            will respond to traveler or data-subject requests and will tell Tapy when assistance is
            needed;
          </li>
          <li>
            will not submit prohibited sensitive data unless Tapy has expressly agreed in writing
            to the required safeguards; and
          </li>
          <li>
            is responsible for the legality, accuracy, and content of communications it chooses to
            send through Tapy, including compliance with marketing, messaging, and opt-out rules.
          </li>
        </ul>
        <p>
          If applicable law requires a data processing agreement or other addendum, the parties
          must sign it before Customer expands the pilot beyond the agreed users and data.
        </p>
      </section>

      <section>
        <h2>5. Mailbox permissions and third-party services</h2>
        <p>
          Mailbox access is read-only, delegated by an individual user, and can be disconnected in
          Tapy. Customer is responsible for configuring its Google or Microsoft environment and
          ensuring that each connected user has authority to grant access. Revoking a provider
          grant may also require action in the Google or Microsoft account or administrator console.
        </p>
        <p>
          Tapy relies on third-party services, including Google, Microsoft, Alibaba Cloud Model
          Studio, Google Gemini, Twilio, and WhatsApp. Their services are governed by their own
          terms and may be changed, limited, or unavailable. Tapy is not responsible for a
          third-party service outside our reasonable control, but we remain responsible for our own
          obligations regarding Customer Data.
        </p>
      </section>

      <section>
        <h2>6. Acceptable use</h2>
        <p>You must not:</p>
        <ul>
          <li>use Tapy unlawfully or infringe another person&apos;s rights;</li>
          <li>access a mailbox, traveler record, or account without authorization;</li>
          <li>
            send spam, deceptive messages, unlawful marketing, or content that is abusive or
            harmful;
          </li>
          <li>
            probe, bypass, disable, or interfere with security, access controls, rate limits, or
            service operation;
          </li>
          <li>
            introduce malware, credentials, payment-card data, government identifiers, health
            information, or other unnecessary highly sensitive data; or
          </li>
          <li>
            resell, reverse engineer, or use Tapy or its output to build a competing service except
            where a restriction is prohibited by law.
          </li>
        </ul>
      </section>

      <section>
        <h2>7. Human review and automated results</h2>
        <p>
          Classification, extraction, matching, and assistant responses may be incomplete or
          incorrect. Customer must review material results and verify booking details with the
          relevant airline, hotel, traveler, or reservation system before relying on them. Customer
          must keep a human in control of traveler communications and business decisions. Tapy
          should not be used for safety-critical, emergency, eligibility, credit, employment,
          insurance, medical, or legal decisions.
        </p>
      </section>

      <section>
        <h2>8. Confidentiality and security</h2>
        <p>
          Each party will use the other party&apos;s non-public information only for the pilot,
          protect it with reasonable care, and disclose it only to people and service providers who
          need it and are subject to confidentiality obligations. These duties do not apply to
          information that is public through no breach, already lawfully known, independently
          developed, or lawfully received from another source. A party compelled to disclose
          confidential information will provide notice when legally permitted.
        </p>
        <p>
          Tapy uses reasonable technical and organizational safeguards appropriate to the pilot.
          Customer is responsible for its endpoint devices, identity-provider controls, internal
          access management, and secure use of exported or copied data.
        </p>
      </section>

      <section>
        <h2>9. Ownership and feedback</h2>
        <p>
          Tapy and its licensors own the service, software, branding, and related intellectual
          property. These Terms give Customer a limited, non-exclusive, non-transferable,
          revocable right to use the pilot for its internal business operations. Customer may
          provide feedback voluntarily, and Tapy may use that feedback without restriction or
          identifying Customer publicly.
        </p>
      </section>

      <section>
        <h2>10. Fees</h2>
        <p>
          The pilot is free unless a written pilot order or other agreement states otherwise.
          Before introducing or changing fees, we will provide notice and obtain agreement for the
          applicable paid period.
        </p>
      </section>

      <section>
        <h2>11. Suspension, termination, and data return</h2>
        <p>
          Either party may end the pilot at any time by notice. We may suspend access immediately
          when reasonably necessary to address a security risk, unlawful use, material breach, or
          threat to the service or others. Where practicable, we will give notice and an opportunity
          to cure.
        </p>
        <p>
          On termination, Customer must stop using Tapy. On request, we will provide a reasonable
          opportunity to retrieve Customer Data in an available format and then delete it as
          described in the Privacy Policy, except where retention is legally required. Sections
          that by their nature should survive termination will survive, including confidentiality,
          ownership, disclaimers, liability limits, and dispute terms.
        </p>
      </section>

      <section>
        <h2>12. Disclaimers</h2>
        <p>
          To the fullest extent permitted by law, the pilot is provided &quot;as is&quot; and
          &quot;as available.&quot; Tapy disclaims implied warranties of merchantability, fitness for
          a particular purpose, non-infringement, and any warranty that the service will be
          uninterrupted, secure, or error-free. Nothing in these Terms limits a warranty or right
          that cannot lawfully be excluded.
        </p>
      </section>

      <section>
        <h2>13. Limitation of liability</h2>
        <p>
          To the fullest extent permitted by law, neither party will be liable for indirect,
          incidental, special, consequential, exemplary, or punitive damages, or for lost profits,
          revenue, goodwill, or business opportunities. Tapy&apos;s total liability arising from the
          pilot will not exceed the fees Customer paid Tapy during the three months before the
          event giving rise to the claim or USD 100 if the pilot was free.
        </p>
        <p>
          These limits do not apply to fraud, willful misconduct, breach of confidentiality,
          infringement or misappropriation of the other party&apos;s intellectual property, payment
          obligations, or liability that cannot be limited by law.
        </p>
      </section>

      <section>
        <h2>14. Governing law and disputes</h2>
        <p>
          These Terms are governed by the laws of the State of Israel, without regard to conflict
          of laws rules. The competent courts in Tel Aviv-Jaffa, Israel will have exclusive
          jurisdiction, unless applicable law requires otherwise. Before filing a claim, each party
          will try in good faith for 30 days to resolve the dispute informally.
        </p>
      </section>

      <section>
        <h2>15. Changes and general terms</h2>
        <p>
          We may update these Terms as the pilot develops. We will notify pilot customers of a
          material change before it takes effect. Continued use after the effective date means
          acceptance; if Customer does not agree, it must stop using Tapy. Neither party may assign
          these Terms without the other&apos;s consent, except in connection with a merger,
          reorganization, or sale of substantially all relevant assets. If part of these Terms is
          unenforceable, the remainder stays effective. Failure to enforce a term is not a waiver.
          These Terms, the Privacy Policy, and any signed pilot agreement are the complete agreement
          about the pilot; a signed agreement controls if it expressly conflicts with these Terms.
        </p>
      </section>

      <section>
        <h2>16. Contact</h2>
        <p>
          Questions or legal notices may be sent to
          <a href="mailto:kfir.marx@gmail.com"> kfir.marx@gmail.com</a>.
        </p>
      </section>
    </LegalDocument>
  );
}
