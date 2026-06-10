import uvicorn
from fastapi import FastAPI, HTTPException, Form, Cookie, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from datetime import datetime, timedelta
import sqlite3
import secrets

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
        created_at TEXT,
        first_response_at TEXT,
        resolved_at TEXT,
        satisfaction INTEGER,
        review TEXT,
        assigned_to_id INTEGER,
        created_by_id INTEGER
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_created_by ON tickets(created_by_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at)")
    conn.commit()
    conn.close()

init_db()
sessions = {}

@app.post("/api/register")
def register(email: str = Form(...), full_name: str = Form(...), password: str = Form(...), role: str = Form(...)):
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
        c.execute("SELECT id, title, status, priority, created_at, satisfaction, review, resolved_at, assigned_to_id FROM tickets WHERE created_by_id=?", (user["id"],))
    elif user["role"] == "operator":
        c.execute("SELECT id, title, status, priority, created_at, satisfaction, review, resolved_at, assigned_to_id FROM tickets WHERE assigned_to_id=? OR assigned_to_id IS NULL", (user["id"],))
    else:
        c.execute("SELECT id, title, status, priority, created_at, satisfaction, review, resolved_at, assigned_to_id FROM tickets")
    tickets = [{"id": row[0], "title": row[1], "status": row[2], "priority": row[3],
                "created_at": row[4], "satisfaction": row[5], "review": row[6],
                "resolved_at": row[7], "assigned_to_id": row[8]} for row in c.fetchall()]
    conn.close()
    return {"tickets": tickets}

@app.post("/api/tickets")
def create_ticket(title: str = Form(...), description: str = Form(...), priority: str = Form(...), session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("INSERT INTO tickets (title, description, status, priority, created_at, created_by_id) VALUES (?,?,?,?,?,?)",
              (title, description, "new", priority, datetime.now().isoformat(), user["id"]))
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
        if status == "resolved":
            updates.append("resolved_at=?")
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
    conn.close()
    return {
        "total_tickets": total,
        "resolved_tickets": resolved,
        "avg_csat": round(avg_sat, 2),
        "status_counts": status_counts,
        "daily_labels": labels,
        "daily_data": data
    }

@app.get("/api/export/tickets")
def export_tickets(session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    conn = sqlite3.connect("monitoring.db")
    c = conn.cursor()
    c.execute("SELECT id, title, status, priority, created_at, satisfaction, review FROM tickets")
    rows = c.fetchall()
    conn.close()
    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Title", "Status", "Priority", "Created", "Satisfaction", "Review"])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5] if r[5] else "", r[6] or ""])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tickets.csv"})

