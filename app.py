#!/usr/bin/env python3
# app.py — scrape printer status pages and emit structured JSON

from __future__ import annotations
import argparse, concurrent.futures as futures, json, os, re, sys, time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, unquote

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

DRAWERS = ["Multi-Purpose Tray", "Drawer 1", "Drawer 2", "Drawer 3", "Drawer 4"]
TONERS  = ["Cyan", "Magenta", "Yellow", "Black"]
ICON_MAP = {"00": "Empty", "04": "1 Bar", "07": "2 Bar", "10": "3 Bar"}
BAR_MAP  = {"0": "Empty", "1": "1 Bar", "2": "2 Bar", "3": "3 Bar"}

def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def try_load_json(path: Optional[str]) -> Any:
    if not path: return None
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def save_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def log(msg: str) -> None: print(msg, file=sys.stderr)

def _dedupe_preserve(seq: List[str]) -> List[str]:
    seen = set(); out: List[str] = []
    for x in seq:
        if x and x not in seen: seen.add(x); out.append(x)
    return out

def _norm_tray_name(raw: str) -> str:
    r = (raw or "").lower()
    if "multi" in r: return "Multi-Purpose Tray"
    if any(s in r for s in ["mp tray", "mp-tray", "bypass"]): return "Multi-Purpose Tray"
    for i in range(1, 5):
        if f"drawer {i}" in r or r.strip() == str(i): return f"Drawer {i}"
    return (raw or "").strip()

def make_session(verify_ssl: bool, user_agent: str) -> requests.Session:
    if not verify_ssl: requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
    s = requests.Session()
    s.verify = verify_ssl
    if user_agent: s.headers.update({"User-Agent": user_agent})
    return s

def login_if_needed(s: requests.Session, base: str, timeout: int) -> str:
    r = s.get(urljoin(base, "/"), allow_redirects=True, timeout=timeout)
    r.raise_for_status()
    if "<title>Login</title>" in r.text or 'name="login"' in r.text:
        doc = soup(r.text)
        form = doc.find("form", {"name": "login"})
        action = form.get("action", "/login") if form else "/login"
        uri = doc.find("input", {"name": "uri"}).get("value", "/") if doc.find("input", {"name": "uri"}) else "/"
        s.post(urljoin(base, action), data={"userID": "", "password": "", "uri": uri}, allow_redirects=True, timeout=timeout).raise_for_status()
        target = urljoin(base, unquote(uri)) if unquote(uri).startswith("/") else unquote(uri)
        r = s.get(target, allow_redirects=True, timeout=timeout); r.raise_for_status()
    return r.text

def fetch_best_status_html(s: requests.Session, base: str, timeout: int) -> str:
    ts = str(int(time.time() * 1000))
    candidates = ["/", "/rps/", f"/rps/dstatus.cgi?CorePGTAG=11&PageFlag=d_tops.tpl&Dummy={ts}"]
    html_content = ""
    for path in candidates:
        try:
            r = s.get(urljoin(base, path), allow_redirects=True, timeout=timeout)
            if r.ok:
                html_content = r.text
                if any(k in html_content for k in ("tonerVolInfo", "cstInfo", "Error Information", "Consumables")): return html_content
        except requests.RequestException: continue
    return html_content

def extract_json_var(name: str, html: str):
    m = re.search(rf"var\s+{re.escape(name)}\s*=\s*([\{{\[].*?[\}}\]])\s*;", html, re.S)
    return m.group(1) if m else None

def parse_toner(html: str) -> dict:
    vals = {c: "N/A" for c in TONERS}
    js = extract_json_var("tonerVolInfo", html)
    if js:
        for k, c in [("tonerCVol", "Cyan"), ("tonerMVol", "Magenta"), ("tonerYVol", "Yellow"), ("tonerKVol", "Black")]:
            m = re.search(rf'"{k}"\s*:\s*"(\d+)"', js)
            if m: vals[c] = m.group(1)
        return vals
    doc = soup(html)
    for color in TONERS:
        tag = doc.find(["th", "td"], string=re.compile(r'\b' + re.escape(color) + r'\b', re.I))
        if tag and (parent_row := tag.find_parent("tr")):
            if (img := parent_row.find("img", alt=re.compile(r"\d+%"))) and 'alt' in img.attrs:
                if m := re.search(r"(\d+)", img['alt']): vals[color] = m.group(1)
    return vals

def parse_paper(html: str) -> dict:
    paper = {k: "N/A" for k in DRAWERS}
    doc = soup(html)
    if js := extract_json_var("cstInfo", html):
        try:
            data = json.loads(js)
            items = list(data.values()) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for entry in items:
                if isinstance(entry, dict):
                    name = _norm_tray_name(str(entry.get("cstName", "")))
                    remain, total = int(str(entry.get("remainPapVol", "0")) or 0), int(str(entry.get("totalPapVol", "0")) or 0)
                    bars = max(0, min(3, min(remain, 3 if total >= 3 else total)))
                    if name in paper: paper[name] = BAR_MAP.get(str(bars), "N/A")
            return paper
        except json.JSONDecodeError: pass
    for tr in doc.find_all("tr"):
        if m_icon := re.search(r"pap_m(00|04|07|10)\.gif", str(tr), re.I):
            if label_cell := (tr.find("th") or tr.find("td")):
                if (key := _norm_tray_name(label_cell.get_text(" ", strip=True))) in paper:
                    paper[key] = ICON_MAP.get(m_icon.group(1), "N/A")
    return paper

