# server.py — single-process runner and API
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os, datetime

from core import load_config, load_printers_from_config, collect_all
from app_html import build_html

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
CORS(app)

def p(*parts): return os.path.join(BASE_DIR, *parts)

@app.post("/api/run")
def api_run():
    try:
        cfg = load_config(p("config.json"))
        printers = load_printers_from_config(cfg)
        rows = collect_all(printers, cfg.get("http", {}), max_workers=8)

        # persist latest
        json_path = p("app-printers.json")
        html_path = p("app-report.html")
        with open(json_path, "w", encoding="utf-8") as f:
            import json; json.dump(rows, f, indent=2, ensure_ascii=False)
        html = build_html(rows, banner_src="vcutsbanner.png", title="Printer Status")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        return jsonify({
            "ok": True,
            "ran_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "count": len(rows),
            "rows": rows,
            "json_path": json_path,
            "html_path": html_path,
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/api/last")
def api_last():
    import json
    try:
        with open(p("app-printers.json"), "r", encoding="utf-8") as f:
            rows = json.load(f)
        return jsonify({"ok": True, "rows": rows}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404

@app.get("/app-report.html")
def report():
    return send_from_directory(BASE_DIR, "app-report.html", mimetype="text/html", max_age=0)

@app.get("/")
def root():
    for name in ("printer-dashboard-debug.html", "printer-dashboard.html"):
        path = os.path.join(BASE_DIR, name)
        if os.path.exists(path):
            return send_from_directory(BASE_DIR, name, mimetype="text/html", max_age=0)
    return ("<h1>Dashboard not found</h1>"
            "<p>Place <code>printer-dashboard-debug.html</code> or "
            "<code>printer-dashboard.html</code> in this folder.</p>", 404)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)