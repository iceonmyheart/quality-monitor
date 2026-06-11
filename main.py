import uvicorn
from fastapi import FastAPI, HTTPException, Form, Cookie, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import sqlite3
import secrets
import csv
import io

app = FastAPI(title="Quality Monitor Pro")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

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
    # Предустановленные пользователи
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('admin@mail.ru', 'Администратор', 'admin123', 'admin', datetime('now'))")
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('operator@mail.ru', 'Оператор', 'operator123', 'operator', datetime('now'))")
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('quality@mail.ru', 'Менеджер качества', 'quality123', 'quality', datetime('now'))")
    c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('client@example.com', 'Клиент', 'client', 'client', datetime('now'))")
    conn.commit()
    conn.close()

init_db()
sessions = {}

# ---------------------------- API ----------------------------
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
def get_tickets(session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    if user["role"] == "client":
        c.execute("SELECT id, title, description, status, priority, created_at, satisfaction, review FROM tickets WHERE created_by_id=?", (user["id"],))
    elif user["role"] == "operator":
        c.execute("SELECT id, title, description, status, priority, created_at, satisfaction, review FROM tickets WHERE assigned_to_id=? OR assigned_to_id IS NULL", (user["id"],))
    else:
        c.execute("SELECT id, title, description, status, priority, created_at, satisfaction, review FROM tickets")
    tickets = [{"id": row[0], "title": row[1], "description": row[2], "status": row[3], "priority": row[4],
                "created_at": row[5], "satisfaction": row[6], "review": row[7]} for row in c.fetchall()]
    conn.close()
    return {"tickets": tickets}

@app.post("/api/tickets")
def create_ticket(title: str = Form(...), description: str = Form(...), priority: str = Form(...), category: str = Form(""), session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("INSERT INTO tickets (title, description, status, priority, category, created_at, created_by_id) VALUES (?,?,?,?,?,?,?)",
              (title, description, "new", priority, category, datetime.now().isoformat(), user["id"]))
    conn.commit()
    conn.close()
    return {"message": "OK"}

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
        if status == "resolved":
            resolved_at = datetime.now().isoformat()
            updates.append("resolved_at=?")
            params.append(resolved_at)
            # Вычисляем время решения
            c.execute("SELECT created_at FROM tickets WHERE id=?", (ticket_id,))
            row = c.fetchone()
            if row and row[0]:
                created = datetime.fromisoformat(row[0])
                resolved = datetime.fromisoformat(resolved_at)
                resolution_minutes = int((resolved - created).total_seconds() / 60)
                updates.append("resolution_time_minutes=?")
                params.append(resolution_minutes)
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
    # Среднее время решения (в минутах)
    c.execute("SELECT AVG(resolution_time_minutes) FROM tickets WHERE resolution_time_minutes IS NOT NULL AND resolution_time_minutes > 0")
    avg_res = c.fetchone()[0] or 0
    conn.close()
    return {"total_tickets": total, "resolved_tickets": resolved, "avg_csat": round(avg_sat, 2), "avg_resolution_minutes": int(avg_res)}

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
    writer.writerow(["ID", "Title", "Status", "Priority", "Created", "Satisfaction", "Review"])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5] if r[5] else "", r[6] or ""])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tickets.csv"})

