#!/usr/bin/env python3
import os
import json
import argparse
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
    return int(s) if s.isdigit() else None

def build_html(rows, banner_src="vcutsbanner.png", title="Printer Status"):
    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # NEW: decide whether to show the Address column (only if any address is non-empty)
    include_address = any((p.get("address") or "").strip() for p in rows)

    html = f"""<html>
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa; }}
    .header {{ background-color: #000000; color: #FEC52E; padding: 20px; text-align: center; position: relative; }}
    .header img {{ position:absolute; top: 10px; left: 10px; height: 80px; }}
    .header .timestamp {{ position:absolute; top: 10px; right: 10px; color: #FEC52E; }}
    .container {{ padding: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
    th, td {{ border: 1px solid #dddddd; text-align: left; padding: 8px; }}
    th {{ background-color: #FEC52E; color: #000000; }}
    .cyan {{ background-color: cyan; color: black; }}
    .magenta {{ background-color: magenta; color: black; }}
    .yellow {{ background-color: #FEC52E; color: #000000; }}
    .black {{ background-color: black; color: white; }}
    tr:nth-child(even) {{ background-color: #f2f2f2; }}
    .empty-paper {{ color: red; }}
    .one-bar {{ color: red; }}
    .two-bar {{ color: darkkhaki; }}
    .printer-name a {{ color: inherit; text-decoration: none; }}
    .low-toner {{ color: red; font-weight: bold; }}
  </style>
</head>
<body>
  <div class="header">
    <img src="{banner_src}" alt="VCU Technology Services">
    <h1>VCU Technology Services Printer Status Report</h1>
    <div class="timestamp">{current_timestamp}</div>
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

    # Main table
    for p in rows:
        name = p.get("name", "Unknown")
        url  = p.get("url") or "#"
        addr = (p.get("address") or "").strip()
        toner = p.get("toner", {})
        paper = p.get("paper", {})

        html += f"<tr>"
        html += f"<td class='printer-name'><a href='{url}'>{name}</a></td>"
        if include_address:
            html += f"<td>{addr or '-'}</td>"

        for color in TONERS:
            val = toner.get(color, "N/A")
            n = pct_to_int_safe(val)
            if n is not None:
                cell = f"<td class='low-toner'>{n}%</td>" if n <= 10 else f"<td>{n}%</td>"
            else:
                cell = f"<td>{val}</td>"
            html += cell

        for drawer in DRAWERS:
            status = paper.get(drawer, "N/A")
            if status == "Empty":
                klass = "empty-paper"
            elif status == "1 Bar":
                klass = "one-bar"
            elif status == "2 Bar":
                klass = "two-bar"
            else:
                klass = ""
            html += f"<td class='{klass}'>{status}</td>" if klass else f"<td>{status}</td>"

        html += "</tr>"

    html += "</tbody></table>"

    # Low Paper section
    low_paper = []
    for p in rows:
        paper = p.get("paper", {})
        if any(paper.get(d) in ("Empty", "1 Bar", "2 Bar") for d in DRAWERS):
            low_paper.append(p)

    if low_paper:
        html += "<h2>Attention Needed: Low Paper Levels</h2>"
        html += "<table class='printer-table'><thead><tr><th>Printer</th>"
        if include_address:
            html += "<th>Address</th>"
        html += "<th>Drawer 1</th><th>Drawer 2</th><th>Drawer 3</th><th>Drawer 4</th></tr></thead><tbody>"
        for p in low_paper:
            name = p.get("name", "Unknown")
            addr = (p.get("address") or "").strip()
            paper = p.get("paper", {})
            html += "<tr>"
            html += f"<td class='printer-name'>{name}</td>"
            if include_address:
                html += f"<td>{addr or '-'}</td>"
            for d in DRAWERS:
                status = paper.get(d, "N/A")
                if status == "Empty":
                    klass = "empty-paper"
                elif status == "1 Bar":
                    klass = "one-bar"
                elif status == "2 Bar":
                    klass = "two-bar"
                else:
                    klass = ""
                html += f"<td class='{klass}'>{status}</td>" if klass else f"<td>{status}</td>"
            html += "</tr>"
        html += "</tbody></table>"

    # Errors section (flatten)
    errors = []
    for p in rows:
        name = p.get("name", "Unknown")
        for e in p.get("errors", []) or []:
            errors.append(f"{name}: {e}")

    if errors:
        html += "<h2>Errors</h2><ul class='alert'>"
        for line in errors:
            html += f"<li>{line}</li>"
        html += "</ul>"
    else:
        html += "<p>No errors reported.</p>"

    html += "\n  </div>\n</body>\n</html>"
    return html

def main():
    ap = argparse.ArgumentParser(description="Render an HTML page from printers.json (produced by app.py)")
    ap.add_argument("-i", "--input", default="printers.json", help="Path to printers.json")
    # default output is timestamped so we never overwrite another report accidentally
    default_out = f"app_printer_report_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.html"
    ap.add_argument("-o", "--output", default=default_out, help="Output HTML file (defaults to a timestamped filename)")
    ap.add_argument("--banner", default="vcutsbanner.png", help="Banner image path/URL")
    ap.add_argument("--title", default="Printer Status", help="HTML <title>")
    args = ap.parse_args()

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
# python3 app-html.py -i app-printers.json -o app-report.html