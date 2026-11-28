"""Exercise the running PostgreSQL-backed app; no model calls or real push sends."""

import json
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def main():
    codes = json.loads(Path(".local/access-codes.json").read_text())
    screenshot_dir = Path(".local/screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        options = {"executable_path": str(chrome)} if chrome.exists() else {}
        browser = playwright.chromium.launch(headless=True, **options)
        desktop = browser.new_context(viewport={"width": 1440, "height": 1040})
        page = desktop.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://localhost:8000")
        page.locator("#access-code").fill(codes["My household"]["access_code"])
        page.get_by_role("button", name="Open my household").click()
        page.locator("#workspace").wait_for(state="visible")
        expect(page.locator("#send")).to_be_enabled()
        page.locator("[data-view=assistant]").click()
        page.locator("#message-input").fill(f"/remind 1m Feasibility check {str(uuid4())[:8]}")
        page.get_by_role("button", name="Send message").click()
        page.locator(".message.assistant").filter(has_text="Saved:").last.wait_for(timeout=15000)
        conversation_url = page.url
        page.reload()
        page.locator(".message.assistant").filter(has_text="Saved:").last.wait_for(timeout=10000)
        page.screenshot(path=str(screenshot_dir / "desktop.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        phone = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        mobile = phone.new_page()
        mobile.goto(conversation_url)
        mobile.locator("#access-code").fill(codes["My household"]["access_code"])
        mobile.get_by_role("button", name="Open my household").click()
        mobile.locator(".message.assistant").filter(has_text="Saved:").last.wait_for(timeout=10000)
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        mobile.screenshot(path=str(screenshot_dir / "mobile.png"), full_page=True)

        other = browser.new_context()
        other_page = other.new_page()
        other_page.goto(conversation_url)
        other_page.locator("#access-code").fill(codes["Isolation test household"]["access_code"])
        other_page.get_by_role("button", name="Open my household").click()
        other_page.locator(".welcome").wait_for(timeout=10000)
        assert other_page.locator(".message").count() == 0
        assert other_page.locator("#task-count").inner_text() == "0"
        assert not errors, errors
        browser.close()
    print(
        "PASS: desktop + mobile layout, saved chat after reload, cross-device history, "
        "second-household isolation; screenshots in .local/screenshots."
    )


if __name__ == "__main__":
    main()
