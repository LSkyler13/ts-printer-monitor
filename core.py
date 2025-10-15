# core.py
from __future__ import annotations
import concurrent.futures as futures
import json, re, time, threading
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

def _dedupe_preserve(seq: List[str]) -> List[str]:
    seen=set(); out=[]
    for x in seq:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

def _norm_tray_name(raw: str) -> str:
    r = (raw or "").lower()
    if "multi" in r: return "Multi-Purpose Tray"
    if any(s in r for s in ["mp tray", "mp-tray", "bypass"]): return "Multi-Purpose Tray"
    for i in range(1, 5):
        if f"drawer {i}" in r or r.strip() == str(i): return f"Drawer {i}"
    return (raw or "").strip()

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
    if "<title>Login</title>" in r.text or 'name="login"' in r.text:
        doc = soup(r.text)
        form = doc.find("form", {"name": "login"})
        action = form.get("action", "/login") if form else "/login"
        uri_el = doc.find("input", {"name": "uri"})
        uri = uri_el.get("value", "/") if uri_el else "/"
        s.post(urljoin(base, action), data={"userID": "", "password": "", "uri": uri}, allow_redirects=True, timeout=timeout).raise_for_status()
        target = urljoin(base, unquote(uri)) if unquote(uri).startswith("/") else unquote(uri)
        r = s.get(target, allow_redirects=True, timeout=timeout); r.raise_for_status()
    return r.text

def _is_login_page(html: str) -> bool:
    return "<title>Login</title>" in html or 'name="login"' in html

def fetch_best_status_html(s: requests.Session, base: str, timeout: int) -> str:
    ts = str(int(time.time() * 1000))
    candidates = [
        f"/rps/dstatus.cgi?CorePGTAG=11&PageFlag=d_tops.tpl&Dummy={ts}",
        "/rps/",
        "/",
    ]
    # fail-fast timeouts per request (connect, read)
    to = (min(2, timeout), min(3, timeout))
    last_ok = ""
    with futures.ThreadPoolExecutor(max_workers=len(candidates)) as ex:
        future_list = [ex.submit(lambda p: s.get(urljoin(base, p), allow_redirects=True, timeout=to), path) for path in candidates]
        for fut in futures.as_completed(future_list):
            try:
                r = fut.result()
            except requests.RequestException:
                continue
            if r.ok:
                html = r.text
                last_ok = html
                if any(k in html for k in ("tonerVolInfo", "cstInfo", "Error Information", "Consumables")) and not _is_login_page(html):
                    return html
    return last_ok

def extract_json_var(name: str, html: str) -> Optional[str]:
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
            img = parent_row.find("img", alt=re.compile(r"\d+%"))
            if img and 'alt' in img.attrs:
                if m := re.search(r"(\d+)", img['alt']):
                    vals[color] = m.group(1)
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
                    remain, total = int(str(entry.get("remainPapVol", "0") or 0)), int(str(entry.get("totalPapVol", "0") or 0))
                    bars = max(0, min(3, min(remain, 3 if total >= 3 else total)))
                    if name in paper: paper[name] = BAR_MAP.get(str(bars), "N/A")
            return paper
        except json.JSONDecodeError:
            pass
    for tr in doc.find_all("tr"):
        if m_icon := re.search(r"pap_m(00|04|07|10)\.gif", str(tr), re.I):
            if label_cell := (tr.find("th") or tr.find("td")):
                key = _norm_tray_name(label_cell.get_text(" ", strip=True))
                if key in paper:
                    paper[key] = ICON_MAP.get(m_icon.group(1), "N/A")
    return paper

def parse_explicit_errors(doc: BeautifulSoup) -> List[str]:
    body_text = doc.body.get_text(" ", strip=True) if doc.body else ""
    error_keywords = r'\b(toner|paper|jam|error|service|install|replace|no\s\w+|empty)\b'
    sentences = re.findall(rf'[^.!?]*{error_keywords}[^.!?]*[.!?]', body_text, re.I)
    cleaned = []
    for s in sentences:
        s2 = s.strip()
        if "no error" in s2.lower(): continue
        cleaned.append(s2)
    return _dedupe_preserve(cleaned)

def derive_fallback_errors(toner: dict, paper: dict) -> List[str]:
    errors = []
    for color, val in toner.items():
        if isinstance(val, str) and val.isdigit() and (pct := int(val)) <= 10:
            errors.append(f"The {color.lower()} toner is {'empty' if pct == 0 else 'low'}.")
    for drawer, status in paper.items():
        if drawer.lower().startswith("drawer") and str(status).lower() in ["empty", "0 bar", "no paper"]:
            errors.append(f"{drawer} is empty.")
    return errors

def fetch_printer_status(s: requests.Session, base: str, timeout: int) -> Dict[str, Any]:
    html = fetch_best_status_html(s, base, timeout)
    if _is_login_page(html):
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
        if (is_toner and not has_explicit_toner_error) or (is_paper and not has_explicit_paper_error):
            errors.append(e)
    toner_pct = {k: (v + "%" if isinstance(v, str) and v.isdigit() else v) for k, v in toner_raw.items()}
    return {"toner": toner_pct, "paper": paper, "errors": _dedupe_preserve(errors)}

@dataclass
class Printer:
    name: str
    base_url: str
    address: str | None = None

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_printers_from_config(cfg: Dict[str, Any]) -> List[Printer]:
    out=[]
    for p in cfg.get("printers", []):
        if "url" in p:
            out.append(Printer(name=p.get("name", p["url"]), base_url=p["url"], address=p.get("address")))
    return out

_tls = threading.local()

def _session_for_thread(verify_ssl: bool, user_agent: str) -> requests.Session:
    s = getattr(_tls, "session", None)
    if s is None:
        s = make_session(verify_ssl, user_agent)
        _tls.session = s
    return s

def collect_all(printers: List[Printer], http_cfg: dict, max_workers: int | None = None) -> List[Dict[str, Any]]:
    timeout = int(http_cfg.get("timeout_seconds", 15))
    verify_ssl = bool(http_cfg.get("verify_ssl", False))
    user_agent = str(http_cfg.get("user_agent", "Mozilla/5.0"))

    if not max_workers:
        max_workers = max(4, min(20, len(printers)))

    rows: List[Dict[str, Any]] = []

    def task(pr: Printer):
        s = _session_for_thread(verify_ssl, user_agent)
        return pr, fetch_printer_status(s, pr.base_url, timeout)

    with futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(task, pr): pr for pr in printers}
        for fut in futures.as_completed(futs):
            pr = futs[fut]
            row = {"name": pr.name, "url": pr.base_url, "address": pr.address or "", "errors": [], "toner": {}, "paper": {}}
            try:
                _pr, data = fut.result()
                row.update(data)
            except Exception as e:
                row["errors"].append(f"Failed to fetch: {e}")
            rows.append(row)
    return rows