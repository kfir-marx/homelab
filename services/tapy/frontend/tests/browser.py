"""Browser regressions against a local production build; every API is intercepted.

TAPY_TEST_URL=http://127.0.0.1:3100 python tests/browser.py
Requires playwright==1.62.0 and Chromium (or TAPY_TEST_CHROMIUM).
"""
import asyncio
import copy
import os
import unittest
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

USER = {"id": "u1", "name": "Agent", "email": "agent@example.com", "language": "en",
        "active_organization_id": "org1", "active_organization_role": "admin", "auth_providers": ["google"],
        "memberships": [{"organization_id": "org1", "organization_name": "Agency", "role": "admin"}], "mailboxes": []}
CARD = {"id": "o1", "booking_id": "b1", "status": "open", "version": 1, "destination": "LHR",
        "service_start": "2099-10-12T12:00:00Z", "service_end": "2099-10-20T12:00:00Z",
        "flight_details": {"booking_reference": "BOOK1", "travelers": ["Ada Lovelace"], "pnrs": ["PNR1"],
                           "segments": [{"airline": "Example Air", "flight_number": "EA1", "origin_code": "TLV", "destination_code": "LHR", "departure_at": "2099-10-12T08:00:00Z"}]},
        "recipients": [{"id": "r1", "person": {"id": "p1", "display_name": "Ada Lovelace", "contacts": [{"id": "c1", "channel": "whatsapp", "display_value": "+15555550123"}]}, "contact_point_id": "c1", "selection_status": "selected"}]}
METRICS = {"monetary_totals": [], "per_agent": [], "total_opportunities": 1, "open_opportunities": 1}


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        options = {"headless": True}
        if os.environ.get("TAPY_TEST_CHROMIUM"):
            options["executable_path"] = os.environ["TAPY_TEST_CHROMIUM"]
        self.browser = await self.pw.chromium.launch(**options)
        self.page = await self.browser.new_page()
        self.user = copy.deepcopy(USER)
        self.card = copy.deepcopy(CARD)
        self.hold_refresh = False
        self.fail_action = False
        self.release = asyncio.Event()
        self.post_count = 0
        await self.page.route("**/v1/**", self.api)
        await self.page.goto(os.environ.get("TAPY_TEST_URL", "http://127.0.0.1:3100"))
        await expect(self.page.get_by_role("button", name="Dismiss", exact=True)).to_be_visible()

    async def asyncTearDown(self):
        self.release.set()
        await self.browser.close()
        await self.pw.stop()

    async def api(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        if method == "GET" and path != "/v1/events" and self.hold_refresh:
            await self.release.wait()
        if method in {"POST", "PATCH", "DELETE"}:
            self.post_count += 1
            if self.fail_action:
                return await route.fulfill(status=503, json={"detail": "Please try again"})
            if path.endswith("/send"):
                return await route.fulfill(status=202, json={"id": "j1", "kind": "send", "status": "queued", "progress": {}, "result": {}, "attempts": 0, "error": None})
            if path == "/v1/opportunities/o1":
                self.card.update(status="declined", version=2)
                return await route.fulfill(json=self.card)
            if method == "DELETE":
                self.user["mailboxes"] = []
                return await route.fulfill(status=204)
        values = {"/v1/users/me": self.user, "/v1/bookings": [], "/v1/opportunities": [self.card],
                  "/v1/metrics": METRICS, "/v1/notifications": [], "/v1/jobs": []}
        if path == "/v1/events":
            return await route.fulfill(content_type="text/event-stream", body=": keepalive\n\n")
        await route.fulfill(json=values.get(path, {}))

    async def test_card_shows_known_details_and_only_send_dismiss(self):
        card = self.page.get_by_role("article")
        await expect(card.get_by_text("Ada Lovelace", exact=True).first).to_be_visible()
        await expect(card.get_by_text("PNR: PNR1")).to_be_visible()
        await expect(card.get_by_text("Stay from 2099-10-12 to 2099-10-20")).to_be_visible()
        self.assertEqual(await card.get_by_role("button").all_text_contents(), ["Send accommodation link", "Dismiss"])
        self.assertNotRegex((await card.inner_text()).lower(), r"commission|won|success|booking completed")

    async def test_mailbox_callback_and_disconnect_update_before_refresh(self):
        await self.page.get_by_role("button", name="Settings", exact=True).click()
        await expect(self.page.get_by_role("heading", name="Email permissions")).to_be_visible()
        self.hold_refresh = True
        mailbox = {"provider": "gmail", "email_address": "agent@gmail.com", "webhook_active": False}
        self.user["mailboxes"] = [mailbox]
        await self.page.evaluate("mailbox => window.postMessage({type: 'tapy-mailbox-connected', mailbox}, location.origin)", mailbox)
        await expect(self.page.get_by_role("button", name="Scan now", exact=True)).to_be_visible(timeout=2000)
        await expect(self.page.get_by_role("button", name="Disconnect", exact=True)).to_be_visible()
        await self.page.get_by_role("button", name="Disconnect", exact=True).click()
        await expect(self.page.get_by_role("button", name="Scan now", exact=True)).to_have_count(0, timeout=2000)

    async def test_dismiss_is_immediate_when_dashboard_refresh_is_stalled(self):
        self.hold_refresh = True
        await self.page.get_by_role("button", name="Dismiss", exact=True).click()
        await expect(self.page.get_by_role("article")).to_have_count(0, timeout=2000)
        self.assertEqual(self.post_count, 1)

    async def test_send_is_immediate_and_exposes_queued_job(self):
        self.hold_refresh = True
        await self.page.get_by_role("button", name="Send accommodation link", exact=True).click()
        await expect(self.page.get_by_role("article")).to_have_count(0, timeout=2000)
        await expect(self.page.get_by_role("region", name="Background activity")).to_contain_text("queued")
        self.assertEqual(self.post_count, 1)

    async def test_logout_cannot_be_undone_by_an_older_refresh(self):
        self.hold_refresh = True
        await self.page.get_by_role("button", name="My data", exact=True).click()
        await self.page.get_by_role("button", name="Sign out", exact=True).click()
        await expect(self.page.get_by_role("button", name="Settings", exact=True)).to_have_count(0)
        self.release.set()
        await self.page.wait_for_timeout(200)
        await expect(self.page.get_by_role("button", name="Settings", exact=True)).to_have_count(0)

    async def test_failed_action_keeps_card_actionable(self):
        self.fail_action = True
        await self.page.get_by_role("button", name="Dismiss", exact=True).click()
        await expect(self.page.get_by_role("article")).to_have_count(1)
        await expect(self.page.get_by_text("Please try again", exact=True)).to_be_visible()
        await expect(self.page.get_by_role("article").get_by_role("button", name="Dismiss", exact=True)).to_be_enabled()


if __name__ == "__main__":
    unittest.main(verbosity=2)
