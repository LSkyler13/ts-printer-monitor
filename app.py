#!/usr/bin/env python3
# app.py — scrape printer status pages and emit structured JSON
# Restores device-specific parsing (tonerVolInfo, cstInfo, icon GIFs)
# Adds -o/--output to write data consumed by app-html.py

from __future__ import annotations
import argparse, concurrent.futures as futures, json, os, re, sys, time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin, unquote

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

# -------------------------
# Constants / Maps
# -------------------------
DRAWERS = ["Multi-Purpose Tray", "Drawer 1", "Drawer 2", "Drawer 3", "Drawer 4"]
TONERS  = ["Cyan", "Magenta", "Yellow", "Black"]
ICON_MAP = {"00": "Empty", "04": "1 Bar", "07": "2 Bar", "10": "3 Bar"}  # pap_mXX.gif
BAR_MAP  = {"0": "Empty", "1": "1 Bar", "2": "2 Bar", "3": "3 Bar"}      # 0..3 bars

# -------------------------
# Small utils
# -------------------------
def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def try_load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def log(msg: str) -> None:
    print(msg, file=sys.stderr)

def _dedupe_preserve(seq):
    seen = set(); out = []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def _norm_tray_name(raw: str) -> str:
    r = (raw or "").lower()
    if "multi" in r and "purpose" in r: return "Multi-Purpose Tray"
    if "mp tray" in r or "mp-tray" in r or "bypass" in r: return "Multi-Purpose Tray"
    if "drawer 1" in r or r.strip() == "1": return "Drawer 1"
    if "drawer 2" in r or r.strip() == "2": return "Drawer 2"
    if "drawer 3" in r or r.strip() == "3": return "Drawer 3"
    if "drawer 4" in r or r.strip() == "4": return "Drawer 4"
    return (raw or "").strip()

# -------------------------
# HTTP session / login
# -------------------------
def make_session(verify_ssl: bool, user_agent: str) -> requests.Session:
    if not verify_ssl:
        requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
    s = requests.Session()
    s.verify = verify_ssl
    if user_agent:
        s.headers.update({"User-Agent": user_agent})
    return s

def login_if_needed(s: requests.Session, base: str, timeout: int) -> str:
    r = s.get(urljoin(base, "/"), allow_redirects=True, timeout=timeout)
    r.raise_for_status()

    # Some devices present a blank-login form—post empty creds to be let through
    if "<title>Login</title>" in r.text or 'name="login"' in r.text:
        doc = soup(r.text)
        form = doc.find("form", {"name": "login"})
        action = form.get("action", "/login") if form else "/login"
        uri_inp = doc.find("input", {"name": "uri"})
        next_uri = uri_inp.get("value", "/") if uri_inp else "/"

        s.post(
            urljoin(base, action),
            data={"userID": "", "password": "", "uri": next_uri},
            allow_redirects=True,
            timeout=timeout,
        ).raise_for_status()

        target = unquote(next_uri)
        if target.startswith("/"):
            target = urljoin(base, target)
        r = s.get(target, allow_redirects=True, timeout=timeout)
        r.raise_for_status()

    return r.text

def fetch_best_status_html(s: requests.Session, base: str, timeout: int) -> str:
    # Try several endpoints; different firmware serves data in different places
    ts = str(int(time.time() * 1000))
    candidates = [
        "/",
        "/rps/",
        f"/rps/dstatus.cgi?CorePGTAG=11&PageFlag=d_tops.tpl&Dummy={ts}",
        f"/rps/jstatpri.cgi?Flag=Init_Data&CorePGTAG=1&FromTopPage=1&Dummy={ts}",
        f"/rps/jsvl.cgi?Flag=Init_Data&CorePGTAG=7&Dummy={ts}",
        f"/rps/dinfo.cgi?CorePGTAG=13&Dummy={ts}",
    ]
    found_html = None
    for path in candidates:
        url = urljoin(base, path)
        try:
            r = s.get(url, allow_redirects=True, timeout=timeout)
            if not r.ok:
                continue
            html = r.text
            # Heuristic: accept pages that contain the data sources we parse
            if any(k in html for k in ("tonerVolInfo", "cstInfo", "pap_m00.gif", "Error Details")):
                found_html = html
                break
            # Fall back to the last OK page
            found_html = html
        except Exception:
            continue
    return found_html or ""