@app.get("/")
def index():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Quality Monitor</title>
    <style>
        body { font-family: system-ui; background: #0a192f; color: #e6f1ff; padding: 20px; }
        .card { background: #1e293b; border-radius: 1rem; padding: 1.5rem; margin-bottom: 1rem; }
        button { background: #15803d; border: none; padding: 0.5rem 1rem; border-radius: 0.5rem; color: white; cursor: pointer; }
        input, select, textarea { background: #0f172a; border: 1px solid #334155; padding: 0.5rem; border-radius: 0.5rem; width: 100%; color: white; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 0.5rem; text-align: left; border-bottom: 1px solid #334155; }
        .status-badge { padding: 0.2rem 0.5rem; border-radius: 1rem; font-size: 0.7rem; }
        .status-new { background: #facc1520; color: #facc15; }
        .status-in_progress { background: #3b82f620; color: #3b82f6; }
        .status-resolved { background: #22c55e20; color: #22c55e; }
        .tab-btn { background: none; border: none; padding: 0.5rem 1rem; cursor: pointer; color: #94a3b8; }
        .tab-btn.active { background: #15803d; color: white; }
        .tab-pane { display: none; }
        .tab-pane.active { display: block; }
    </style>
</head>
<body>
<div style="max-width: 1200px; margin: 0 auto;">
    <h1>Quality Monitor</h1>
    <div id="userPanel"></div>
    <div id="app"></div>
</div>
<script>
    let currentUser = null;

    async function api(url, method='GET', body=null) {
        let opts = { method };
        if (body) { opts.body = body; opts.headers = {'Content-Type':'application/x-www-form-urlencoded'}; }
        let res = await fetch(url, opts);
        if (!res.ok) throw new Error(await res.text());
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
            document.getElementById('userPanel').innerHTML = `<span>${currentUser.name} (${currentUser.role})</span> <button onclick="logout()">Выйти</button>`;
            renderUI();
        } catch(e) {
            currentUser = null;
            renderLogin();
        }
    }

    async function logout() {
        await api('/api/logout','POST');
        currentUser = null;
        renderLogin();
    }

    function renderLogin() {
        document.getElementById('app').innerHTML = `
            <div class="card">
                <h2>Вход</h2>
                <form id="loginForm">
                    <div><label>Email</label><input id="email" type="email"></div>
                    <div><label>Пароль</label><input id="password" type="password"></div>
                    <button type="submit">Войти</button>
                </form>
                <hr>
                <h3>Регистрация</h3>
                <form id="registerForm">
                    <div><label>Email</label><input id="regEmail" type="email"></div>
                    <div><label>ФИО</label><input id="regName"></div>
                    <div><label>Пароль</label><input id="regPassword" type="password"></div>
                    <button type="submit">Зарегистрироваться</button>
                </form>
            </div>
        `;
        document.getElementById('loginForm').onsubmit = async (e) => {
            e.preventDefault();
            try {
                await login(e.target.email.value, e.target.password.value);
                alert('Вход выполнен');
            } catch(e) { alert('Ошибка входа'); }
        };
        document.getElementById('registerForm').onsubmit = async (e) => {
            e.preventDefault();
            let form = new URLSearchParams({ email:e.target.regEmail.value, full_name:e.target.regName.value, password:e.target.regPassword.value });
            await fetch('/api/register', { method:'POST', body:form });
            alert('Регистрация успешна');
        };
    }

    async function renderUI() {
        if (!currentUser) return;
        let tabs = [];
        if (currentUser.role === 'client') tabs = ['Мои заявки', 'Новая заявка'];
        else if (currentUser.role === 'operator') tabs = ['Все заявки', 'Экспорт'];
        else if (currentUser.role === 'admin') tabs = ['Дашборд', 'Экспорт'];
        else if (currentUser.role === 'quality') tabs = ['Дашборд', 'Экспорт'];
        let html = `<div class="tabs">${tabs.map((t,i)=>`<button class="tab-btn ${i===0?'active':''}" data-tab="${i}">${t}</button>`).join('')}</div><div id="panes"></div>`;
        document.getElementById('app').innerHTML = html;
        let panesDiv = document.getElementById('panes');
        for (let i=0; i<tabs.length; i++) {
            let pane = document.createElement('div');
            pane.className = `tab-pane ${i===0?'active':''}`;
            pane.id = `pane-${i}`;
            panesDiv.appendChild(pane);
            if (currentUser.role === 'client') {
                if (tabs[i]==='Мои заявки') await renderClientTickets(pane);
                if (tabs[i]==='Новая заявка') renderNewTicket(pane);
            } else if (currentUser.role === 'operator') {
                if (tabs[i]==='Все заявки') await renderOperatorTickets(pane);
                if (tabs[i]==='Экспорт') renderExport(pane);
            } else if (currentUser.role === 'admin' || currentUser.role === 'quality') {
                if (tabs[i]==='Дашборд') await renderDashboard(pane);
                if (tabs[i]==='Экспорт') renderExport(pane);
            }
        }
        document.querySelectorAll('.tab-btn').forEach((btn,idx)=>btn.onclick=()=>{
            document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
            document.getElementById(`pane-${idx}`).classList.add('active');
        });
    }

    async function renderClientTickets(container) {
        let data = await api('/api/tickets');
        let html = `<tr><thead><tr><th>ID</th><th>Название</th><th>Описание</th><th>Статус</th><th>Приоритет</th><th>Дата</th><th>Оценка</th><th>Ответ</th></tr></thead><tbody>`;
        for (let t of data.tickets) {
            html += `<tr><td>${t.id}</td><td>${t.title}</td><td>${t.description || '—'}</td><td><span class="status-badge status-${t.status}">${t.status}</span></td><td>${t.priority}</td><td>${new Date(t.created_at).toLocaleDateString()}</td><td>${t.satisfaction ? '⭐'+t.satisfaction : '—'}</td><td>${t.review || '—'}</td></tr>`;
        }
        html += `</tbody></table>`;
        container.innerHTML = html;
    }

    function renderNewTicket(container) {
        container.innerHTML = `
            <div class="card">
                <h3>Новая заявка</h3>
                <form id="newForm">
                    <div><label>Название</label><input id="title" required></div>
                    <div><label>Описание</label><textarea id="desc" rows="3"></textarea></div>
                    <div><label>Приоритет</label><select id="priority"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></div>
                    <button type="submit">Создать</button>
                </form>
            </div>
        `;
        document.getElementById('newForm').onsubmit = async (e) => {
            e.preventDefault();
            let body = new URLSearchParams({ title:document.getElementById('title').value, description:document.getElementById('desc').value, priority:document.getElementById('priority').value });
            await api('/api/tickets','POST',body);
            alert('Заявка создана');
            renderUI();
        };
    }

    async function renderOperatorTickets(container) {
        let data = await api('/api/tickets');
        let html = `<table><thead><tr><th>ID</th><th>Название</th><th>Описание</th><th>Статус</th><th>Приоритет</th><th>Действия</th></tr></thead><tbody>`;
        for (let t of data.tickets) {
            let actions = '';
            if (t.status === 'new') actions = `<button onclick="assign(${t.id})">Принять</button>`;
            if (t.status === 'in_progress') actions = `<button onclick="resolve(${t.id})">Решить</button> <button onclick="respond(${t.id})">Ответить</button>`;
            if (t.status === 'resolved') actions = `<button onclick="closeTicket(${t.id})">Закрыть</button>`;
            html += `<tr><td>${t.id}</td><td>${t.title}</td><td>${t.description || '—'}</td><td><span class="status-badge status-${t.status}">${t.status}</span></td><td>${t.priority}</td><td>${actions}</td></tr>`;
        }
        html += `</tbody></table>`;
        container.innerHTML = html;
        window.assign = async (id) => { await api(`/api/tickets/${id}?status=in_progress&assigned_to_id=${currentUser.id}`,'PUT'); renderUI(); };
        window.resolve = async (id) => { let rev = prompt("Комментарий к решению:"); await api(`/api/tickets/${id}?status=resolved&review=${encodeURIComponent(rev||'')}`,'PUT'); renderUI(); };
        window.closeTicket = async (id) => { await api(`/api/tickets/${id}?status=closed`,'PUT'); renderUI(); };
        window.respond = async (id) => { let msg = prompt("Ответ клиенту:"); if(msg) await api(`/api/tickets/${id}?review=${encodeURIComponent(msg)}`,'PUT'); renderUI(); };
    }

    function renderExport(container) {
        container.innerHTML = `<div class="card"><a href="/api/export/tickets" target="_blank"><button>Скачать CSV</button></a></div>`;
    }

    async function renderDashboard(container) {
        let m = await api('/api/dashboard/metrics');
        container.innerHTML = `<div class="card"><h3>Дашборд</h3><p>Всего заявок: ${m.total_tickets}</p><p>Решено/закрыто: ${m.resolved_tickets}</p><p>Средний CSAT: ${m.avg_csat}/5</p><p>Среднее время решения: ${m.avg_resolution_minutes} минут</p></div>`;
    }

    loadUser();
</script>
</body>
</html>
    """)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
