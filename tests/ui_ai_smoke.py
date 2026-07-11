import os
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("QA_UI_BASE_URL", "http://127.0.0.1:5056")
PASSWORD = os.environ.get("QA_UI_PASSWORD", "ui-test-password")


def login(page):
    page.goto(BASE_URL, wait_until="networkidle")
    if page.locator('input[name="username"]').count():
        page.locator('input[name="username"]').fill("shuxing666")
        page.locator('input[name="password"]').fill(PASSWORD)
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("networkidle")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    login(page)
    print("tabs=", page.locator(".tab-btn").all_inner_texts())
    page.locator('.tab-btn[data-tab="ai"]').click()
    page.wait_for_timeout(200)
    print("provider_visible=", page.locator("#aiProviderName").is_visible())
    print("switch_visible=", page.locator("#aiFullUrl").is_visible())
    print("footer_visible=", page.locator(".provider-config-footer").is_visible())
    page.locator("#aiFullUrl").check(force=True)
    print("endpoint_label=", page.locator("#aiEndpointLabel").inner_text())
    print("endpoint_helper=", page.locator("#aiEndpointHelper").inner_text())
    overflow = page.evaluate("""() => Array.from(document.querySelectorAll('*'))
        .filter(el => el.getBoundingClientRect().right > window.innerWidth + 1)
        .map(el => ({tag: el.tagName, id: el.id, cls: String(el.className), right: Math.round(el.getBoundingClientRect().right)}))""")
    print("overflow_nodes=", overflow[:5])
    page.screenshot(path="ui-screenshots/ai-provider-mobile.png", full_page=True)
    browser.close()
