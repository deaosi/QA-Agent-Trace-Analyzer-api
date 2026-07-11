import os
from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("QA_UI_BASE_URL", "http://127.0.0.1:5056")
PASSWORD = os.environ.get("QA_UI_PASSWORD", "ui-test-password")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(BASE_URL, wait_until="networkidle")
    if page.locator('input[name="username"]').count():
        page.locator('input[name="username"]').fill("shuxing666")
        page.locator('input[name="password"]').fill(PASSWORD)
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("networkidle")
    print("url=", page.url)
    print("tab_count=", page.locator(".tab-btn").count())
    page.locator('.tab-btn[data-tab="cleanup"]').click()
    page.wait_for_timeout(200)
    print("cleanup_visible=", page.locator("#panel-cleanup").is_visible())
    print("search_visible=", page.locator("#cleanupShopSearch").is_visible())
    print("delete_button_visible=", page.locator("#panel-cleanup button", has_text="删除选中店铺").is_visible())
    print("local_delete_buttons=", page.locator('#panel-local button[onclick*="deleteSelected"] , #panel-local button[onclick*="delShop"]').count())
    print("horizontal_overflow=", page.evaluate("document.documentElement.scrollWidth > window.innerWidth"))
    page.screenshot(path="ui-screenshots/shop-cleanup-mobile.png", full_page=True)
    browser.close()
