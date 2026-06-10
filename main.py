import uvicorn
from fastapi import FastAPI, HTTPException, Form, Cookie, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from datetime import datetime, timedelta
import sqlite3
import secrets
import csv
import io

app = FastAPI(title="Quality Monitor Pro")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=500)

def init_db():
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        full_name TEXT,
        hashed_password TEXT,
        role TEXT,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        status TEXT,
        priority TEXT,
        category TEXT,
        created_at TEXT,
        first_response_at TEXT,
        resolved_at TEXT,
        closed_at TEXT,
        satisfaction INTEGER,
        review TEXT,
        assigned_to_id INTEGER,
        created_by_id INTEGER,
        response_time_minutes INTEGER,
        resolution_time_minutes INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS detailed_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        overall_rating INTEGER,
        speed_rating INTEGER,
        professionalism_rating INTEGER,
        politeness_rating INTEGER,
        comment TEXT,
        created_at TEXT
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_reviews_ticket ON detailed_reviews(ticket_id)")
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        user_id INTEGER,
        comment TEXT,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        filename TEXT,
        filepath TEXT,
        uploaded_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS system_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_time TEXT,
        user_id INTEGER,
        action TEXT,
        details TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sla_settings (
        param_key TEXT PRIMARY KEY,
        param_value INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS knowledge_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        content TEXT,
        category_id INTEGER,
        created_at TEXT
    )''')
    c.execute("INSERT OR IGNORE INTO sla_settings (param_key, param_value) VALUES ('response_high_hours', 2)")
    c.execute("INSERT OR IGNORE INTO sla_settings (param_key, param_value) VALUES ('response_medium_hours', 8)")
    c.execute("INSERT OR IGNORE INTO categories (id, name) VALUES (1, 'Технические проблемы')")
    c.execute("INSERT OR IGNORE INTO categories (id, name) VALUES (2, 'Консультации')")
    c.execute("INSERT OR IGNORE INTO categories (id, name) VALUES (3, 'Доступ и права')")
    c.execute("INSERT OR IGNORE INTO knowledge_articles (id, title, content, category_id, created_at) VALUES (1, 'Как сбросить пароль?', 'Обратитесь в техподдержку через форму заявки', 1, datetime('now'))")
    c.execute("INSERT OR IGNORE INTO knowledge_articles (id, title, content, category_id, created_at) VALUES (2, 'Настройка VPN', 'Скачайте конфигурационный файл из личного кабинета', 2, datetime('now'))")
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('admin@mail.ru', 'Администратор', 'admin123', 'admin', datetime('now'))")
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('operator@mail.ru', 'Оператор', 'operator123', 'operator', datetime('now'))")
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('quality@mail.ru', 'Менеджер качества', 'quality123', 'quality', datetime('now'))")
    conn.commit()
    conn.close()

init_db()
sessions = {}

# ---------------------------------------- API ----------------------------------------
@app.post("/api/register")
def register(email: str = Form(...), full_name: str = Form(...), password: str = Form(...)):
    if email == "admin@mail.ru":
        role = "admin"
    elif email == "operator@mail.ru":
        role = "operator"
    elif email == "quality@mail.ru":
        role = "quality"
    else:
        role = "client"
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (email, full_name, hashed_password, role, created_at) VALUES (?,?,?,?,?)",
                  (email, full_name, password, role, datetime.now().isoformat()))
        conn.commit()
        return {"message": "OK"}
    except:
        raise HTTPException(400, "Email already exists")
    finally:
        conn.close()

@app.post("/api/login")
def login(email: str = Form(...), password: str = Form(...), response: Response = None):
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT id, full_name, role, hashed_password FROM users WHERE email=?", (email,))
    user = c.fetchone()
    conn.close()
    if not user or user[3] != password:
        raise HTTPException(400, "Invalid credentials")
    token = secrets.token_hex(32)
    sessions[token] = {"id": user[0], "name": user[1], "role": user[2]}
    response.set_cookie(key="session", value=token, httponly=True)
    return {"message": "OK", "role": user[2]}

@app.get("/api/me")
def me(session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401, "Unauthorized")
    return sessions[session]

@app.post("/api/logout")
def logout(response: Response, session: str = Cookie(None)):
    if session:
        sessions.pop(session, None)
    response.delete_cookie("session")
    return {"message": "OK"}

@app.get("/api/tickets")
def get_tickets(session: str = Cookie(None), page: int = 1, limit: int = 10,
                status: str = "", priority: str = "", search: str = ""):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    offset = (page - 1) * limit
    query = "SELECT id, title, description, status, priority, category, created_at, satisfaction, review, assigned_to_id, created_by_id FROM tickets WHERE 1=1"
    params = []
    if user["role"] == "client":
        query += " AND created_by_id = ?"
        params.append(user["id"])
    elif user["role"] == "operator":
        query += " AND (assigned_to_id = ? OR assigned_to_id IS NULL)"
        params.append(user["id"])
    if status:
        query += " AND status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    if search:
        query += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    count_query = query.replace("SELECT id, title, description, status, priority, category, created_at, satisfaction, review, assigned_to_id, created_by_id", "SELECT COUNT(*)")
    c.execute(count_query, params)
    total = c.fetchone()[0]
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    c.execute(query, params)
    tickets = []
    for row in c.fetchall():
        tickets.append({
            "id": row[0], "title": row[1], "description": row[2], "status": row[3],
            "priority": row[4], "category": row[5], "created_at": row[6],
            "satisfaction": row[7], "review": row[8], "assigned_to_id": row[9],
            "created_by_id": row[10]
        })
    conn.close()
    return {"tickets": tickets, "total": total, "page": page, "limit": limit}

@app.post("/api/tickets")
def create_ticket(title: str = Form(...), description: str = Form(...), priority: str = Form(...), category: str = Form(""), session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("INSERT INTO tickets (title, description, status, priority, category, created_at, created_by_id) VALUES (?,?,?,?,?,?,?)",
              (title, description, "new", priority, category, datetime.now().isoformat(), user["id"]))
    ticket_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"message": "OK", "id": ticket_id}

@app.put("/api/tickets/{ticket_id}")
def update_ticket(ticket_id: int, status: str = None, assigned_to_id: int = None,
                  satisfaction: int = None, review: str = None, session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    updates = []
    params = []
    if status:
        updates.append("status=?")
        params.append(status)
        if status == "in_progress" and not assigned_to_id:
            assigned_to_id = user["id"]
            updates.append("first_response_at=?")
            params.append(datetime.now().isoformat())
            updates.append("response_time_minutes=?")
            params.append(0)
        if status == "resolved":
            updates.append("resolved_at=?")
            params.append(datetime.now().isoformat())
            # calculate resolution time
            c.execute("SELECT created_at FROM tickets WHERE id=?", (ticket_id,))
            created = c.fetchone()[0]
            if created:
                mins = (datetime.now() - datetime.fromisoformat(created)).total_seconds() / 60
                updates.append("resolution_time_minutes=?")
                params.append(int(mins))
        if status == "closed":
            updates.append("closed_at=?")
            params.append(datetime.now().isoformat())
    if assigned_to_id:
        updates.append("assigned_to_id=?")
        params.append(assigned_to_id)
    if satisfaction is not None:
        updates.append("satisfaction=?")
        params.append(satisfaction)
    if review is not None:
        updates.append("review=?")
        params.append(review)
    params.append(ticket_id)
    if updates:
        c.execute(f"UPDATE tickets SET {','.join(updates)} WHERE id=?", params)
        conn.commit()
    conn.close()
    return {"message": "OK"}

@app.post("/api/tickets/{ticket_id}/detailed_review")
def add_detailed_review(ticket_id: int, overall: int = Form(...), speed: int = Form(...),
                        professionalism: int = Form(...), politeness: int = Form(...),
                        comment: str = Form(""), session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("INSERT INTO detailed_reviews (ticket_id, overall_rating, speed_rating, professionalism_rating, politeness_rating, comment, created_at) VALUES (?,?,?,?,?,?,?)",
              (ticket_id, overall, speed, professionalism, politeness, comment, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"message": "OK"}

@app.get("/api/tickets/{ticket_id}/detailed_review")
def get_detailed_review(ticket_id: int):
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT overall_rating, speed_rating, professionalism_rating, politeness_rating, comment FROM detailed_reviews WHERE ticket_id=? ORDER BY id DESC LIMIT 1", (ticket_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"overall": row[0], "speed": row[1], "professionalism": row[2], "politeness": row[3], "comment": row[4]}
    return None

@app.get("/api/dashboard/advanced_metrics")
def advanced_metrics(period: str = "month", operator_id: int = None, category: str = None, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] not in ["admin", "quality"]:
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    now = datetime.now()
    if period == "week":
        start_date = (now - timedelta(days=7)).isoformat()
    elif period == "month":
        start_date = (now - timedelta(days=30)).isoformat()
    elif period == "quarter":
        start_date = (now - timedelta(days=90)).isoformat()
    else:
        start_date = "1970-01-01"
    query = """SELECT dr.overall_rating, dr.speed_rating, dr.professionalism_rating, dr.politeness_rating,
                      t.assigned_to_id, u.full_name, t.category
               FROM detailed_reviews dr
               JOIN tickets t ON dr.ticket_id = t.id
               LEFT JOIN users u ON t.assigned_to_id = u.id
               WHERE dr.created_at >= ?"""
    params = [start_date]
    if operator_id:
        query += " AND t.assigned_to_id = ?"
        params.append(operator_id)
    if category:
        query += " AND t.category = ?"
        params.append(category)
    c.execute(query, params)
    rows = c.fetchall()
    total = len(rows)
    if total == 0:
        conn.close()
        return {"overall_avg": 0, "speed_avg": 0, "prof_avg": 0, "politeness_avg": 0, "operator_stats": [], "total_reviews": 0}
    overall_avg = sum(r[0] for r in rows) / total
    speed_avg = sum(r[1] for r in rows) / total
    prof_avg = sum(r[2] for r in rows) / total
    politeness_avg = sum(r[3] for r in rows) / total
    op_stats = {}
    for r in rows:
        op_id = r[4]
        op_name = r[5] or "Не назначен"
        if op_id not in op_stats:
            op_stats[op_id] = {"name": op_name, "count": 0, "overall_sum": 0, "speed_sum": 0, "prof_sum": 0, "politeness_sum": 0}
        op_stats[op_id]["count"] += 1
        op_stats[op_id]["overall_sum"] += r[0]
        op_stats[op_id]["speed_sum"] += r[1]
        op_stats[op_id]["prof_sum"] += r[2]
        op_stats[op_id]["politeness_sum"] += r[3]
    operator_stats = [{"name": d["name"], "count": d["count"],
                      "overall_avg": round(d["overall_sum"]/d["count"], 2),
                      "speed_avg": round(d["speed_sum"]/d["count"], 2),
                      "prof_avg": round(d["prof_sum"]/d["count"], 2),
                      "politeness_avg": round(d["politeness_sum"]/d["count"], 2)} for d in op_stats.values()]
    conn.close()
    return {
        "overall_avg": round(overall_avg, 2),
        "speed_avg": round(speed_avg, 2),
        "prof_avg": round(prof_avg, 2),
        "politeness_avg": round(politeness_avg, 2),
        "total_reviews": total,
        "operator_stats": operator_stats
    }

@app.get("/api/dashboard/metrics")
def dashboard_metrics(session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] not in ["admin", "quality"]:
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tickets")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tickets WHERE status IN ('resolved','closed')")
    resolved = c.fetchone()[0]
    c.execute("SELECT AVG(satisfaction) FROM tickets WHERE satisfaction IS NOT NULL")
    avg_sat = c.fetchone()[0] or 0
    c.execute("SELECT status, COUNT(*) FROM tickets GROUP BY status")
    status_counts = dict(c.fetchall())
    c.execute("SELECT DATE(created_at), COUNT(*) FROM tickets WHERE created_at >= DATE('now','-7 days') GROUP BY DATE(created_at)")
    daily = dict(c.fetchall())
    labels = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    data = [daily.get(d, 0) for d in labels]
    c.execute("SELECT COUNT(*) FROM tickets WHERE first_response_at IS NOT NULL")
    total_responded = c.fetchone()[0] or 0
    if total_responded > 0:
        c.execute("SELECT COUNT(*) FROM tickets WHERE first_response_at IS NOT NULL AND response_time_minutes <= 480")
        compliant = c.fetchone()[0]
        sla_compliance = round(compliant / total_responded * 100, 1)
    else:
        sla_compliance = 100.0
    c.execute("SELECT AVG(resolution_time_minutes) FROM tickets WHERE resolution_time_minutes IS NOT NULL")
    avg_res = c.fetchone()[0] or 0
    conn.close()
    return {
        "total_tickets": total,
        "resolved_tickets": resolved,
        "avg_csat": round(avg_sat, 2),
        "status_counts": status_counts,
        "daily_labels": labels,
        "daily_data": data,
        "sla_compliance": sla_compliance,
        "avg_resolution_minutes": round(avg_res, 0)
    }

@app.get("/api/users")
def get_users(session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT id, email, full_name, role FROM users")
    users = [{"id": row[0], "email": row[1], "full_name": row[2], "role": row[3]} for row in c.fetchall()]
    conn.close()
    return users

@app.put("/api/users/{user_id}/role")
def change_role(user_id: int, new_role: str, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    conn.commit()
    conn.close()
    return {"message": "OK"}

@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    current_admin_id = sessions[session]["id"]
    if user_id == current_admin_id:
        raise HTTPException(400, "Нельзя удалить свою учётную запись")
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return {"message": "Пользователь удалён"}

@app.get("/api/admin/logs")
def admin_logs(session: str = Cookie(None), limit: int = 100):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT event_time, user_id, action, details FROM system_logs ORDER BY event_time DESC LIMIT ?", (limit,))
    logs = [{"time": row[0], "user_id": row[1], "action": row[2], "details": row[3]} for row in c.fetchall()]
    conn.close()
    return logs

@app.get("/api/admin/sla")
def admin_get_sla(session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT param_key, param_value FROM sla_settings")
    rows = dict(c.fetchall())
    conn.close()
    return {"response_high_hours": rows.get("response_high_hours", 2), "response_medium_hours": rows.get("response_medium_hours", 8)}

@app.put("/api/admin/sla")
def admin_update_sla(response_high_hours: int, response_medium_hours: int, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("UPDATE sla_settings SET param_value=? WHERE param_key=?", (response_high_hours, "response_high_hours"))
    c.execute("UPDATE sla_settings SET param_value=? WHERE param_key=?", (response_medium_hours, "response_medium_hours"))
    conn.commit()
    conn.close()
    return {"message": "OK"}

@app.get("/api/knowledge")
def get_knowledge(search: str = ""):
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    if search:
        c.execute("SELECT id, title, content, category_id FROM knowledge_articles WHERE title LIKE ? OR content LIKE ?", (f"%{search}%", f"%{search}%"))
    else:
        c.execute("SELECT id, title, content, category_id FROM knowledge_articles")
    articles = [{"id": row[0], "title": row[1], "content": row[2], "category_id": row[3]} for row in c.fetchall()]
    conn.close()
    return articles

@app.get("/api/categories")
def get_categories():
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT id, name FROM categories")
    cats = [{"id": row[0], "name": row[1]} for row in c.fetchall()]
    conn.close()
    return cats

@app.get("/api/export/tickets")
def export_tickets(session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT id, title, status, priority, created_at, satisfaction, review FROM tickets")
    rows = c.fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Title", "Status", "Priority", "Created", "Satisfaction", "OperatorComment"])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5] if r[5] else "", r[6] or ""])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tickets.csv"})

@app.get("/")
def index():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>Оптимасеть | Мониторинг качества</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.css">
    <script src="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.js"></script>
    <style>
        body.light { background: #ffffff; color: #1e293b; }
        body.dark { background: #0a192f; color: #e6f1ff; }
        .card { background: var(--bg); border: 1px solid #e2e8f0; border-radius: 1rem; padding: 1.5rem; margin-bottom: 1.5rem; }
        body.light .card { background: #ffffff; border-color: #e2e8f0; }
        body.dark .card { background: #1e293b; border-color: #334155; }
        .btn-primary { background: #15803d; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; cursor: pointer; border: none; }
        .btn-primary:hover { background: #166534; }
        .status-badge { display: inline-block; padding: 0.2rem 0.7rem; border-radius: 2rem; font-size: 0.7rem; font-weight: 600; }
        .status-new { background: #facc1520; color: #facc15; }
        .status-in_progress { background: #3b82f620; color: #3b82f6; }
        .status-resolved { background: #22c55e20; color: #22c55e; }
        .status-closed { background: #64748b20; color: #94a3b8; }
        .tab-btn { padding: 0.5rem 1rem; border-radius: 2rem; cursor: pointer; transition: 0.2s; }
        .tab-btn.active { background: #15803d; color: white; }
        body.light input, body.light select, body.light textarea { background: #f8fafc; border: 1px solid #cbd5e1; color: #1e293b; }
        body.dark input, body.dark select, body.dark textarea { background: #0f172a; border: 1px solid #334155; color: #e2e8f0; }
        input, select, textarea { border-radius: 0.5rem; padding: 0.5rem; width: 100%; outline: none; }
        input:focus, select:focus, textarea:focus { border-color: #15803d; }
        @media (max-width: 768px) {
            body { padding: 0.5rem; }
            .container { padding: 0.5rem; }
            .card { padding: 1rem; margin-bottom: 1rem; }
            .tab-btn { padding: 0.4rem 0.8rem; font-size: 0.8rem; }
            .btn-primary { padding: 0.6rem 1rem; font-size: 1rem; }
            table, thead, tbody, th, td, tr { display: block; }
            thead { display: none; }
            tr { margin-bottom: 1rem; border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 0.5rem; background: inherit; }
            td { display: flex; justify-content: space-between; align-items: center; padding: 0.4rem; border-bottom: none; }
            td:before { content: attr(data-label); font-weight: bold; width: 40%; color: #15803d; }
            .grid { grid-template-columns: 1fr !important; }
        }
    </style>
</head>
<body class="light">
<div class="max-w-7xl mx-auto px-4 py-6 container">
    <div class="flex justify-between items-center mb-8 p-4 bg-white shadow rounded-2xl border">
        <div class="flex items-center gap-3"><div class="text-3xl">🛜</div><div><h1 class="text-2xl font-bold text-gray-800">ОПТИМАСЕТЬ</h1><p class="text-xs text-gray-500">Мониторинг качества услуг и технической поддержки</p></div></div>
        <div class="flex items-center gap-4"><button id="themeToggle" class="text-2xl">🌙</button><div id="userPanel"></div></div>
    </div>
    <div id="app"></div>
</div>
<script>
    let currentUser = null;
    let theme = localStorage.getItem('theme') || 'light';
    const notyf = new Notyf({ duration:3000, position:{x:'right',y:'top'} });

    function applyTheme() {
        if(theme === 'dark') { document.body.classList.remove('light'); document.body.classList.add('dark'); document.getElementById('themeToggle').innerText = '☀️'; }
        else { document.body.classList.remove('dark'); document.body.classList.add('light'); document.getElementById('themeToggle').innerText = '🌙'; }
        localStorage.setItem('theme', theme);
    }
    applyTheme();
    document.getElementById('themeToggle').onclick = () => { theme = theme === 'dark' ? 'light' : 'dark'; applyTheme(); };

    async function api(url, method='GET', body=null) {
        let opts = { method };
        if(body) { opts.body = body; opts.headers = {'Content-Type':'application/x-www-form-urlencoded'}; }
        let res = await fetch(url, opts);
        if(!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function login(email, password) {
        let form = new URLSearchParams({email,password});
        await api('/api/login','POST',form);
        await loadUser();
    }

    async function loadUser() {
        try {
            currentUser = await api('/api/me');
            document.getElementById('userPanel').innerHTML = `<span class="font-medium">${currentUser.name}</span><span class="bg-gray-200 text-gray-800 px-2 py-0.5 rounded-full text-sm">${currentUser.role}</span><button class="bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1 rounded-full text-sm" onclick="logout()">Выйти</button>`;
            renderUI();
        } catch(e) { currentUser = null; renderLogin(); }
    }

    async function logout() { await api('/api/logout','POST'); currentUser = null; renderLogin(); }

    function renderLogin() {
        document.getElementById('app').innerHTML = `
            <div class="max-w-md mx-auto card"><h2 class="text-2xl font-bold mb-4">Вход</h2>
            <form id="loginForm" class="space-y-4"><div><label class="block text-sm font-medium">Email</label><input id="email" type="email" class="w-full"></div>
            <div><label class="block text-sm font-medium">Пароль</label><input id="password" type="password" class="w-full"></div>
            <button type="submit" class="btn-primary w-full">Войти</button></form>
            <hr class="my-6 border-gray-200"><h3 class="text-xl font-semibold mb-4">Регистрация</h3>
            <form id="registerForm" class="space-y-4"><div><label class="block text-sm font-medium">Email</label><input id="regEmail" type="email" class="w-full"></div>
            <div><label class="block text-sm font-medium">ФИО</label><input id="regName" class="w-full"></div>
            <div><label class="block text-sm font-medium">Пароль</label><input id="regPassword" type="password" class="w-full"></div>
            <button type="submit" class="btn-primary w-full">Зарегистрироваться</button></form></div>`;
        document.getElementById('loginForm').onsubmit = async (e) => { e.preventDefault(); try { await login(e.target.email.value, e.target.password.value); notyf.success('Вход выполнен'); } catch(e) { notyf.error('Ошибка входа'); } };
        document.getElementById('registerForm').onsubmit = async (e) => { e.preventDefault(); let form = new URLSearchParams({ email:e.target.regEmail.value, full_name:e.target.regName.value, password:e.target.regPassword.value }); await fetch('/api/register', { method:'POST', body:form }); notyf.success('Регистрация успешна, теперь войдите'); };
    }

    async function renderUI() {
        if(!currentUser) return;
        let tabs = [];
        if(currentUser.role === 'client') tabs = ['Мои заявки', 'Новая заявка', 'База знаний'];
        else if(currentUser.role === 'operator') tabs = ['Все заявки', 'Экспорт'];
        else if(currentUser.role === 'admin') tabs = ['Пользователи', 'Настройки SLA', 'Логи', 'База знаний', 'Дашборд', 'Аналитика оценок', 'Экспорт'];
        else if(currentUser.role === 'quality') tabs = ['Дашборд', 'Аналитика оценок', 'База знаний', 'Экспорт'];
        let html = `<div class="flex gap-2 mb-4 border-b pb-2 flex-wrap">${tabs.map((t,i)=>`<button class="tab-btn ${i===0?'active':''}" data-tab="${i}">${t}</button>`).join('')}</div><div id="panes"></div>`;
        document.getElementById('app').innerHTML = html;
        let panesDiv = document.getElementById('panes');
        for(let i=0; i<tabs.length; i++) {
            let pane = document.createElement('div'); pane.className = `tab-pane ${i===0?'block':'hidden'}`; pane.id = `pane-${i}`;
            panesDiv.appendChild(pane);
            if(currentUser.role === 'client') {
                if(tabs[i]==='Мои заявки') await renderClientTickets(pane);
                if(tabs[i]==='Новая заявка') renderNewTicket(pane);
                if(tabs[i]==='База знаний') await renderKnowledge(pane);
            } else if(currentUser.role === 'operator') {
                if(tabs[i]==='Все заявки') await renderOperatorTickets(pane);
                if(tabs[i]==='Экспорт') renderExport(pane);
            } else if(currentUser.role === 'admin') {
                if(tabs[i]==='Пользователи') await renderAdminUsers(pane);
                if(tabs[i]==='Настройки SLA') await renderAdminSLA(pane);
                if(tabs[i]==='Логи') await renderAdminLogs(pane);
                if(tabs[i]==='База знаний') await renderKnowledge(pane);
                if(tabs[i]==='Дашборд') await renderDashboard(pane);
                if(tabs[i]==='Аналитика оценок') await renderAdvancedDashboard(pane);
                if(tabs[i]==='Экспорт') renderExport(pane);
            } else if(currentUser.role === 'quality') {
                if(tabs[i]==='Дашборд') await renderDashboard(pane);
                if(tabs[i]==='Аналитика оценок') await renderAdvancedDashboard(pane);
                if(tabs[i]==='База знаний') await renderKnowledge(pane);
                if(tabs[i]==='Экспорт') renderExport(pane);
            }
        }
        document.querySelectorAll('.tab-btn').forEach((btn,idx)=>btn.onclick=()=>{ document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active')); btn.classList.add('active'); document.querySelectorAll('.tab-pane').forEach(p=>p.classList.add('hidden')); document.getElementById(`pane-${idx}`).classList.remove('hidden'); });
    }

    // ---------------------------- Рендеры ----------------------------
    async function renderClientTickets(container) {
        let data = await api('/api/tickets');
        let html = '<div class="card overflow-x-auto"><table class="w-full"><thead><tr><th>Номер</th><th>Название</th><th>Описание</th><th>Статус</th><th>Приоритет</th><th>Дата</th><th>Ответ оператора</th><th>Оценка</th><th>Отзыв</th><th></th></tr></thead><tbody>';
        for(let t of data.tickets) {
            let reviewShort = t.review ? t.review.substring(0,60)+(t.review.length>60?'…':'') : '—';
            let actionBtn = '';
            if(t.status === 'resolved' && !t.satisfaction) {
                actionBtn = `<button class="bg-green-600 text-white px-2 py-1 rounded text-sm" onclick="openDetailedReview(${t.id})">Оценить качество</button>`;
            }
            html += `<tr>
                <td data-label="Номер">${t.id}</td>
                <td data-label="Название">${t.title}</td>
                <td data-label="Описание">${t.description ? t.description.substring(0,40)+(t.description.length>40?'…':'') : '—'}</td>
                <td data-label="Статус"><span class="status-badge status-${t.status}">${t.status}</span></td>
                <td data-label="Приоритет">${t.priority}</td>
                <td data-label="Дата">${new Date(t.created_at).toLocaleDateString()}</td>
                <td data-label="Ответ оператора">${reviewShort}</td>
                <td data-label="Оценка">${t.satisfaction ? '⭐'+t.satisfaction : '—'}</td>
                <td data-label="Отзыв">${t.review ? t.review.substring(0,40) : '—'}</td>
                <td data-label="Действие">${actionBtn}</td>
            </tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.openDetailedReview = async (id) => {
            const modalHtml = `
                <div id="reviewModal" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
                    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md">
                        <h3 class="text-xl font-bold mb-4">Оцените качество обслуживания</h3>
                        <div class="space-y-4">
                            <div><label class="block font-medium">Общая оценка (1-5)</label><div class="flex gap-1 stars" data-criterion="overall">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer text-gray-400 hover:text-yellow-400" data-value="${v}">★</span>`).join('')}</div><input type="hidden" id="overallVal"></div>
                            <div><label class="block font-medium">Скорость ответа (1-5)</label><div class="flex gap-1 stars" data-criterion="speed">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer text-gray-400 hover:text-yellow-400" data-value="${v}">★</span>`).join('')}</div><input type="hidden" id="speedVal"></div>
                            <div><label class="block font-medium">Профессионализм (1-5)</label><div class="flex gap-1 stars" data-criterion="prof">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer text-gray-400 hover:text-yellow-400" data-value="${v}">★</span>`).join('')}</div><input type="hidden" id="profVal"></div>
                            <div><label class="block font-medium">Вежливость (1-5)</label><div class="flex gap-1 stars" data-criterion="politeness">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer text-gray-400 hover:text-yellow-400" data-value="${v}">★</span>`).join('')}</div><input type="hidden" id="politenessVal"></div>
                            <div><label class="block font-medium">Комментарий</label><textarea id="reviewComment" rows="3" class="w-full border rounded p-2"></textarea></div>
                            <div class="flex justify-end gap-2"><button class="bg-gray-200 px-4 py-2 rounded" onclick="closeModal()">Отмена</button><button class="bg-green-600 text-white px-4 py-2 rounded" onclick="submitDetailedReview(${id})">Отправить</button></div>
                        </div>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            document.querySelectorAll('.stars').forEach(group => {
                let criterion = group.dataset.criterion;
                group.querySelectorAll('.star').forEach(star => {
                    star.addEventListener('click', function() {
                        let val = this.dataset.value;
                        group.querySelectorAll('.star').forEach(s => s.classList.remove('text-yellow-400'));
                        this.classList.add('text-yellow-400');
                        document.getElementById(`${criterion}Val`).value = val;
                    });
                });
            });
        };
        window.closeModal = () => { document.getElementById('reviewModal')?.remove(); };
        window.submitDetailedReview = async (id) => {
            let overall = document.getElementById('overallVal')?.value;
            let speed = document.getElementById('speedVal')?.value;
            let prof = document.getElementById('profVal')?.value;
            let politeness = document.getElementById('politenessVal')?.value;
            let comment = document.getElementById('reviewComment')?.value || '';
            if(!overall || !speed || !prof || !politeness) { notyf.error('Заполните все оценки'); return; }
            let form = new URLSearchParams({ overall, speed, professionalism:prof, politeness, comment });
            await api(`/api/tickets/${id}/detailed_review`, 'POST', form);
            notyf.success('Спасибо за отзыв!');
            closeModal();
            renderUI();
        };
    }

    function renderNewTicket(container) {
        container.innerHTML = `<div class="card"><h3 class="text-xl font-semibold mb-4">Новая заявка</h3>
        <form id="newForm" class="space-y-4">
            <div><label class="block text-sm font-medium">Название</label><input id="title" required></div>
            <div><label class="block text-sm font-medium">Описание</label><textarea id="desc" rows="3"></textarea></div>
            <div><label class="block text-sm font-medium">Приоритет</label><select id="priority"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></div>
            <div><label class="block text-sm font-medium">Категория</label><select id="category"></select></div>
            <button type="submit" class="btn-primary">Создать заявку</button>
        </form></div>`;
        fetch('/api/categories').then(r=>r.json()).then(cats=>{ let sel=document.getElementById('category'); cats.forEach(c=>{ let opt=document.createElement('option'); opt.value=c.name; opt.innerText=c.name; sel.appendChild(opt); }); });
        document.getElementById('newForm').onsubmit = async (e) => {
            e.preventDefault();
            let body = new URLSearchParams({ title:document.getElementById('title').value, description:document.getElementById('desc').value, priority:document.getElementById('priority').value, category:document.getElementById('category').value });
            await api('/api/tickets', 'POST', body);
            notyf.success('Заявка создана');
            renderUI();
        };
    }

    async function renderKnowledge(container) {
        let articles = await api('/api/knowledge');
        let html = `<div class="card"><h3 class="text-xl font-semibold mb-4">База знаний</h3><input type="text" id="kbSearch" placeholder="Поиск..." class="mb-4">`;
        html += articles.map(a=>`<div class="bg-gray-100 dark:bg-gray-800 p-3 rounded-lg mb-2"><b>${a.title}</b><br>${a.content}</div>`).join('');
        html += `</div>`;
        container.innerHTML = html;
        document.getElementById('kbSearch').addEventListener('input', async (e) => {
            let arts = await api(`/api/knowledge?search=${encodeURIComponent(e.target.value)}`);
            document.querySelector('#pane-2 .card .mb-4').nextSibling.remove();
            let newDiv = document.createElement('div');
            newDiv.innerHTML = arts.map(a=>`<div class="bg-gray-100 dark:bg-gray-800 p-3 rounded-lg mb-2"><b>${a.title}</b><br>${a.content}</div>`).join('');
            document.querySelector('#pane-2 .card').appendChild(newDiv);
        });
    }

    async function renderOperatorTickets(container) {
        let data = await api('/api/tickets');
        let html = '<div class="card overflow-x-auto"><table class="w-full"><thead><tr><th>Номер</th><th>Название</th><th>Описание</th><th>Статус</th><th>Приоритет</th><th>Действия</th></tr></thead><tbody>';
        for(let t of data.tickets) {
            let actions = '';
            if(t.status === 'new') actions = `<button class="bg-yellow-500 text-white px-2 py-1 rounded text-sm" onclick="assign(${t.id})">Принять</button>`;
            if(t.status === 'in_progress') actions = `<button class="bg-green-600 text-white px-2 py-1 rounded text-sm" onclick="resolve(${t.id})">Решить</button> <button class="bg-blue-600 text-white px-2 py-1 rounded text-sm" onclick="respond(${t.id})">Ответить</button>`;
            if(t.status === 'resolved') actions = `<button class="bg-red-600 text-white px-2 py-1 rounded text-sm" onclick="closeTicket(${t.id})">Закрыть</button>`;
            html += `<tr>
                <td data-label="Номер">${t.id}</td>
                <td data-label="Название">${t.title}</td>
                <td data-label="Описание">${t.description ? t.description.substring(0,40)+(t.description.length>40?'…':'') : '—'}</td>
                <td data-label="Статус"><span class="status-badge status-${t.status}">${t.status}</span></td>
                <td data-label="Приоритет">${t.priority}</td>
                <td data-label="Действия">${actions}</td>
            </tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.assign = async (id) => { await api(`/api/tickets/${id}?status=in_progress&assigned_to_id=${currentUser.id}`, 'PUT'); renderUI(); };
        window.resolve = async (id) => { 
            let comment = prompt("Введите ответ/решение, который увидит клиент:");
            if(comment !== null) {
                await api(`/api/tickets/${id}?status=resolved&review=${encodeURIComponent(comment||'')}`, 'PUT');
                notyf.success('Заявка решена, ответ отправлен');
            } else {
                await api(`/api/tickets/${id}?status=resolved`, 'PUT');
            }
            renderUI(); 
        };
        window.closeTicket = async (id) => { await api(`/api/tickets/${id}?status=closed`, 'PUT'); renderUI(); };
        window.respond = async (id) => { let msg = prompt("Введите промежуточный ответ (будет виден клиенту):"); if(msg) { await api(`/api/tickets/${id}?review=${encodeURIComponent(msg)}`, 'PUT'); notyf.success("Ответ сохранён"); renderUI(); } };
    }

    function renderExport(container) {
        container.innerHTML = `<div class="card"><h3 class="text-xl font-semibold mb-4">Экспорт</h3><a href="/api/export/tickets" target="_blank"><button class="btn-primary">Скачать заявки CSV</button></a></div>`;
    }

    async function renderAdminUsers(container) {
        let users = await api('/api/users');
        let html = '<div class="card overflow-x-auto"><h3 class="text-xl font-semibold mb-4">Управление пользователями</h3><table class="w-full"><thead><tr><th>ID</th><th>Email</th><th>ФИО</th><th>Роль</th><th>Новая роль</th><th></th></tr></thead><tbody>';
        for(let u of users) {
            html += `<tr><td data-label="ID">${u.id}</td><td data-label="Email">${u.email}</td><td data-label="ФИО">${u.full_name}</td><td data-label="Роль">${u.role}</td><td data-label="Новая роль"><select id="role-${u.id}"><option>client</option><option>operator</option><option>admin</option><option>quality</option></select></td><td data-label="Действия"><button class="bg-blue-600 text-white px-2 py-1 rounded text-sm" onclick="changeRole(${u.id})">Изменить</button> <button class="bg-red-600 text-white px-2 py-1 rounded text-sm" onclick="delUser(${u.id})">Удалить</button></td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.changeRole = async (id) => { let newRole = document.getElementById(`role-${id}`).value; await api(`/api/users/${id}/role?new_role=${newRole}`, 'PUT'); notyf.success('Роль изменена'); renderAdminUsers(container); };
        window.delUser = async (id) => { if(confirm('Удалить пользователя?')) { await fetch(`/api/users/${id}`, { method:'DELETE' }); notyf.success('Пользователь удалён'); renderAdminUsers(container); } };
    }

    async function renderAdminSLA(container) {
        let sla = await api('/api/admin/sla');
        container.innerHTML = `<div class="card"><h3 class="text-xl font-semibold mb-4">Настройки SLA</h3>
        <div class="space-y-4"><div><label>Время ответа для High/Critical (часы)</label><input type="number" id="high" value="${sla.response_high_hours}"></div>
        <div><label>Время ответа для Medium/Low (часы)</label><input type="number" id="medium" value="${sla.response_medium_hours}"></div>
        <button class="btn-primary" id="saveSla">Сохранить</button></div></div>`;
        document.getElementById('saveSla').onclick = async () => { await api(`/api/admin/sla?response_high_hours=${document.getElementById('high').value}&response_medium_hours=${document.getElementById('medium').value}`, 'PUT'); notyf.success('Настройки сохранены'); };
    }

    async function renderAdminLogs(container) {
        let logs = await api('/api/admin/logs');
        let html = `<div class="card overflow-x-auto"><h3 class="text-xl font-semibold mb-4">Логи системы</h3><table class="w-full"><thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Детали</th></tr></thead><tbody>`;
        for(let l of logs) html += `<tr><td>${new Date(l.time).toLocaleString()}</td><td>${l.user_id}</td><td>${l.action}</td><td>${l.details||''}</td></tr>`;
        html += `</tbody></table></div>`;
        container.innerHTML = html;
    }

    async function renderDashboard(container) {
        let m = await api('/api/dashboard/metrics');
        const statusLabels = Object.keys(m.status_counts);
        const statusData = Object.values(m.status_counts);
        const dailyLabels = m.daily_labels;
        const dailyData = m.daily_data;
        const slaCompliance = m.sla_compliance;
        const avgResolution = m.avg_resolution_minutes;
        container.innerHTML = `
            <div class="card">
                <h3 class="text-xl font-semibold mb-4 text-gray-800 dark:text-white">📊 Дашборд качества</h3>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
                    <div class="bg-gradient-to-br from-blue-500 to-blue-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Всего заявок</div><div class="text-3xl font-bold">${m.total_tickets}</div></div>
                    <div class="bg-gradient-to-br from-green-500 to-green-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Решено / закрыто</div><div class="text-3xl font-bold">${m.resolved_tickets}</div><div class="text-xs mt-1">${((m.resolved_tickets/m.total_tickets)*100).toFixed(1)}%</div></div>
                    <div class="bg-gradient-to-br from-purple-500 to-purple-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Средний CSAT</div><div class="text-3xl font-bold">${m.avg_csat}/5</div></div>
                    <div class="bg-gradient-to-br from-orange-500 to-orange-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Соблюдение SLA</div><div class="text-3xl font-bold">${slaCompliance}%</div></div>
                    <div class="bg-gradient-to-br from-cyan-500 to-cyan-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Ср. время решения</div><div class="text-3xl font-bold">${avgResolution}<span class="text-lg"> мин</span></div></div>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div class="bg-gray-50 dark:bg-gray-800 p-4 rounded-xl"><canvas id="statusChartDash" height="200"></canvas></div>
                    <div class="bg-gray-50 dark:bg-gray-800 p-4 rounded-xl"><canvas id="trendChartDash" height="200"></canvas></div>
                </div>
                <div class="text-xs text-gray-500 text-center mt-6">Данные обновлены: ${new Date().toLocaleString()}</div>
            </div>
        `;
        const statusColors = { 'new':'#facc15','in_progress':'#3b82f6','resolved':'#22c55e','closed':'#64748b'};
        const backColors = statusLabels.map(s=>statusColors[s]||'#94a3b8');
        new Chart(document.getElementById('statusChartDash'), { type:'pie', data:{ labels:statusLabels.map(s=>({new:'Новые',in_progress:'В работе',resolved:'Решённые',closed:'Закрытые'}[s]||s)), datasets:[{ data:statusData, backgroundColor:backColors }] }, options:{ responsive:true, maintainAspectRatio:true } });
        new Chart(document.getElementById('trendChartDash'), { type:'line', data:{ labels:dailyLabels, datasets:[{ label:'Заявки', data:dailyData, borderColor:'#3b82f6', fill:true, tension:0.3 }] }, options:{ responsive:true } });
    }

    async function renderAdvancedDashboard(container) {
        container.innerHTML = `<div class="card"><h3 class="text-xl font-semibold mb-4">Аналитика оценок</h3>
        <div class="flex flex-wrap gap-4 mb-6">
            <div><label class="block text-sm">Период</label><select id="periodFilter"><option value="month">Месяц</option><option value="week">Неделя</option><option value="quarter">Квартал</option></select></div>
            <div><label class="block text-sm">Оператор</label><select id="operatorFilter"><option value="">Все</option></select></div>
            <div><label class="block text-sm">Категория</label><select id="categoryFilter"><option value="">Все</option></select></div>
            <button class="btn-primary" id="applyFilters">Применить</button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-gray-100 dark:bg-gray-800 p-4 rounded-xl"><div class="text-sm">Общая оценка</div><div id="overallAvg" class="text-2xl font-bold">-</div></div>
            <div class="bg-gray-100 dark:bg-gray-800 p-4 rounded-xl"><div class="text-sm">Скорость ответа</div><div id="speedAvg" class="text-2xl font-bold">-</div></div>
            <div class="bg-gray-100 dark:bg-gray-800 p-4 rounded-xl"><div class="text-sm">Профессионализм</div><div id="profAvg" class="text-2xl font-bold">-</div></div>
            <div class="bg-gray-100 dark:bg-gray-800 p-4 rounded-xl"><div class="text-sm">Вежливость</div><div id="politenessAvg" class="text-2xl font-bold">-</div></div>
        </div>
        <div class="mb-6"><canvas id="operatorChart" height="300"></canvas></div>
        <div class="overflow-x-auto"><table class="w-full"><thead><tr><th>Оператор</th><th>Оценок</th><th>Общая</th><th>Скорость</th><th>Проф.</th><th>Вежливость</th></tr></thead><tbody id="operatorTable"></tbody></table></div>
        </div>`;
        const usersRes = await fetch('/api/users');
        const users = await usersRes.json();
        const catRes = await fetch('/api/categories');
        const cats = await catRes.json();
        let opSelect = document.getElementById('operatorFilter');
        opSelect.innerHTML = '<option value="">Все</option>' + users.filter(u=>u.role==='operator').map(u=>`<option value="${u.id}">${u.full_name}</option>`).join('');
        let catSelect = document.getElementById('categoryFilter');
        catSelect.innerHTML = '<option value="">Все</option>' + cats.map(c=>`<option value="${c.name}">${c.name}</option>`).join('');
        const loadData = async () => {
            let period = document.getElementById('periodFilter').value;
            let operator_id = document.getElementById('operatorFilter').value;
            let category = document.getElementById('categoryFilter').value;
            let params = new URLSearchParams({ period });
            if(operator_id) params.append('operator_id', operator_id);
            if(category) params.append('category', category);
            let data = await api(`/api/dashboard/advanced_metrics?${params}`);
            document.getElementById('overallAvg').innerText = data.overall_avg;
            document.getElementById('speedAvg').innerText = data.speed_avg;
            document.getElementById('profAvg').innerText = data.prof_avg;
            document.getElementById('politenessAvg').innerText = data.politeness_avg;
            let tableHtml = '';
            for(let op of data.operator_stats) {
                tableHtml += `<tr><td data-label="Оператор">${op.name}</td><td data-label="Оценок">${op.count}</td><td data-label="Общая">${op.overall_avg}</td><td data-label="Скорость">${op.speed_avg}</td><td data-label="Проф.">${op.prof_avg}</td><td data-label="Вежливость">${op.politeness_avg}</td></tr>`;
            }
            document.getElementById('operatorTable').innerHTML = tableHtml;
            const ctx = document.getElementById('operatorChart').getContext('2d');
            if(window.opChart) window.opChart.destroy();
            let labels = data.operator_stats.map(o=>o.name);
            let overalls = data.operator_stats.map(o=>o.overall_avg);
            window.opChart = new Chart(ctx, { type:'bar', data:{ labels, datasets:[{ label:'Общая оценка', data:overalls, backgroundColor:'#15803d' }] } });
        };
        document.getElementById('applyFilters').addEventListener('click', loadData);
        loadData();
    }

    loadUser();
</script>
</body>
</html>
    """)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🚀 Сервер Оптимасеть запущен на порту {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
