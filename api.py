from collections import defaultdict
from datetime import datetime
import os
import sqlite3
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
import uvicorn

app = FastAPI()

# ==========================================
# RATE-LIMIT CONFIGURATION (20 REQ/S ANTI-SPAM)
# ==========================================
request_limits = defaultdict(list)
RATE_LIMIT_WINDOW = 1.0
MAX_REQUESTS = 20


@app.middleware("http")
async def rate_limit_protection(request: Request, call_next):
    if request.url.path.startswith("/dashboard-abyssal-code-2015"):
        return await call_next(request)

    client_ip = request.headers.get("CF-Connecting-IP")
    if not client_ip:
        client_ip = request.client.host if request.client else "127.0.0.1"  # type: ignore

    current_time = time.time()
    timestamps = request_limits[client_ip]
    timestamps[:] = [t for t in timestamps if current_time - t < RATE_LIMIT_WINDOW]

    if len(timestamps) >= MAX_REQUESTS:
        return PlainTextResponse(
            'game.Players.LocalPlayer:Kick("\\n[ABYSSAL HUB]\\nRate limit exceeded! Slow down.")',
            status_code=429,
        )

    timestamps.append(current_time)
    response = await call_next(request)
    return response


# ==========================================
# DIRECTORIES & DATABASE INITIALIZATION
# ==========================================
SCRIPTS_DIR = "scripts"
DEFAULT_PAYLOAD_FILE = "payload.lua"

if not os.path.exists(SCRIPTS_DIR):
    os.makedirs(SCRIPTS_DIR)


def init_db():
    conn = sqlite3.connect("bloxy.db", timeout=15)
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key TEXT PRIMARY KEY,
            status TEXT DEFAULT 'active',
            hwid TEXT DEFAULT NULL,
            expires_at TEXT DEFAULT 'PERMANENT',
            redeemed_by TEXT DEFAULT NULL,
            note TEXT DEFAULT '',
            last_reset TEXT DEFAULT NULL,
            is_locked INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


init_db()


@app.get("/ping", response_class=PlainTextResponse)
def ping():
    return "OK"


# ==========================================
# SCRIPT DELIVERY LOGIC (HỖ TRỢ TÊN GAME & PLACEID)
# ==========================================
def handle_secure_script_delivery(key: str = None, hwid: str = None, placeid: int = 0, game: str = ""):  # type: ignore
    if not key:
        return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nKey not provided!')")

    conn = sqlite3.connect("bloxy.db", timeout=15)
    cursor = conn.cursor()
    cursor.execute("SELECT status, hwid, expires_at, is_locked FROM keys WHERE key = ?", (key,))
    result = cursor.fetchone()

    if not result:
        conn.close()
        return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nKey does not exist!')")

    status, saved_hwid, expires_at, is_locked = result

    if is_locked == 1:
        conn.close()
        return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nKey is currently locked by user!')")

    if status and status.upper() == "BLACKLISTED":
        conn.close()
        return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nYour Key has been Blacklisted!')")

    if status and status.upper() == "EXPIRED":
        conn.close()
        return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nYour Key has expired!')")

    if expires_at and expires_at != "PERMANENT" and str(expires_at).upper() != "LIFETIME":
        try:
            exp_date = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            if datetime.now() > exp_date:
                cursor.execute("UPDATE keys SET status = 'EXPIRED' WHERE key = ?", (key,))
                conn.commit()
                conn.close()
                return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nYour Key has expired!')")
        except Exception:
            pass

    if hwid and hwid != "UNKNOWN_HWID":
        if not saved_hwid or saved_hwid == "" or saved_hwid == "Not Bound":
            cursor.execute("UPDATE keys SET hwid = ? WHERE key = ?", (hwid, key))
            conn.commit()
        elif saved_hwid != hwid:
            conn.close()
            return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nThis Key is bound to another device!')")

    conn.close()

    # Ưu tiên 1: Load theo tên file game cụ thể (nếu có truyền tham số game)
    target_script_path = None
    if game and game.strip() != "":
        custom_path = os.path.join(SCRIPTS_DIR, f"{game.strip()}.lua")
        if os.path.exists(custom_path):
            target_script_path = custom_path

    # Ưu tiên 2: Load theo PlaceID nếu không tìm thấy file theo tên game
    if not target_script_path and placeid and placeid > 0:
        place_path = os.path.join(SCRIPTS_DIR, f"{placeid}.lua")
        if os.path.exists(place_path):
            target_script_path = place_path

    # Ưu tiên 3: Dùng payload mặc định nếu các cách trên không có
    if not target_script_path:
        default_path = os.path.join(SCRIPTS_DIR, DEFAULT_PAYLOAD_FILE)
        if os.path.exists(default_path):
            target_script_path = default_path

    if not target_script_path or not os.path.exists(target_script_path):
        return PlainTextResponse("game.Players.LocalPlayer:Kick('\\n[ABYSSAL HUB]\\nScript file not found on server!')")

    try:
        with open(target_script_path, "r", encoding="utf-8") as f:
            content = f.read()

        session_token = str(uuid.uuid4())
        final_script = content.replace("{{SECURE_TOKEN}}", session_token)
        return PlainTextResponse(final_script)
    except Exception as e:
        return PlainTextResponse(f'game.Players.LocalPlayer:Kick("\\n[ABYSSAL HUB]\\nError reading script: {str(e)}")')


