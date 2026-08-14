import argparse
import urllib.parse

from playwright.sync_api import sync_playwright


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="yoga mat")
    ap.add_argument("--regions", default="TH,ID")
    args = ap.parse_args()
    regions = [r.strip().upper() for r in args.regions.split(",") if r.strip()]
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        for region in regions:
            q = urllib.parse.quote(args.keyword)
            url = f"https://shop.tiktok.com/{region.lower()}/s?q={q}"
            ctx = b.new_context(locale="en-US", timezone_id="America/New_York", user_agent=UA)
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(8000)
                print(f"--- REGION {region} ---")
                print("FINAL:", page.url[:150])
                print("TITLE:", (page.title() or "")[:120])
                pdp = page.evaluate("""() => document.querySelectorAll('a[href*="/pdp/"]').length""")
                print("PDP:", pdp)
                imgs = page.evaluate("() => document.querySelectorAll('img').length")
                print("IMGS:", imgs)
                body = page.evaluate("() => document.body ? document.body.innerText.slice(0,400) : ''")
                print("BODY:", body.replace(chr(10), " | ")[:400])
                html_len = page.evaluate("() => document.documentElement.outerHTML.length")
                print("HTML_LEN:", html_len)
                page.screenshot(path=f"diag_{region}.png")
            except Exception as exc:
                print(f"--- REGION {region} ERROR:", str(exc)[:300])
            ctx.close()
        b.close()


if __name__ == "__main__":
    main()
