import os
import sys

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("QA_UI_BASE_URL", "http://127.0.0.1:5055")
LOGIN_USERNAME = os.environ.get("QA_UI_USERNAME", "shuxing666")
LOGIN_PASSWORD = os.environ.get("QA_UI_PASSWORD", "ui-test-password")
OUTPUT_DIR = os.environ.get("QA_UI_SCREENSHOT_DIR", os.path.join(os.path.dirname(__file__), "..", "ui-screenshots"))
os.makedirs(OUTPUT_DIR, exist_ok=True)


def inspect_page(page, name, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(BASE_URL, wait_until="networkidle")
    if page.locator('input[name="username"]').count():
        page.locator('input[name="username"]').fill(LOGIN_USERNAME)
        page.locator('input[name="password"]').fill(LOGIN_PASSWORD)
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("networkidle")
    page.screenshot(path=os.path.join(OUTPUT_DIR, f"{name}.png"), full_page=True)
    print(f"{name}: title={page.title()} h1={page.locator('h1').count()} context={page.locator('#workspaceShopName').inner_text()}")
    overflow = page.evaluate("""() => Array.from(document.querySelectorAll('*'))
        .filter(el => el.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 8)
        .map(el => ({tag: el.tagName, id: el.id, cls: el.className, right: Math.round(el.getBoundingClientRect().right)}))""")
    print(f"{name}: horizontal_overflow={page.evaluate('document.documentElement.scrollWidth > window.innerWidth')} nodes={overflow}")
    print(f"{name}: widths={page.evaluate('''() => ["body",".page",".layout",".layout > aside",".workspace-rail"].map(selector => { const el = selector === "body" ? document.body : document.querySelector(selector); const box = el?.getBoundingClientRect(); return {selector, width: box && Math.round(box.width), right: box && Math.round(box.right), grid: el && getComputedStyle(el).gridTemplateColumns}; })''')}")
    print(f"{name}: media={page.evaluate('''() => Array.from(document.styleSheets).flatMap(sheet => { try { return Array.from(sheet.cssRules); } catch (_) { return []; } }).filter(rule => rule.media && rule.media.matches).map(rule => rule.cssText.slice(0, 180))''')}")


def main():
    console_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        inspect_page(page, "desktop", 1440, 900)
        inspect_page(page, "mobile", 390, 844)
        page.locator("button", has_text="抓取数据").first.click()
        print(f"mobile_fetch_target={page.locator('#goBtn').is_visible()}")
        browser.close()
    if console_errors:
        print("console_errors=" + " | ".join(console_errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