# -------------------------
# Parsers (device-specific)
# -------------------------
def extract_json_var(name: str, html: str):
    m = re.search(rf"var\s+{re.escape(name)}\s*=\s*([\{{\[].*?[\}}\]])\s*;", html, flags=re.S)
    return m.group(1) if m else None

def parse_toner(html: str) -> dict:
    # Parse JS object like: var tonerVolInfo = {"tonerCVol":"100",...}
    vals = {"Cyan":"N/A","Magenta":"N/A","Yellow":"N/A","Black":"N/A"}
    js = extract_json_var("tonerVolInfo", html)
    if not js:
        return vals
    for k, color in [("tonerCVol","Cyan"),("tonerMVol","Magenta"),("tonerYVol","Yellow"),("tonerKVol","Black")]:
        m = re.search(rf'"{k}"\s*:\s*"(\d+)"', js)
        if m:
            vals[color] = m.group(1)
    return vals

def parse_paper_from_icons(doc: BeautifulSoup) -> dict:
    # Fallback when cstInfo is missing — uses pap_mXX.gif icons in a table
    out = {k: "N/A" for k in DRAWERS}
    for tr in doc.find_all("tr"):
        row_html = str(tr)
        m_icon = re.search(r"pap_m(00|04|07|10)\.gif", row_html, flags=re.I)
        if not m_icon:
            continue
        th = tr.find("th")
        label_cell = th if th else tr.find("td")
        if not label_cell:
            continue
        label = label_cell.get_text(" ", strip=True)
        key   = _norm_tray_name(label)
        if key in out:
            out[key] = ICON_MAP.get(m_icon.group(1), "N/A")
    return out

def parse_paper_from_cstinfo(html: str) -> dict:
    # Primary source — JS var "cstInfo" contains per-drawer remaining/total
    out = {k: "N/A" for k in DRAWERS}
    js = extract_json_var("cstInfo", html)
    if not js:
        return out
    try:
        data = json.loads(js)
    except Exception:
        return out
    items = list(data.values()) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for entry in items:
        if not isinstance(entry, dict): 
            continue
        name   = _norm_tray_name(str(entry.get("cstName", "")))
        remain = int(str(entry.get("remainPapVol", "0")) or 0)
        total  = int(str(entry.get("totalPapVol", "0")) or 0)
        cap = 3 if total >= 3 else total  # normalize to 0..3 bars
        bars = max(0, min(3, min(remain, cap)))
        if name in out:
            out[name] = BAR_MAP.get(str(bars), "N/A")
    return out

def parse_errors_dom(doc: BeautifulSoup) -> List[str]:
    # Conservative: look for obvious error strings on the page
    msgs = []
    for t in doc.stripped_strings:
        if not isinstance(t, str):
            continue
        # Typical phrasing we saw in your earlier logs
        for m in re.finditer(r"The\s+(cyan|magenta|yellow|black)\s+toner\s+is\s+(low|empty)\.", t, re.I):
            color, kind = m.groups()
            msgs.append(f"The {color.lower()} toner is {kind}.")
    return _dedupe_preserve(msgs)

def find_inline_toner_errors(html: str) -> List[str]:
    msgs = []
    for m in re.finditer(r"The\s+(cyan|magenta|yellow|black)\s+toner\s+is\s+(low|empty)\.", html, re.I):
        color, kind = m.groups()
        msgs.append(f"The {color.lower()} toner is {kind}.")
    return _dedupe_preserve(msgs)

def discover_error_details_href(html: str) -> Optional[str]:
    d = soup(html)
    a = d.find("a", string=lambda s: isinstance(s, str) and "Error Details" in s)
    if a and a.get("href"):
        return a["href"]
    for tag in d.find_all("a", href=True):
        href = tag["href"]
        if "error" in href.lower():
            return href
    return None

