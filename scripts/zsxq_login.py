"""
知识星球扫码登录脚本

用 Playwright 打开知识星球网页版，扫码登录后保存认证信息
（cookies + 请求头 + API 版本号），供导出脚本使用。

用法:
    python zsxq_login.py
    python zsxq_login.py --state-file D:\zsxq_auth.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://wx.zsxq.com"
DEFAULT_STATE_FILE = "zsxq_auth.json"
LOGIN_TIMEOUT_MS = 180_000  # 3 minutes
BEIJING_TZ_OFFSET = 8


def login(state_file: str = DEFAULT_STATE_FILE) -> bool:
    state_path = Path(state_file).resolve()
    captured_headers: dict = {}
    captured_api_urls: list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # Intercept API requests to capture headers and API version
        def on_request(request):
            if "api.zsxq.com" in request.url:
                nonlocal captured_headers
                captured_headers = dict(request.headers)
                captured_api_urls.append(request.url)

        page.on("request", on_request)

        print("[1/4] Opening zsxq login page...")
        page.goto(LOGIN_URL)

        print("[2/4] Please scan the QR code with your zsxq mobile app to login.")
        print(f"      Timeout: {LOGIN_TIMEOUT_MS // 1000} seconds. Waiting for login...")

        # Poll for login success by checking cookies every 2 seconds.
        # zsxq is a SPA — URL may not change, but cookies get set after login.
        login_ok = False
        poll_interval = 2000  # 2 seconds
        elapsed = 0
        while elapsed < LOGIN_TIMEOUT_MS:
            cookies = context.cookies()
            cookie_names = {c["name"] for c in cookies}
            # Check for any zsxq auth cookie
            auth_cookies = cookie_names & {
                "zsxq_access_token", "zsxqsessionid",
                "zsxq_access_token_third", "sid",
            }
            if auth_cookies:
                login_ok = True
                print(f"  Login detected! Found cookies: {auth_cookies}")
                break
            # Also check if the page URL changed away from the login page
            current_url = page.url
            if "wx.zsxq.com" in current_url and "dweb" in current_url:
                login_ok = True
                print(f"  Login detected! URL changed to: {current_url}")
                break
            elapsed += poll_interval
            page.wait_for_timeout(poll_interval)

        if not login_ok:
            # Last resort: check if any cookies exist at all
            cookies = context.cookies()
            if len(cookies) > 5:  # Login page typically has <5, logged in has more
                login_ok = True
                print(f"  Login likely detected ({len(cookies)} cookies found).")
            else:
                print("[ERROR] Login timeout or not detected. Please retry.")
                browser.close()
                return False

        print("[3/4] Login detected. Capturing API info...")
        # Wait for the web app to make API calls
        page.wait_for_timeout(5000)

        # If no API calls captured yet, try navigating to the index page
        if not captured_api_urls:
            try:
                page.goto("https://wx.zsxq.com/dweb2/index")
                page.wait_for_timeout(5000)
            except Exception:
                pass

        # Get cookies
        cookies = context.cookies()

        # Detect API base URL from captured requests
        api_base = "https://api.zsxq.com/v2/"
        if captured_api_urls:
            match = re.search(r"(https://api\.zsxq\.com/v[\d.]+/)", captured_api_urls[0])
            if match:
                api_base = match.group(1)

        # Build auth info
        auth_info = {
            "cookies": {c["name"]: c["value"] for c in cookies},
            "headers": captured_headers,
            "api_base": api_base,
            "api_urls_sample": captured_api_urls[:5],
            "login_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "login_timestamp": int(time.time()),
        }

        # Save
        state_path.write_text(
            json.dumps(auth_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"[4/4] Auth info saved to: {state_path}")
        print(f"      API base: {api_base}")
        print(f"      Cookies: {len(auth_info['cookies'])} entries")
        print()
        print("Next steps:")
        print("  python zsxq_export.py list                          # List your circles")
        print("  python zsxq_export.py export --group-id <ID>        # Export all topics")

        browser.close()
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Login to zsxq via QR scan")
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Path to save auth state (default: zsxq_auth.json)",
    )
    args = parser.parse_args()

    success = login(args.state_file)
    sys.exit(0 if success else 1)
