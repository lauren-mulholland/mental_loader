"""Exercise birthdays in an isolated local browser fixture; no model or Google calls."""

import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

from mental_loader.storage import Household, database, digest

root = Path.cwd()
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
                page.locator("#birthday-editor summary").click()
                form = page.locator("#birthday-form")
                form.locator("[name=recipient]").fill("Sam")
                form.locator("[name=occasion_date]").fill("2027-01-20")
                form.locator("[name=preferences]").fill("Gardening")
                form.locator("[name=budget]").fill("40.00")
                form.locator("[type=submit]").click()
                expect(page.locator("#birthdays")).to_contain_text("Sam")
                card = page.locator(".birthday-card").last
                card.get_by_role("button", name="Edit", exact=True).click()
                form.locator("[name=gift_status]").select_option("ready")
                form.locator("[type=submit]").click()
                expect(card).to_contain_text("ready")
                page.reload()
                expect(page.locator("#birthdays")).to_contain_text("ready")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                page.screenshot(
                    path=f"{temporary}/birthday-{viewport['width']}.png", full_page=True
                )
                context.close()
            browser.close()
        print(
            "PASS: desktop/mobile birthday creation, editing, completion, reload; "
            "no JS errors or horizontal overflow"
        )
    finally:
        process.terminate()
        process.wait(timeout=10)
