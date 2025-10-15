#!/usr/bin/env python3
import os
import json
import argparse
import sys
import re
from datetime import datetime

TONERS  = ["Cyan", "Magenta", "Yellow", "Black"]
DRAWERS = ["Drawer 1", "Drawer 2", "Drawer 3", "Drawer 4"]

def pct_to_int_safe(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if not s or s.upper() == "N/A":
        return None
    s = s.rstrip("%").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None

def norm_text(x: str) -> str:
    # normalize whitespace and case
    return re.sub(r"\s+", " ", (str(x or "").replace("\xa0", " "))).strip().lower()

def is_empty_text(txt) -> bool:
    t = norm_text(txt)
    return bool(t) and (t.startswith("0") or "empty" in t or "no paper" in t)

def bars_from_text(txt):
    t = norm_text(txt)
    if not t or t in {"n/a", "na", "-", "—"}:
        return None
    if is_empty_text(t):
        return 0
    m = re.search(r"\b([123])\s*bar\b", t)
    if m:
        return int(m.group(1))
    if re.fullmatch(r"[123]", t):
        return int(t)
    return None

def build_html(rows, banner_src="vcutsbanner.png", title="Printer Status"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    include_address = any((p.get("address") or "").strip() for p in rows)
    attention_rows = []

    html = f"""<html>
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin:0; padding:0; background:#f8f9fa; }}
    .header {{ background:#000; color:#FEC52E; padding:20px; text-align:center; position:relative; }}
    .header img {{ position:absolute; top:10px; left:10px; height:80px; }}
    .header .timestamp {{ position:absolute; top:10px; right:10px; color:#FEC52E; }}
    .container {{ padding:20px; }}
    table {{ width:100%; border-collapse:collapse; margin-bottom:20px; }}
    th, td {{ border:1px solid #ddd; text-align:left; padding:8px; }}
    th {{ background:#FEC52E; color:#000; }}
    .cyan {{ background:cyan; color:#000; }}
    .magenta {{ background:magenta; color:#000; }}
    .yellow {{ background:#FEC52E; color:#000; }}
    .black {{ background:#000; color:#fff; }}
    tr:nth-child(even) {{ background:#f2f2f2; }}

    .empty-paper {{ color:red; font-weight:bold; }}
    .one-bar {{ color:red; }}
    .two-bar {{ color:darkkhaki; }}
    .low-toner {{ color:red; font-weight:bold; }}
    .printer-name a {{ color:inherit; text-decoration:none; }}
  </style>
</head>
<body>
  <div class="header">
    <img src="{banner_src}" alt="VCU Technology Services">
    <h1>VCU Technology Services Printer Status Report</h1>
    <div class="timestamp">{ts}</div>
  </div>
  <div class="container">
    <table class='printer-table'>
      <thead>
        <tr>
          <th>Printer</th>"""
    if include_address:
        html += "<th>Address</th>"
    html += """
          <th class='cyan'>Cyan</th>
          <th class='magenta'>Magenta</th>
          <th class='yellow'>Yellow</th>
          <th class='black'>Black</th>
          <th>Drawer 1</th>
          <th>Drawer 2</th>
          <th>Drawer 3</th>
          <th>Drawer 4</th>
        </tr>
      </thead>
      <tbody>
"""

    # Main table and collect attention-needed in one pass
    for p in rows:
        name  = p.get("name", "Unknown")
        url   = p.get("url") or "#"
        addr  = (p.get("address") or "").strip()
        toner = p.get("toner", {})
        paper = p.get("paper", {})

        html += "<tr>"
        html += f"<td class='printer-name'><a href='{url}'>{name}</a></td>"
        if include_address:
            html += f"<td>{addr or '-'}</td>"

        # Toner (<=10% or 'empty' or '0%') -> red
        for color in TONERS:
            raw = toner.get(color, "N/A")
            n = pct_to_int_safe(raw)
            if n is not None:
                html += f"<td class='low-toner'>{n}%</td>" if n <= 10 else f"<td>{n}%</td>"
            else:
                txt = str(raw)
                html += f"<td class='low-toner'>{txt}</td>" if "empty" in norm_text(txt) or norm_text(txt) == "0%" else f"<td>{txt}</td>"

        # Drawers (ultra-strict empty detection → red + include in attention)
        any_low = False
        for d in DRAWERS:
            raw = paper.get(d, "N/A")
            t = norm_text(raw)
            is_hard_empty = (t == "empty" or t == "no paper" or t.startswith("0") or "empty" in t or "no paper" in t)
            if is_hard_empty:
                any_low = True
                html += f"<td class='empty-paper'>{raw}</td>"
                continue
            b = bars_from_text(raw)
            if b is None:
                html += f"<td>{raw}</td>"
            elif b == 1:
                any_low = True
                html += f"<td class='one-bar'>{raw}</td>"
            elif b == 2:
                any_low = True
                html += f"<td class='two-bar'>{raw}</td>"
            else:
                html += f"<td>{raw}</td>"
        html += "</tr>"

        if any_low:
            attention_rows.append({
                "name": name,
                "addr": addr,
                "vals": [paper.get(d, "N/A") for d in DRAWERS]
            })

    html += "</tbody></table>"

    # Attention Needed table
    if attention_rows:
        html += "<h2>Attention Needed: Low Paper Levels</h2>"
        html += "<table class='printer-table'><thead><tr><th>Printer</th>"
        if include_address:
            html += "<th>Address</th>"
        html += "<th>Drawer 1</th><th>Drawer 2</th><th>Drawer 3</th><th>Drawer 4</th></tr></thead><tbody>"
        for r in attention_rows:
            html += "<tr>"
            html += f"<td class='printer-name'>{r['name']}</td>"
            if include_address:
                html += f"<td>{r['addr'] or '-'}</td>"
            for raw in r["vals"]:
                t = norm_text(raw)
                is_hard_empty = (t == "empty" or t == "no paper" or t.startswith("0") or "empty" in t or "no paper" in t)
                if is_hard_empty:
                    html += f"<td class='empty-paper'>{raw}</td>"
                    continue
                b = bars_from_text(raw)
                if b is None:
                    html += f"<td>{raw}</td>"
                elif b == 1:
                    html += f"<td class='one-bar'>{raw}</td>"
                elif b == 2:
                    html += f"<td class='two-bar'>{raw}</td>"
                else:
                    html += f"<td>{raw}</td>"
            html += "</tr>"
        html += "</tbody></table>"

    # Errors — drop 'No paper' when only MPT is empty
    errors = []
    for p in rows:
        name  = p.get("name", "Unknown")
        paper = p.get("paper", {})
        drawers_empty = any(is_empty_text(paper.get(d)) for d in DRAWERS)
        for e in (p.get("errors") or []):
            if "no paper" in str(e).lower() and not drawers_empty:
                continue  # ignore MPT-only "No paper."
            errors.append(f"{name}: {e}")

    html += "<h2>Errors</h2>"
    if errors:
        html += "<ul class='alert'>" + "".join(f"<li>{x}</li>" for x in errors) + "</ul>"
    else:
        html += "<p>No errors reported.</p>"

    html += "\n  </div>\n</body>\n</html>"
    return html

def main():
    ap = argparse.ArgumentParser(description="Render an HTML page from printers.json (produced by app.py)")
    ap.add_argument("-i", "--input", default="printers.json", help="Path to printers.json")
    default_out = f"app_printer_report_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.html"
    ap.add_argument("-o", "--output", default=default_out, help="Output HTML file")
    ap.add_argument("--banner", default="vcutsbanner.png", help="Banner image path/URL")
    ap.add_argument("--title", default="Printer Status", help="HTML <title>")
    # tolerate extra flags from the runner
    args, unknown = ap.parse_known_args()
    if unknown:
        print(f"[warn] Ignoring unknown args: {' '.join(unknown)}", file=sys.stderr)

    with open(args.input, "r", encoding="utf-8") as f:
        rows = json.load(f)
        if not isinstance(rows, list):
            raise ValueError("Input JSON must be a list of printer objects")

    html = build_html(rows, banner_src=args.banner, title=args.title)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report saved to {os.path.abspath(args.output)}")

if __name__ == "__main__":
    main()