def derive_errors_from_toner(toner_numeric: dict) -> List[str]:
    out = []
    for color in TONERS:
        v = toner_numeric.get(color, "N/A")
        if isinstance(v, str) and v.isdigit():
            pct = int(v)
            if pct == 0:
                out.append(f"The {color.lower()} toner is empty.")
            else:
                # Slightly stricter on black
                if color == "Black":
                    if pct <= 30:
                        out.append("The black toner is low.")
                else:
                    if pct < 30:
                        out.append(f"The {color.lower()} toner is low.")
    return out

def collect_status_for_printer(s: requests.Session, base: str, timeout: int) -> Dict[str, Any]:
    _ = login_if_needed(s, base, timeout)
    html = fetch_best_status_html(s, base, timeout)
    doc  = soup(html)

    toner_raw = parse_toner(html)  # numeric strings (e.g., "80")
    toner_pct = {k: (v + "%" if isinstance(v, str) and v.isdigit() else v) for k, v in toner_raw.items()}

    paper_cst   = parse_paper_from_cstinfo(html)
    paper_icons = parse_paper_from_icons(doc)
    paper = {k: (paper_cst.get(k) if paper_cst.get(k) != "N/A" else paper_icons.get(k, "N/A")) for k in DRAWERS}

    errors = parse_errors_dom(doc) or find_inline_toner_errors(html)
    if not errors:
        href = discover_error_details_href(html)
        if href:
            try:
                r = s.get(urljoin(base, href), allow_redirects=True, timeout=timeout)
                if r.ok:
                    details_doc = soup(r.text)
                    errors = parse_errors_dom(details_doc) or find_inline_toner_errors(r.text)
            except Exception:
                pass
    if not errors:
        errors = derive_errors_from_toner(toner_raw)

    return {"paper": paper, "toner": toner_pct, "errors": errors}

# -------------------------
# Data model
# -------------------------
@dataclass
class Printer:
    name: str
    base_url: str

# -------------------------
# Config + connectivity
# -------------------------
def load_printers_from_config(cfg: Dict[str, Any]) -> List[Printer]:
    items = []
    for p in cfg.get("printers", []):
        if "url" in p:
            items.append(Printer(name=p.get("name", p["url"]), base_url=p["url"]))
        else:
            # Back-compat if someone supplies host/scheme/port/status_path
            host = p["host"]
            scheme = p.get("scheme", "https")
            port = int(p.get("port", 8443))
            path = p.get("status_path", "/")
            items.append(Printer(name=p.get("name", host), base_url=f"{scheme}://{host}:{port}{path}"))
    return items

def filter_up_printers(printers: List[Printer], status_path: str) -> List[Printer]:
    """
    Use connectivity.json to select only UP printers when possible.
    Supports:
      - {"up":[{name,url},...]} or {"up_urls":[...]}
      - {"down":[...]} or {"down_urls":[...]}
      - or a simple map {url: true/false}
    If no status file or unrecognized content, returns original list.
    """
    status = try_load_json(status_path)
    if not isinstance(status, dict):
        return printers

    # Normalize into sets of URLs
    up_urls = set()
    down_urls = set()

    # Prefer explicit up entries
    if isinstance(status.get("up"), list):
        for e in status["up"]:
            u = e.get("url") if isinstance(e, dict) else e
            if isinstance(u, str): up_urls.add(u)
    if isinstance(status.get("up_urls"), list):
        up_urls.update([u for u in status["up_urls"] if isinstance(u, str)])

    # Consider down entries
    if isinstance(status.get("down"), list):
        for e in status["down"]:
            u = e.get("url") if isinstance(e, dict) else e
            if isinstance(u, str): down_urls.add(u)
    if isinstance(status.get("down_urls"), list):
        down_urls.update([u for u in status["down_urls"] if isinstance(u, str)])

    # Simple map fallback
    for k, v in status.items():
        if isinstance(k, str) and isinstance(v, bool):
            (up_urls if v else down_urls).add(k)

    if not up_urls and not down_urls:
        return printers

    out = []
    for pr in printers:
        u = pr.base_url
        if up_urls and u in up_urls:
            out.append(pr)
        elif not up_urls and u not in down_urls:
            out.append(pr)
    return out