# ================= HTML =================
@app.get("/")
def index():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Оптимасеть | Мониторинг качества</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.css">
    <script src="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.js"></script>
    <style>
        body.light { background:#ffffff; color:#1e293b; }
        body.dark { background:#0a192f; color:#e6f1ff; }
        .card { background: var(--bg); border:1px solid #e2e8f0; border-radius:1rem; padding:1.5rem; margin-bottom:1.5rem; }
        .btn-primary { background:#15803d; color:white; padding:0.5rem 1rem; border-radius:0.5rem; }
        .status-badge { padding:0.2rem 0.7rem; border-radius:2rem; font-size:0.7rem; }
        .status-new { background:#facc1520; color:#facc15; }
        .status-in_progress { background:#3b82f620; color:#3b82f6; }
        .status-resolved { background:#22c55e20; color:#22c55e; }
        .status-closed { background:#64748b20; color:#94a3b8; }
        .tab-btn.active { background:#15803d; color:white; }
        input, select, textarea { background:#0f172a; border:1px solid #334155; border-radius:0.5rem; padding:0.5rem; width:100%; color:white; }
    </style>
</head>
<body class="light">
<div class="max-w-7xl mx-auto px-4 py-6">
    <div class="flex justify-between items-center mb-8 p-4 bg-white shadow rounded-2xl">
        <div class="flex items-center gap-3">
            <div class="text-3xl">🛜</div>
            <div>
                <h1 class="text-2xl font-bold text-gray-800">ОПТИМАСЕТЬ</h1>
                <p class="text-xs text-gray-500">Мониторинг качества услуг и технической поддержки</p>
            </div>
        </div>
        <div class="flex items-center gap-4">
            <button id="themeToggle" class="text-2xl">🌙</button>
            <div id="userPanel"></div>
        </div>
    </div>
    <div id="app"></div>
</div>
<script>
    let currentUser = null;
    let theme = localStorage.getItem('theme') || 'light';
    const notyf = new Notyf({ duration:3000, position:{x:'right',y:'top'} });

    function applyTheme() {
        if(theme === 'dark') { document.body.classList.remove('light'); document.body.classList.add('dark'); document.getElementById('themeToggle').innerText='☀️'; }
        else { document.body.classList.remove('dark'); document.body.classList.add('light'); document.getElementById('themeToggle').innerText='🌙'; }
        localStorage.setItem('theme', theme);
    }
    applyTheme();
    document.getElementById('themeToggle').onclick = () => { theme = theme==='dark'?'light':'dark'; applyTheme(); };

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
            document.getElementById('userPanel').innerHTML = `<span>${currentUser.name}</span> <span class="bg-gray-200 px-2 rounded-full">${currentUser.role}</span> <button class="bg-red-50 text-red-600 px-3 py-1 rounded-full" onclick="logout()">Выйти</button>`;
            renderUI();
        } catch(e) { currentUser=null; renderLogin(); }
    }

    async function logout() { await api('/api/logout','POST'); currentUser=null; renderLogin(); }

    function renderLogin() {
        document.getElementById('app').innerHTML = `
            <div class="card max-w-md mx-auto">
                <h2>Вход</h2>
                <form id="loginForm">
                    <div><label>Email</label><input id="email" type="email"></div>
                    <div><label>Пароль</label><input id="password" type="password"></div>
                    <button type="submit" class="btn-primary">Войти</button>
                </form>
                <hr>
                <h3>Регистрация</h3>
                <form id="registerForm">
                    <div><label>Email</label><input id="regEmail" type="email"></div>
                    <div><label>ФИО</label><input id="regName"></div>
                    <div><label>Пароль</label><input id="regPassword" type="password"></div>
                    <div><label>Роль</label><select id="regRole"><option>client</option><option>operator</option><option>admin</option><option>quality</option></select></div>
                    <button type="submit" class="btn-primary">Зарегистрироваться</button>
                </form>
            </div>
        `;
        document.getElementById('loginForm').onsubmit = async (e) => { e.preventDefault(); try { await login(e.target.email.value, e.target.password.value); notyf.success('Вход выполнен'); } catch(e) { notyf.error('Ошибка входа'); } };
        document.getElementById('registerForm').onsubmit = async (e) => { e.preventDefault(); let form = new URLSearchParams({ email:e.target.regEmail.value, full_name:e.target.regName.value, password:e.target.regPassword.value, role:e.target.regRole.value }); await fetch('/api/register',{method:'POST',body:form}); notyf.success('Регистрация успешна'); };
    }

    async function renderUI() {
        if(!currentUser) return;
        let tabs = [];
        if(currentUser.role === 'client') tabs = ['Мои заявки', 'Новая заявка'];
        else if(currentUser.role === 'operator') tabs = ['Все заявки', 'Экспорт'];
        else if(currentUser.role === 'admin') tabs = ['Пользователи', 'Дашборд', 'Экспорт'];
        else if(currentUser.role === 'quality') tabs = ['Дашборд', 'Экспорт'];
        let html = `<div class="flex gap-2 mb-4">${tabs.map((t,i)=>`<button class="tab-btn px-3 py-1 rounded-full ${i===0?'active':''}" data-tab="${i}">${t}</button>`).join('')}</div><div id="panes"></div>`;
        document.getElementById('app').innerHTML = html;
        let panesDiv = document.getElementById('panes');
        for(let i=0;i<tabs.length;i++) {
            let pane = document.createElement('div'); pane.className = `tab-pane ${i===0?'block':'hidden'}`; pane.id = `pane-${i}`;
            panesDiv.appendChild(pane);
            if(currentUser.role === 'client') {
                if(tabs[i]==='Мои заявки') await renderClientTickets(pane);
                if(tabs[i]==='Новая заявка') renderNewTicket(pane);
            } else if(currentUser.role === 'operator') {
                if(tabs[i]==='Все заявки') await renderOperatorTickets(pane);
                if(tabs[i]==='Экспорт') renderExport(pane);
            } else if(currentUser.role === 'admin') {
                if(tabs[i]==='Пользователи') await renderAdminUsers(pane);
                if(tabs[i]==='Дашборд') await renderDashboard(pane);
                if(tabs[i]==='Экспорт') renderExport(pane);
            } else if(currentUser.role === 'quality') {
                if(tabs[i]==='Дашборд') await renderDashboard(pane);
                if(tabs[i]==='Экспорт') renderExport(pane);
            }
        }
        document.querySelectorAll('.tab-btn').forEach((btn,idx)=>btn.onclick=()=>{
            document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
            btn.classList.add('active');
            document.querySelectorAll('.tab-pane').forEach(p=>p.classList.add('hidden'));
            document.getElementById(`pane-${idx}`).classList.remove('hidden');
        });
    }

    async function renderClientTickets(container) {
        let data = await api('/api/tickets');
        let html = `<div class="card"><table class="w-full"><thead><tr><th>ID</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Дата</th><th>Оценка</th><th>Отзыв</th><th></th></tr></thead><tbody>`;
        for(let t of data.tickets) {
            let act = '';
            if(t.status==='resolved' && !t.satisfaction) act = `<button class="bg-green-600 text-white px-2 py-1 rounded" onclick="openReview(${t.id})">Оценить</button>`;
            html += `<tr><td>${t.id}</td><td>${t.title}</td><td><span class="status-badge status-${t.status}">${t.status}</span></td><td>${t.priority}</td><td>${new Date(t.created_at).toLocaleDateString()}</td><td>${t.satisfaction?'⭐'+t.satisfaction:'—'}</td><td>${t.review?t.review.substring(0,50):'—'}</td><td>${act}</td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.openReview = async (id) => {
            let val = prompt("Оценка (1-5):",5);
            if(val && val>=1 && val<=5) {
                let rev = prompt("Ваш отзыв (необязательно):","");
                await api(`/api/tickets/${id}?satisfaction=${val}&review=${encodeURIComponent(rev||'')}`,'PUT');
                notyf.success('Спасибо за отзыв!');
                renderUI();
            }
        };
    }

    function renderNewTicket(container) {
        container.innerHTML = `<div class="card"><h3>Новая заявка</h3><form id="newForm"><div><label>Название</label><input id="title" required></div><div><label>Описание</label><textarea id="desc" rows="3"></textarea></div><div><label>Приоритет</label><select id="priority"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></div><button class="btn-primary" type="submit">Создать</button></form></div>`;
        document.getElementById('newForm').onsubmit = async (e) => {
            e.preventDefault();
            let body = new URLSearchParams({ title:document.getElementById('title').value, description:document.getElementById('desc').value, priority:document.getElementById('priority').value });
            await api('/api/tickets','POST',body);
            notyf.success('Заявка создана');
            renderUI();
        };
    }

    async function renderOperatorTickets(container) {
        let data = await api('/api/tickets');
        let html = `<div class="card"><table class="w-full"><thead><tr><th>ID</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Действия</th></tr></thead><tbody>`;
        for(let t of data.tickets) {
            let actions = '';
            if(t.status==='new') actions = `<button class="bg-yellow-500 text-white px-2 py-1 rounded" onclick="assign(${t.id})">Принять</button>`;
            if(t.status==='in_progress') actions = `<button class="bg-green-600 text-white px-2 py-1 rounded" onclick="resolve(${t.id})">Решить</button> <button class="bg-blue-600 text-white px-2 py-1 rounded" onclick="respond(${t.id})">Ответить</button>`;
            if(t.status==='resolved') actions = `<button class="bg-red-600 text-white px-2 py-1 rounded" onclick="closeTicket(${t.id})">Закрыть</button>`;
            html += `<tr><td>${t.id}</td><td>${t.title}</td><td><span class="status-badge status-${t.status}">${t.status}</span></td><td>${t.priority}</td><td>${actions}</td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.assign = async (id) => { await api(`/api/tickets/${id}?status=in_progress&assigned_to_id=${currentUser.id}`,'PUT'); renderUI(); };
        window.resolve = async (id) => { await api(`/api/tickets/${id}?status=resolved`,'PUT'); renderUI(); };
        window.closeTicket = async (id) => { await api(`/api/tickets/${id}?status=closed`,'PUT'); renderUI(); };
        window.respond = async (id) => { let msg = prompt("Введите ответ:"); if(msg) alert("Ответ отправлен (демо)"); };
    }

    function renderExport(container) {
        container.innerHTML = `<div class="card"><h3>Экспорт</h3><a href="/api/export/tickets" target="_blank"><button class="btn-primary">Скачать CSV</button></a></div>`;
    }

    async function renderAdminUsers(container) {
        let users = await api('/api/users');
        let html = `<div class="card"><h3>Пользователи</h3><table class="w-full"><thead><tr><th>ID</th><th>Email</th><th>Имя</th><th>Роль</th><th>Новая роль</th><th></th></tr></thead><tbody>`;
        for(let u of users) {
            html += `<tr><td>${u.id}</td><td>${u.email}</td><td>${u.full_name}</td><td>${u.role}</td><td><select id="role-${u.id}"><option>client</option><option>operator</option><option>admin</option><option>quality</option></select></td><td><button class="bg-blue-600 text-white px-2 py-1 rounded" onclick="changeRole(${u.id})">Изменить</button> <button class="bg-red-600 text-white px-2 py-1 rounded" onclick="delUser(${u.id})">Удалить</button></td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.changeRole = async (id) => { let newRole = document.getElementById(`role-${id}`).value; await api(`/api/users/${id}/role?new_role=${newRole}`,'PUT'); renderAdminUsers(container); };
        window.delUser = async (id) => { if(confirm('Удалить?')) { await fetch(`/api/users/${id}`,{method:'DELETE'}); renderAdminUsers(container); } };
    }

    async function renderDashboard(container) {
        let m = await api('/api/dashboard/metrics');
        let statusLabels = Object.keys(m.status_counts);
        let statusData = Object.values(m.status_counts);
        let dailyLabels = m.daily_labels;
        let dailyData = m.daily_data;
        container.innerHTML = `<div class="card"><h3>Дашборд качества</h3>
        <div class="grid grid-cols-3 gap-4 mb-4">
            <div class="bg-gray-100 p-3 rounded"><b>Всего заявок</b><br><span class="text-2xl">${m.total_tickets}</span></div>
            <div class="bg-gray-100 p-3 rounded"><b>Решено/закрыто</b><br><span class="text-2xl">${m.resolved_tickets}</span></div>
            <div class="bg-gray-100 p-3 rounded"><b>Средний CSAT</b><br><span class="text-2xl">${m.avg_csat}/5</span></div>
        </div>
        <div class="grid grid-cols-2 gap-4">
            <div><canvas id="statusChart"></canvas></div>
            <div><canvas id="trendChart"></canvas></div>
        </div></div>`;
        setTimeout(() => {
            new Chart(document.getElementById('statusChart'), { type:'pie', data:{ labels:statusLabels, datasets:[{ data:statusData, backgroundColor:['#facc15','#3b82f6','#22c55e','#64748b'] }] } });
            new Chart(document.getElementById('trendChart'), { type:'line', data:{ labels:dailyLabels, datasets:[{ label:'Заявки', data:dailyData, borderColor:'#15803d' }] } });
        },100);
    }

    loadUser();
</script>
</body>
</html>
    """)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🚀 Сервер запущен на порту {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
