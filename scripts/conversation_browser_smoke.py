"""Temporary-account browser check for private chats; no personal services/model calls."""

import secrets
import socket
import tempfile
import threading
from pathlib import Path

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from mental_loader.accounts import provision_account
from mental_loader.app import create_app
from mental_loader.config import Settings
from mental_loader.storage import Household, digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    with (
        tempfile.TemporaryDirectory(prefix="ml-browser-") as temporary,
        socket.socket() as listener,
    ):
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{temporary}/app.db",
            public_origin=origin,
            ai_enabled=False,
        )
        app = create_app(settings)
        with app.state.sessions.begin() as db:
            household = Household(
                name="Synthetic browser household", access_hash=digest(secrets.token_urlsafe(32))
            )
            db.add(household)
            db.flush()
            _, owner_code = provision_account(db, household.id, "Owner", "owner")
            db.flush()
            member, member_code = provision_account(db, household.id, "Member", "member")
            member_id = member.id
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        output = ROOT / ".local/screenshots/phase2"
        output.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
                options = {"executable_path": str(chrome)} if chrome.exists() else {}
                with playwright.chromium.launch(headless=True, **options) as browser:
                    owner = browser.new_context(viewport={"width": 1440, "height": 1040})
                    member = browser.new_context(viewport={"width": 390, "height": 844})
                    for context, code in ((owner, owner_code), (member, member_code)):
                        response = context.request.post(
                            origin + "/api/login",
                            data={"access_code": code},
                            headers={"Origin": origin},
                        )
                        assert response.status == 200
                    page = owner.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(origin)
                    page.get_by_role("button", name="New private chat").click()
                    expect(page.locator("#chat-heading")).to_have_text("Private chat")
                    private_url = page.url
                    page.locator("#message-input").fill("/remind 1m Synthetic private reminder")
                    page.get_by_role("button", name="Send message").click()
                    expect(page.locator(".message.assistant").last).to_contain_text(
                        "Saved:", timeout=15000
                    )
                    other = member.new_page()
                    other.goto(private_url)
                    expect(other.locator("#chat-heading")).to_have_text("Household chat")
                    expect(other.locator("#task-count")).to_have_text("0")
                    expect(other.locator("#messages")).not_to_contain_text("Synthetic private")
                    page.locator("#participants-panel summary").click()
                    page.locator("#participant-select").select_option(member_id)
                    page.get_by_role("button", name="Add to chat").click()
                    expect(page.locator("#participants-list")).to_contain_text("Member")
                    other.goto(private_url)
                    expect(other.locator("#chat-heading")).to_have_text("Private chat")
                    expect(other.locator("#tasks")).to_contain_text("Synthetic private reminder")
                    expect(other.locator("#participants-panel")).to_be_hidden()
                    assert other.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    other.screenshot(path=str(output / "mobile.png"), full_page=True)
                    page.screenshot(path=str(output / "desktop.png"), full_page=True)
                    page.get_by_role("button", name="Remove Member from chat").click()
                    expect(page.locator("#participants-list")).not_to_contain_text("Member")
                    # Check automatic loss of access while the page stays open, without a reload.
                    expect(other.locator("#chat-heading")).to_have_text(
                        "Household chat", timeout=15000
                    )
                    expect(other.locator("#tasks")).not_to_contain_text("Synthetic private")
                    expect(other.locator("#messages")).not_to_contain_text("Synthetic private")
                    assert not errors, errors
                    # Exercise the actual sign-in and one-time recovery controls.
                    page.locator("#account-panel details summary").click()
                    page.locator("#current-code").fill(owner_code)
                    page.get_by_role("button", name="Replace codes and sign out").click()
                    expect(page.locator("#credential-result")).to_be_visible()
                    result = page.locator("#new-credentials").input_value().splitlines()
                    new_code, recovery = result[1], result[4]
                    page.locator("#access-code").fill(new_code)
                    page.get_by_role("button", name="Open my household").click()
                    expect(page.locator("#workspace")).to_be_visible()
                    expect(page.locator("#connect-calendar")).to_be_disabled()
                    page.get_by_role("button", name="Sign out", exact=True).click()
                    page.locator(".recovery-panel summary").click()
                    page.locator("#recovery-code").fill(recovery)
                    page.get_by_role("button", name="Recover account", exact=True).click()
                    expect(page.locator("#credential-result")).to_be_visible()
                    recovered = page.locator("#new-credentials").input_value().splitlines()[1]
                    assert recovered != new_code
                    page.locator("#access-code").fill(recovered)
                    page.get_by_role("button", name="Open my household").click()
                    expect(page.locator("#workspace")).to_be_visible()
                    assert not errors, errors
            # No personal subscriptions or credentials were involved.
            assert httpx.get(origin + "/health").status_code == 200
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Temporary backend did not stop")
    print(
        "PASS: private chat creation, deterministic reminder, add/remove participant, "
        "same-household denial and automatic revocation in browser; desktop/mobile layout. "
        "Screenshots contain synthetic data only: .local/screenshots/phase2/"
    )


if __name__ == "__main__":
    main()
