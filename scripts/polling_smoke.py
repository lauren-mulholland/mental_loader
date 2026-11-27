"""Check real frontend polling with a browser clock and fake API; no services/model calls."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

STATIC = Path(__file__).resolve().parents[1] / "src/mental_loader/static"
MOCK_API = """
window.fixture = {
  reads: [], posts: 0, inFlight: 0, maxInFlight: 0, hidden: false,
  hold: false, release: null, fail: false, active: false, tasks: [],
  messages: {a: [], b: [{role: 'assistant', content: 'Other conversation'}]},
};
Object.defineProperty(document, 'hidden', {get: () => fixture.hidden});
window.fetch = async (path, options = {}) => {
  const f = fixture;
  const method = options.method || 'GET';
  let data;
  if (path === '/api/me') data = {household: 'Fixture', timezone: 'Australia/Sydney'};
  else if (path === '/api/conversations') {
    data = ['a', 'b'].map(id => ({id, created_at: '2026-10-27T00:00:00Z'}));
  } else if (path.endsWith('/complete')) { f.tasks = []; data = {}; }
  else if (path === '/api/logout') data = {};
  else if (method === 'POST' && path.endsWith('/messages')) {
    f.posts++;
    f.active = true;
    data = {};
  } else if (path === '/api/tasks' || path.endsWith('/messages')) {
    f.reads.push(path);
    f.inFlight++;
    f.maxInFlight = Math.max(f.maxInFlight, f.inFlight);
    const id = path.split('/')[3];
    data = structuredClone(path === '/api/tasks' ? f.tasks : {
      messages: f.messages[id], active_runs: f.active ? [{id: 'run'}] : [],
    });
    if (path.endsWith('/messages') && f.hold) {
      f.hold = false;
      await new Promise(resolve => { f.release = resolve; });
    }
    f.inFlight--;
    if (f.fail && path.endsWith('/messages')) {
      f.fail = false;
      return new Response('{}', {status: 503});
    }
  } else throw new Error(`Unexpected fixture request: ${method} ${path}`);
  return new Response(JSON.stringify(data), {status: 200});
};
"""


def main():
    with sync_playwright() as playwright:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        options = {"executable_path": str(chrome)} if chrome.exists() else {}
        browser = playwright.chromium.launch(headless=True, **options)
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            path = urlparse(route.request.url).path
            filename = "index.html" if path == "/" else Path(path).name
            file = STATIC / filename
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404, body="")

        context.route("http://localhost:8137/**", serve)
        page.add_init_script(MOCK_API)
        page.clock.install(time=datetime(2026, 10, 27, tzinfo=UTC))
        page.clock.pause_at(datetime(2026, 10, 27, 0, 0, 1, tzinfo=UTC))
        page.goto("http://localhost:8137/")
        expect(page.locator("#send")).to_be_enabled()
        assert page.evaluate("fixture.reads.length") == 2, page.evaluate("fixture.reads")

        # Idle tabs should not keep the old fast cadence. New reminders appear on idle refresh.
        page.clock.run_for(29999)
        assert page.evaluate("fixture.reads.length") == 2
        page.evaluate("fixture.messages.a.push({role: 'assistant', content: 'Reminder: card'})")
        page.clock.run_for(1)
        expect(page.locator("#messages")).to_contain_text("Reminder: card")
        assert page.evaluate("fixture.reads.length") == 4

        # Submission refreshes immediately, then polls quickly until the delayed reply arrives.
        page.locator("#message-input").fill("Fixture question")
        page.locator("#send").click()
        expect(page.locator("#run-status")).to_contain_text("Working")
        reads = page.evaluate("fixture.reads.length")
        page.clock.run_for(2500)
        assert page.evaluate("fixture.reads.length") == reads + 2
        page.evaluate("""() => {
          fixture.active = false;
          fixture.messages.a.push({role: 'assistant', content: 'Delayed answer'});
        }""")
        page.clock.run_for(2500)
        expect(page.locator("#messages")).to_contain_text("Delayed answer")
        reads = page.evaluate("fixture.reads.length")
        page.clock.run_for(29999)
        assert page.evaluate("fixture.reads.length") == reads

        # Hidden tabs stop polling; returning refreshes immediately.
        page.evaluate("fixture.hidden = true; "
                      "document.dispatchEvent(new Event('visibilitychange'))")
        page.clock.run_for(90000)
        assert page.evaluate("fixture.reads.length") == reads
        page.evaluate("fixture.hidden = false; "
                      "document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("reads => fixture.reads.length === reads + 2", arg=reads)

        # Switch during a slow refresh: ignore the old response, queue the new chat, no overlap.
        page.evaluate("fixture.hold = true; void refresh()")
        page.wait_for_function("fixture.release !== null")
        page.locator("#conversations button").nth(1).click()
        page.wait_for_function("location.search.includes('conversation=b')")
        reads = page.evaluate("fixture.reads.length")
        page.clock.run_for(60000)
        assert page.evaluate("fixture.reads.length") == reads
        page.evaluate("fixture.release(); fixture.release = null")
        expect(page.locator("#messages")).to_have_text("Mental LoaderOther conversation")
        assert page.evaluate("fixture.maxInFlight") <= 2  # One messages/tasks pair.

        # An action during a refresh queues another read instead of losing its update.
        page.evaluate("""() => {
          fixture.tasks = [{id: 'task', title: 'Card', status: 'open',
            due_at: '2026-10-28T00:00:00Z', push_status: []}];
          void refresh();
        }""")
        expect(page.locator("#task-count")).to_have_text("1")
        page.evaluate("fixture.hold = true; void refresh()")
        page.wait_for_function("fixture.release !== null")
        page.get_by_role("button", name="Complete Card").click()
        page.wait_for_function("fixture.tasks.length === 0")
        page.evaluate("fixture.release(); fixture.release = null")
        expect(page.locator("#task-count")).to_have_text("0")

        # A failed refresh recovers on the next idle tick without accumulating timers.
        page.evaluate("fixture.fail = true; void refresh().catch(() => {})")
        page.wait_for_function("!refreshing")
        reads = page.evaluate("fixture.reads.length")
        page.clock.run_for(30000)
        assert page.evaluate("fixture.reads.length") == reads + 2
        assert page.evaluate("fixture.posts") == 1
        page.locator("#logout").click()
        expect(page.locator("#workspace")).to_be_hidden()
        reads = page.evaluate("fixture.reads.length")
        page.clock.run_for(60000)
        assert page.evaluate("fixture.reads.length") == reads
        assert not errors, errors
        browser.close()
    print("PASS: idle/active cadence, reminder/reply updates, hidden/return, slow conversation "
          "switch, queued action refresh, error recovery, logout, single submission; "
          "fake API only.")


if __name__ == "__main__":
    main()
