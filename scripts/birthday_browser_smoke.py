"""Exercise birthdays in an isolated local browser fixture; no model or Google calls."""

import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from mental_loader.storage import Household, database, digest

root = Path.cwd()
output = root / ".local/screenshots/categories"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as temporary:
    url = f"sqlite:///{temporary}/browser.db"
    engine, sessions = database(url)
    code = "birthday-browser-fixture-" + "x" * 32
    with sessions.begin() as db:
        db.add(Household(name="Birthday test", access_hash=digest(code)))
    engine.dispose()
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "PUBLIC_ORIGIN": "http://localhost:8013",
        "PYTHONPATH": str(root / "src"),
        "AI_ENABLED": "false",
    }
    process = subprocess.Popen(
        [
            os.sys.executable,
            "-m",
            "uvicorn",
            "mental_loader.app:create_app",
            "--factory",
            "--port",
            "8013",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                if httpx.get("http://localhost:8013/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Isolated browser fixture did not start")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                headless=True,
            )
            for viewport in ({"width": 1440, "height": 1040}, {"width": 390, "height": 844}):
                context = browser.new_context(viewport=viewport)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto("http://localhost:8013")
                page.locator("#access-code").fill(code)
                page.locator("#login-form button").click()
                expect(page.locator("#send")).to_be_enabled()
                expect(page.locator("#events-panel")).to_be_visible()
                expect(page.locator("#assistant-panel")).to_be_hidden()
                page.locator("#birthday-editor summary").click()
                form = page.locator("#birthday-form")
                recipient = "Sam" if viewport["width"] == 1440 else "Alex"
                form.locator("[name=recipient]").fill(recipient)
                form.locator("[name=occasion_date]").fill("2027-01-20")
                form.locator("[name=preferences]").fill("Gardening")
                # A rejected save keeps the form and stable record identity for retry.
                page.route(
                    "**/api/events/birthdays/*",
                    lambda route: route.fulfill(
                        status=503, content_type="application/json", body='{"detail":"Try again"}'
                    ),
                    times=1,
                )
                form.locator("[type=submit]").click()
                expect(page.locator("#birthday-feedback")).to_contain_text(
                    "Your draft is still here"
                )
                expect(form.locator("[name=recipient]")).to_have_value(recipient)
                form.locator("[type=submit]").click()
                expect(page.locator("#birthdays")).to_contain_text(recipient)
                card = page.locator(".birthday-card").filter(
                    has=page.get_by_text(recipient, exact=True)
                )
                card.get_by_role("button", name="Edit", exact=True).click()
                form.locator("[name=gift_status]").select_option("ready")
                form.locator("[type=submit]").click()
                expect(card).to_contain_text("ready")
                page.reload()
                expect(page.locator("#birthdays")).to_contain_text("ready")
                expect(page.locator("#events-panel")).to_be_visible()
                page.locator("#people-setup").click()
                expect(
                    page.locator("#people-panel").get_by_label("Birthday card", exact=True)
                ).not_to_be_checked()
                page.locator("#people-next").click()
                expect(page.locator("#people-fields")).to_contain_text(recipient)
                page.locator("#people-next").click()
                page.locator("#people-next").click()
                expect(page.locator("#people-panel")).to_be_hidden()
                page.locator("#assistant-toggle").click()
                expect(page.locator("#assistant-panel")).to_be_visible()
                expect(page.locator("#events-panel")).to_be_visible()
                page.locator("#assistant-close").click()
                page.locator("#bulk-editor summary").click()
                page.locator("#bulk-form [name=budget]").fill("45")
                page.locator("#bulk-form button[type=submit]").click()
                expect(page.locator("#assistant-previews")).to_contain_text("Review")
                page.locator("#assistant-previews summary").click()
                page.get_by_role("button", name="Apply these changes", exact=True).click()
                expect(page.locator("#assistant-receipt")).to_contain_text("Updated")
                page.locator("#assistant-receipt").get_by_role("button", name="Undo").click()
                expect(page.locator("#assistant-receipt")).to_contain_text("restored")
                page.locator("#assistant-close").click()
                page.locator("#reminder-editor summary").click()
                reminder = page.locator("#reminder-form")
                reminder.locator("[name=title]").fill("Book a birthday lunch")
                reminder.locator("[name=due_at]").fill(
                    (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
                )
                reminder.locator("[type=submit]").click()
                expect(page.locator("#tasks")).to_contain_text("Book a birthday lunch")
                page.locator("#reminder-editor summary").click()
                for category in ("meals", "shopping", "stock"):
                    page.locator(f"[data-view={category}]").click()
                    expect(page.locator("#future-panel")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.locator("[data-view=assistant]").click()
                page.locator("#message-input").fill("A draft to keep")
                page.locator("[data-view=events]").click()
                page.locator("[data-view=assistant]").click()
                expect(page.locator("#message-input")).to_have_value("A draft to keep")
                page.locator("[data-view=settings]").click()
                expect(page.locator("#notification-panel")).to_be_visible()
                expect(page.locator("#setup-panel")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.locator("[data-view=events]").click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                page.screenshot(
                    path=str(output / f"events-{viewport['width']}.png"), full_page=True
                )
                page.locator("#tasks").get_by_role(
                    "button", name="Complete Book a birthday lunch", exact=True
                ).click()
                expect(page.locator("#tasks")).not_to_contain_text("Book a birthday lunch")
                page.locator("[data-view=assistant]").click()
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(
                    path=str(output / f"assistant-{viewport['width']}.png"), full_page=True
                )
                context.close()
            browser.close()
        print(
            "PASS: desktop/mobile birthday creation, editing, completion, reload; "
            "direct reminders, failed-save retry, category navigation, draft retention; "
            "no JS errors or horizontal overflow. Screenshots: .local/screenshots/categories/"
        )
    finally:
        process.terminate()
        process.wait(timeout=10)
