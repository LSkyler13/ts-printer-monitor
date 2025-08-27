#!/usr/bin/env python3
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

# ---------- Settings ----------
TIMEOUT_CONNECT = 2.0   # seconds for TCP connect
TIMEOUT_READ    = 2.5   # seconds for first byte (read)
TOTAL_TIMEOUT   = (TIMEOUT_CONNECT, TIMEOUT_READ)
MAX_WORKERS     = 16

CURRENT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(CURRENT_DIR, "config.json")
STATUS_PATH  = os.path.join(CURRENT_DIR, "connectivity.json")  # same name as before

# Skip SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Helpers ----------
def load_printers(config_path: str):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("printers", [])

def _probe_once(url: str) -> tuple[bool, int | None, str | None]:
    """
    Returns (is_up, status_code, reason)
    - Tries both "/" (as given) and "/rps/"
    - Any 200..399 => up
    - reason is a short string describing failures (timeout, ssl, etc.)
    """
    session = requests.Session()
    session.verify = False  # self-signed certs on these devices
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    paths = ["", "rps/"]  # try base and rps/
    for p in paths:
        try:
            # ensure single slash
            if not url.endswith("/"):
                base = url + "/"
            else:
                base = url
            target = base + p
            r = session.get(target, timeout=TOTAL_TIMEOUT, allow_redirects=True)
            code = r.status_code
            if 200 <= code < 400:
                return True, code, None
            # non-success; continue to next path
        except requests.exceptions.ConnectTimeout:
            return False, None, "connect_timeout"
        except requests.exceptions.ReadTimeout:
            return False, None, "read_timeout"
        except requests.exceptions.SSLError:
            # Treat SSL issues as down for now (could relax if needed)
            return False, None, "ssl_error"
        except requests.exceptions.ConnectionError as e:
            # DNS or refused, etc.
            return False, None, "connection_error"
        except requests.RequestException as e:
            return False, None, "request_exception"

    # Both paths failed or returned non-2xx/3xx
    return False, None, "bad_status"

def main():
    printers = load_printers(CONFIG_PATH)
    if not printers:
        print("No printers found in config.json")
        return

    up, down = [], []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_probe_once, p["url"]): p for p in printers}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                ok, code, reason = fut.result()
            except Exception:
                ok, code, reason = False, None, "unknown_error"

            if ok:
                up.append(p)
            else:
                # keep reason alongside the object for later inspection
                down.append({**p, "reason": reason})

    payload = {
        "checked_count": len(printers),
        "up": up,
        "down": down,
        "up_urls":   [p["url"] for p in up],
        "down_urls": [p["url"] for p in down],
    }

    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Checked: {len(printers)} | UP: {len(up)} | DOWN: {len(down)}")
    if down:
        print("Down URLs:")
        for p in down:
            tag = f" ({p.get('reason')})" if p.get("reason") else ""
            print(f" - {p['name']}: {p['url']}{tag}")

if __name__ == "__main__":
    main()
