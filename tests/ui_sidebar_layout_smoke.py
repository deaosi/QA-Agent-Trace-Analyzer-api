import os
import sys

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("QA_UI_BASE_URL", "http://127.0.0.1:5000")
USERNAME = os.environ.get("QA_UI_USERNAME", "shuxing666")
PASSWORD = os.environ.get("QA_UI_PASSWORD", "ui-test-password")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "ui-screenshots")


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1254, "height": 740})
        page.goto(BASE_URL, wait_until="networkidle")
        if page.locator('input[name="username"]').count():
            page.locator('input[name="username"]').fill(USERNAME)
            page.locator('input[name="password"]').fill(PASSWORD)
            page.locator('button[type="submit"]').click()
            page.wait_for_load_state("networkidle")

        metrics = page.evaluate(
            """() => {
              const aside = document.querySelector('#panel-local .layout > aside');
              const cards = Array.from(aside.querySelectorAll(':scope > .card'));
              const cookie = document.querySelector('#cookie');
              const batch = document.querySelector('#batchIds');
              return {
                asideScrollable: aside.scrollHeight > aside.clientHeight,
                cardClipping: cards.map(card => card.scrollHeight > card.clientHeight + 1),
                cookieHeight: Math.round(cookie.getBoundingClientRect().height),
                batchHeight: Math.round(batch.getBoundingClientRect().height),
                horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth
              };
            }"""
        )
        page.evaluate(
            """() => {
              const layout = document.querySelector('#panel-local .layout');
              window.scrollTo(0, layout.getBoundingClientRect().top + window.scrollY);
            }"""
        )
        page.wait_for_timeout(100)
        aside = page.locator("#panel-local .layout > aside")
        cookie_visible = page.evaluate(
            """() => {
              const aside = document.querySelector('#panel-local .layout > aside');
              const toolbar = document.querySelector('#cookie').closest('.field').nextElementSibling;
              const a = aside.getBoundingClientRect();
              const t = toolbar.getBoundingClientRect();
              return t.top >= a.top && t.bottom <= Math.min(a.bottom, window.innerHeight);
            }"""
        )
        page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, "sidebar-cookie-desktop.png"), full_page=False
        )
        aside.evaluate("element => { element.scrollTop = element.scrollHeight; }")
        page.wait_for_timeout(100)
        batch_visible = page.evaluate(
            """() => {
              const aside = document.querySelector('#panel-local .layout > aside');
              const toolbar = document.querySelector('#batchIds').closest('.field').nextElementSibling;
              const a = aside.getBoundingClientRect();
              const t = toolbar.getBoundingClientRect();
              return t.top >= a.top && t.bottom <= Math.min(a.bottom, window.innerHeight);
            }"""
        )
        page.screenshot(
            path=os.path.join(SCREENSHOT_DIR, "sidebar-batch-desktop.png"), full_page=False
        )
        browser.close()

    print("metrics=", metrics)
    print("cookie_actions_visible=", cookie_visible)
    print("batch_visible_after_scroll=", batch_visible)
    valid = (
        metrics["asideScrollable"]
        and not any(metrics["cardClipping"])
        and metrics["cookieHeight"] >= 90
        and metrics["batchHeight"] >= 74
        and not metrics["horizontalOverflow"]
        and cookie_visible
        and batch_visible
    )
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