# **NEW & FINAL** scraping logic
def parse_explicit_errors(doc: BeautifulSoup) -> List[str]:
    body_text = doc.body.get_text(" ", strip=True)
    # Regex to find full sentences containing error-related keywords
    error_keywords = r'\b(toner|paper|jam|error|service|install|replace|no\s\w+|empty)\b'
    sentences = re.findall(rf'[^.!?]*{error_keywords}[^.!?]*[.!?]', body_text, re.I)
    
    # Clean up results
    cleaned_sentences = []
    for s in sentences:
        s_clean = s.strip()
        # Filter out noisy, non-error sentences
        if "no error" in s_clean.lower(): continue
        cleaned_sentences.append(s_clean)
        
    return _dedupe_preserve(cleaned_sentences)

def derive_fallback_errors(toner: dict, paper: dict) -> List[str]:
    errors = []
    for color, val in toner.items():
        if isinstance(val, str) and val.isdigit() and (pct := int(val)) <= 10:
            errors.append(f"The {color.lower()} toner is {'empty' if pct == 0 else 'low'}.")
    for drawer, status in paper.items():
        if drawer.lower().startswith("drawer") and str(status).lower() in ["empty", "0 bar", "no paper"]:
            errors.append(f"{drawer} is empty.")
    return errors

def collect_status_for_printer(s: requests.Session, base: str, timeout: int) -> Dict[str, Any]:
    _ = login_if_needed(s, base, timeout)
    html = fetch_best_status_html(s, base, timeout)
    doc = soup(html)
    
    toner_raw = parse_toner(html)
    paper = parse_paper(html)
    errors = parse_explicit_errors(doc)
    
    has_explicit_toner_error = any("toner" in e.lower() for e in errors)
    has_explicit_paper_error = any("paper" in e.lower() or "drawer" in e.lower() for e in errors)
    
    for e in derive_fallback_errors(toner_raw, paper):
        is_toner = "toner" in e.lower()
        is_paper = "drawer" in e.lower()
        if (is_toner and not has_explicit_toner_error) or \
           (is_paper and not has_explicit_paper_error):
            errors.append(e)

    toner_pct = {k: (v + "%" if isinstance(v, str) and v.isdigit() else v) for k, v in toner_raw.items()}
    return {"toner": toner_pct, "paper": paper, "errors": _dedupe_preserve(errors)}

@dataclass
class Printer: name: str; base_url: str

def load_printers_from_config(cfg: Dict[str, Any]) -> List[Printer]:
    return [Printer(name=p.get("name", p["url"]), base_url=p["url"]) for p in cfg.get("printers", []) if "url" in p]

def filter_up_printers(printers: List[Printer], status_path: Optional[str]) -> List[Printer]:
    status = try_load_json(status_path)
    if not isinstance(status, dict): return printers
    up_urls = set(u for u in status.get("up_urls", []) if isinstance(u, str))
    down_urls = set(u for u in status.get("down_urls", []) if isinstance(u, str))
    if not up_urls and not down_urls: return printers
    if up_urls: return [p for p in printers if p.base_url in up_urls]
    return [p for p in printers if p.base_url not in down_urls]

def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape printer status pages and write JSON.")
    ap.add_argument("-c", "--config", required=True, help="Path to config.json")
    ap.add_argument("-s", "--status", help="Optional path to connectivity.json")
    ap.add_argument("--max-workers", type=int, default=8, help="Concurrency")
    ap.add_argument("-o", "--output", default="app-printers.json", help="Output JSON file")
    args, _ = ap.parse_known_args()
    cfg = load_json(args.config)
    http_cfg = cfg.get("http", {})
    printers = filter_up_printers(load_printers_from_config(cfg), args.status)
    rows: List[Dict[str, Any]] = []
    
    with make_session(bool(http_cfg.get("verify_ssl", False)), str(http_cfg.get("user_agent", "Mozilla/5.0"))) as s:
        with futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futs = {ex.submit(collect_status_for_printer, s, pr.base_url, int(http_cfg.get("timeout_seconds", 15))): pr for pr in printers}
            results_map: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
            for fut in futures.as_completed(futs):
                pr = futs[fut]
                try: results_map[pr.base_url] = (fut.result(), None)
                except Exception as e: results_map[pr.base_url] = (None, f"Failed to fetch: {e}")
            for pr in printers:
                result, err = results_map.get(pr.base_url, (None, "Unknown error"))
                row = {"name": pr.name, "url": pr.base_url, "errors": [], "toner": {}, "paper": {}}
                if err: row["errors"].append(str(err))
                else: row.update(result)
                rows.append(row)
    save_json(args.output, rows)
    log(f"[info] wrote {len(rows)} printers to {args.output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())