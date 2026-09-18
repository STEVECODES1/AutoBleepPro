from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.firefox.launch_persistent_context(
        user_data_dir="./browser_profile",
        headless=False,
        slow_mo=150,
        firefox_user_prefs={
            "dom.webdriver.enabled": False,
            "useAutomationExtension": False,
        },
        args=[
            "--disable-blink-features=AutomationControlled",
        ]
    )
    
    page = browser.new_page()
    
    # Stronger anti-detection
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.navigator.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    """)
    
    page.goto("https://www.tiktok.com/")
    print("Try clicking the login buttons now.")
    print("If it still doesn't work, close this and use the normal Firefox method.")
    input("Press Enter after you are logged in (or to quit)...")
    browser.close()