# -------------------------
# CLI
# -------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape printer status pages and write JSON.")
    ap.add_argument("-c","--config",required=True, help="Path to config.json")
    ap.add_argument("-s","--status",required=True, help="Path to connectivity.json")
    ap.add_argument("--max-workers",type=int,default=4, help="Concurrency")
    ap.add_argument("-o","--output",default="app-printers.json", help="Output JSON for app-html.py")
    args = ap.parse_args()

    cfg = load_json(args.config)
    http_cfg = cfg.get("http", {})
    verify_ssl = bool(http_cfg.get("verify_ssl", False))
    user_agent = str(http_cfg.get("user_agent", "Mozilla/5.0"))

    printers = load_printers_from_config(cfg)
    printers = filter_up_printers(printers, args.status)

    rows: List[Dict[str,Any]] = []
    scrape_failures: List[Dict[str,str]] = []

    start = time.time()
    with make_session(verify_ssl=verify_ssl, user_agent=user_agent) as s:
        with futures.ThreadPoolExecutor(max_workers=max(1,args.max_workers)) as ex:
            futs = {ex.submit(collect_status_for_printer, s, pr.base_url, int(http_cfg.get("timeout_seconds", 15))): pr for pr in printers}
            # maintain input order
            ordered = list(printers)
            results_map: Dict[str, Tuple[Optional[Dict[str,Any]], Optional[str]]] = {}

            for fut in futures.as_completed(futs):
                pr = futs[fut]
                try:
                    res = fut.result()
                    results_map[pr.base_url] = (res, None)
                except Exception as e:
                    results_map[pr.base_url] = (None, f"Failed to fetch: {e}")

            for pr in ordered:
                name = pr.name
                base = pr.base_url
                log(f"[debug] picked status page: {base}")

                result, err = results_map.get(base, (None, "No result"))
                print(f"\n=== {name} — Device Status ===\n")
                if err:
                    print("Errors:"); print(f" - {err}\n")
                    rows.append({
                        "name": name, "url": base, "address": "",
                        "toner": {c:"N/A" for c in TONERS},
                        "paper": {d:"N/A" for d in DRAWERS},
                        "errors": [err],
                    })
                    scrape_failures.append({"name": name, "url": base})
                    continue

                toner = result["toner"]; paper = result["paper"]; errors = result["errors"]

                print("Errors:")
                for e in errors: print(f" - {e}")
                print("\nPaper Drawers:")
                print(f" - Multi-Purpose Tray: {paper.get('Multi-Purpose Tray','N/A')}")
                for d in ("Drawer 1","Drawer 2","Drawer 3","Drawer 4"):
                    print(f" - {d}: {paper.get(d,'N/A')}")
                print("\nToner Levels:")
                for c in TONERS:
                    print(f" - {c}: {toner.get(c,'N/A')}")

                rows.append({
                    "name": name, "url": base, "address": "",
                    "toner": {c: toner.get(c,"N/A") for c in TONERS},
                    "paper": {
                        "Multi-Purpose Tray": paper.get("Multi-Purpose Tray","N/A"),
                        "Drawer 1": paper.get("Drawer 1","N/A"),
                        "Drawer 2": paper.get("Drawer 2","N/A"),
                        "Drawer 3": paper.get("Drawer 3","N/A"),
                        "Drawer 4": paper.get("Drawer 4","N/A"),
                    },
                    "errors": errors,
                })

    save_json(args.output, rows)
    print(f"\n[info] wrote {len(rows)} printers to {args.output} in {time.time()-start:.2f}s")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
# python3 app.py -c config.json -s connectivity.json --max-workers 7 -o app-printers.json