@app.get("/get-script", response_class=PlainTextResponse)
def get_script(key: str = None, hwid: str = None, placeid: int = 0, game: str = ""):  # type: ignore
    return handle_secure_script_delivery(key, hwid, placeid, game)


@app.get("/load", response_class=PlainTextResponse)
def load_route(key: str = None, hwid: str = None, placeid: int = 0, game: str = ""):  # type: ignore
    return handle_secure_script_delivery(key, hwid, placeid, game)


# ==========================================
# ADMIN DASHBOARD ROUTE (RESPONSIVE)
# ==========================================
@app.get("/dashboard-abyssal-code-2015", response_class=HTMLResponse)
def dashboard(q: str = ""):
    search_query = q.strip()
    conn = sqlite3.connect("bloxy.db")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM keys")
    total_keys = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM keys WHERE status = 'active'")
    active_keys = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM keys WHERE hwid IS NOT NULL AND hwid != ''")
    bound_keys = cursor.fetchone()[0]

    if search_query:
        qp = f"%{search_query}%"
        cursor.execute("SELECT key, status, expires_at, hwid, redeemed_by, note, last_reset, is_locked FROM keys WHERE key LIKE ? OR redeemed_by LIKE ? OR note LIKE ?", (qp, qp, qp))
    else:
        cursor.execute("SELECT key, status, expires_at, hwid, redeemed_by, note, last_reset, is_locked FROM keys")

    keys = cursor.fetchall()
    conn.close()

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Abyssal Hub - Admin Dashboard</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{ font-family: system-ui, sans-serif; background-color: #0b0f19; color: #f8fafc; margin: 0; padding: 15px; }}
            .container {{ max-width: 1300px; margin: 0 auto; background: #111827; padding: 20px; border-radius: 16px; border: 1px solid #1f2937; }}
            h2 {{ color: #38bdf8; margin-top: 0; font-size: 20px; display: flex; justify-content: space-between; align-items: center; }}
            .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin: 20px 0; }}
            .card {{ background: #1f2937; padding: 15px; border-radius: 10px; border-left: 4px solid #38bdf8; }}
            .card.green {{ border-left-color: #4ade80; }}
            .card.orange {{ border-left-color: #fbbf24; }}
            .card-title {{ font-size: 12px; color: #9ca3af; text-transform: uppercase; }}
            .card-val {{ font-size: 22px; font-weight: bold; margin-top: 5px; }}
            .search-box {{ display: flex; gap: 10px; margin-bottom: 20px; }}
            input, button {{ padding: 12px; border-radius: 8px; border: 1px solid #374151; background: #0b0f19; color: #fff; font-size: 14px; }}
            input {{ flex: 1; }}
            button {{ background: #0284c7; border: none; font-weight: bold; cursor: pointer; }}
            button:hover {{ background: #0369a1; }}
            .table-wrap {{ overflow-x: auto; border-radius: 8px; border: 1px solid #1f2937; }}
            table {{ width: 100%; border-collapse: collapse; min-width: 800px; background: #0b0f19; }}
            th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #1f2937; font-size: 13px; }}
            th {{ background: #161e2e; color: #38bdf8; }}
            .badge-active {{ color: #4ade80; background: rgba(74,222,128,0.1); padding: 4px 8px; border-radius: 4px; font-weight: bold; }}
            .badge-locked {{ color: #fbbf24; background: rgba(251,191,36,0.1); padding: 4px 8px; border-radius: 4px; font-weight: bold; }}
            @media(max-width: 768px) {{ .stats {{ grid-template-columns: 1fr; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2><span>Kairos Hub Dashboard</span></h2>
            <div class="stats">
                <div class="card"><div class="card-title">Total Keys</div><div class="card-val">{total_keys}</div></div>
                <div class="card green"><div class="card-title">Active Keys</div><div class="card-val">{active_keys}</div></div>
                <div class="card orange"><div class="card-title">Bound HWID</div><div class="card-val">{bound_keys}</div></div>
            </div>
            <form method="GET" action="/dashboard-abyssal-code-2015" class="search-box">
                <input type="text" name="q" value="{search_query}" placeholder="Search key or note...">
                <button type="submit">Search</button>
            </form>
            <div class="table-wrap">
                <table>
                    <tr><th>Key</th><th>Status</th><th>Lock</th><th>Expires</th><th>HWID</th><th>Note</th></tr>
    """

    for row in keys:
        k, status, exp, hwid, _, note, _, is_locked = row
        lock_str = "<span class='badge-locked'>LOCKED</span>" if is_locked == 1 else "UNLOCKED"
        hwid_str = hwid if hwid else "<i>Unbound</i>"
        html_content += f"<tr><td><code>{k}</code></td><td><span class='badge-active'>{status}</span></td><td>{lock_str}</td><td>{exp}</td><td><small>{hwid_str}</small></td><td>{note}</td></tr>"

    html_content += """
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("api:app", host="0.0.0.0", port=port)