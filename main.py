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
    # Предустановленные пользователи удалены – только пустые таблицы
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

@app.get("/")
def index():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Оптимасеть | Мониторинг качества услуг</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.css">
    <script src="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.js"></script>
    <style>
        * { transition: all 0.2s ease; }
        body { font-family: 'Segoe UI', 'Inter', system-ui, sans-serif; background: var(--bg); color: var(--text); }
        :root {
            --bg-light: #ffffff;
            --bg-dark: #0a192f;
            --text-light: #1e293b;
            --text-dark: #e6f1ff;
            --primary-light: #15803d;
            --primary-dark: #00b4d8;
            --border-light: #e2e8f0;
            --border-dark: #1e4a76;
        }
        body.light { --bg: var(--bg-light); --text: var(--text-light); --primary: var(--primary-light); --border: var(--border-light); }
        body.dark { --bg: var(--bg-dark); --text: var(--text-dark); --primary: var(--primary-dark); --border: var(--border-dark); }
        body { background: var(--bg); color: var(--text); }
        .card { background: var(--bg); border: 1px solid var(--border); border-radius: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: transform 0.2s, box-shadow 0.2s; }
        .card:hover { box-shadow: 0 8px 20px rgba(0,0,0,0.08); }
        .btn-primary { background: var(--primary); color: white; border-radius: 0.5rem; padding: 0.5rem 1rem; font-weight: 500; transition: all 0.2s; }
        .btn-primary:hover { filter: brightness(1.05); transform: translateY(-1px); }
        .status-badge { display: inline-block; padding: 0.2rem 0.7rem; border-radius: 2rem; font-size: 0.7rem; font-weight: 600; }
        .status-new { background: #facc1520; color: #facc15; }
        .status-in_progress { background: #3b82f620; color: #3b82f6; }
        .status-resolved { background: #22c55e20; color: #22c55e; }
        .status-closed { background: #64748b20; color: #94a3b8; }
        .tab-btn { padding: 0.5rem 1rem; border-radius: 2rem; transition: all 0.2s; font-weight: 500; }
        .tab-btn.active { background: var(--primary); color: white; }
        input, select, textarea { background: var(--bg); border: 1px solid var(--border); border-radius: 0.5rem; padding: 0.5rem; width: 100%; color: var(--text); }
        input:focus, select:focus, textarea:focus { outline: none; border-color: var(--primary); ring: 2px solid var(--primary); }
        .table-optim th { background: var(--bg); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; padding: 0.75rem; border-bottom: 1px solid var(--border); }
        .table-optim td { padding: 0.75rem; border-bottom: 1px solid var(--border); }
        .animate-pulse { animation: pulse 1.5s cubic-bezier(0.4,0,0.6,1) infinite; }
        @keyframes pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 0.8; } }
    </style>
</head>
<body class="light">
<div class="max-w-7xl mx-auto px-4 py-6">
    <div class="flex justify-between items-center mb-8 p-4 bg-white shadow-sm rounded-2xl border border-gray-100">
        <div class="flex items-center gap-3">
            <div class="text-3xl">🛜</div>
            <div>
                <h1 class="text-2xl font-bold text-gray-800">ОПТИМАСЕТЬ</h1>
                <p class="text-xs text-gray-500">Мониторинг качества услуг и технической поддержки</p>
            </div>
        </div>
        <div class="flex items-center gap-4">
            <button id="themeToggle" class="text-2xl opacity-80 hover:opacity-100 transition">🌙</button>
            <div id="userPanel" class="flex items-center gap-3 bg-gray-50 px-3 py-1 rounded-full border border-gray-200"></div>
        </div>
    </div>
    <div id="app"></div>
</div>
<script>
    let currentUser = null;
    let currentTheme = localStorage.getItem('theme') || 'light';
    const notyf = new Notyf({ duration: 3000, position: { x: 'right', y: 'top' } });

    function applyTheme() {
        if (currentTheme === 'dark') {
            document.body.classList.remove('light');
            document.body.classList.add('dark');
            document.getElementById('themeToggle').innerText = '☀️';
        } else {
            document.body.classList.remove('dark');
            document.body.classList.add('light');
            document.getElementById('themeToggle').innerText = '🌙';
        }
        localStorage.setItem('theme', currentTheme);
    }
    applyTheme();
    document.getElementById('themeToggle').addEventListener('click', () => { currentTheme = currentTheme === 'dark' ? 'light' : 'dark'; applyTheme(); });

    async function api(url, method='GET', body=null) {
        const opts = { method };
        if (body) {
            opts.body = body;
            opts.headers = {'Content-Type':'application/x-www-form-urlencoded'};
        }
        const res = await fetch(url, opts);
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    }

    async function login(email, password) {
        const form = new URLSearchParams({email,password});
        await api('/api/login','POST',form);
        await loadUser();
    }

    async function loadUser() {
        try {
            currentUser = await api('/api/me');
            document.getElementById('userPanel').innerHTML = `<span class="font-medium text-gray-700">${currentUser.name}</span> <span class="text-xs bg-gray-200 text-gray-700 px-2 py-0.5 rounded-full">${currentUser.role}</span> <button class="bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1 rounded-full text-sm transition" onclick="logout()">Выйти</button>`;
            renderUI();
        } catch(e) { currentUser = null; renderLogin(); }
    }

    async function logout() { await api('/api/logout','POST'); currentUser = null; renderLogin(); }

    function showSkeleton() {
        document.getElementById('app').innerHTML = `<div class="space-y-4"><div class="h-32 bg-gray-100 rounded-2xl animate-pulse"></div><div class="h-32 bg-gray-100 rounded-2xl animate-pulse"></div></div>`;
    }

    function renderLogin() {
        document.getElementById('app').innerHTML = `
            <div class="max-w-md mx-auto card p-8">
                <h2 class="text-2xl font-bold mb-4 text-gray-800">Вход</h2>
                <form id="loginForm" class="space-y-4">
                    <div><label class="block text-sm text-gray-600">Email</label><input id="email" type="email" class="w-full"></div>
                    <div><label class="block text-sm text-gray-600">Пароль</label><input id="password" type="password" class="w-full"></div>
                    <button type="submit" class="w-full btn-primary py-2 transition">Войти</button>
                </form>
                <hr class="my-6 border-gray-200">
                <h3 class="text-xl font-semibold mb-4 text-gray-800">Регистрация</h3>
                <form id="registerForm" class="space-y-4">
                    <div><label class="block text-sm text-gray-600">Email</label><input id="regEmail" type="email" class="w-full"></div>
                    <div><label class="block text-sm text-gray-600">ФИО</label><input id="regName" class="w-full"></div>
                    <div><label class="block text-sm text-gray-600">Пароль</label><input id="regPassword" type="password" class="w-full"></div>
                    <div><label class="block text-sm text-gray-600">Роль</label><select id="regRole" class="w-full"><option>client</option><option>operator</option><option>admin</option><option>quality</option></select></div>
                    <button type="submit" class="w-full btn-primary py-2 transition">Зарегистрироваться</button>
                </form>
            </div>
        `;
        document.getElementById('loginForm').onsubmit = async (e) => { e.preventDefault(); try { await login(e.target.email.value, e.target.password.value); notyf.success('Вход выполнен'); } catch { notyf.error('Ошибка входа'); } };
        document.getElementById('registerForm').onsubmit = async (e) => { e.preventDefault(); const form = new URLSearchParams({ email:e.target.regEmail.value, full_name:e.target.regName.value, password:e.target.regPassword.value, role:e.target.regRole.value }); await fetch('/api/register',{method:'POST',body:form}); notyf.success('Регистрация успешна'); };
    }

    async function renderUI() {
        if (!currentUser) return;
        showSkeleton();
        let tabs = [];
        if (currentUser.role === 'client') tabs = ['Мои заявки', 'Новая заявка'];
        else if (currentUser.role === 'operator') tabs = ['Все заявки', 'Экспорт'];
        else if (currentUser.role === 'admin') tabs = ['Пользователи', 'Дашборд', 'Экспорт'];
        else if (currentUser.role === 'quality') tabs = ['Дашборд', 'Экспорт'];
        let html = `<div class="flex gap-2 mb-6 border-b pb-2">${tabs.map((t,i)=>`<button class="tab-btn px-4 py-2 rounded-full ${i===0?'active bg-gray-900 text-white':''}" data-tab="${i}">${t}</button>`).join('')}</div><div id="panes"></div>`;
        document.getElementById('app').innerHTML = html;
        const panes = document.getElementById('panes');
        for (let i=0;i<tabs.length;i++) {
            const pane = document.createElement('div'); pane.className = `tab-pane ${i===0?'block':'hidden'}`; pane.id = `pane-${i}`;
            panes.appendChild(pane);
            if (currentUser.role === 'client') {
                if (tabs[i]==='Мои заявки') await renderClientTickets(pane);
                if (tabs[i]==='Новая заявка') renderNewTicket(pane);
            } else if (currentUser.role === 'operator') {
                if (tabs[i]==='Все заявки') await renderOperatorTickets(pane);
                if (tabs[i]==='Экспорт') renderExport(pane);
            } else if (currentUser.role === 'admin') {
                if (tabs[i]==='Пользователи') await renderAdminUsers(pane);
                if (tabs[i]==='Дашборд') await renderDashboard(pane);
                if (tabs[i]==='Экспорт') renderExport(pane);
            } else if (currentUser.role === 'quality') {
                if (tabs[i]==='Дашборд') await renderDashboard(pane);
                if (tabs[i]==='Экспорт') renderExport(pane);
            }
        }
        document.querySelectorAll('.tab-btn').forEach((btn,idx)=>btn.onclick=()=>{
            document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active','bg-gray-900','text-white'));
            btn.classList.add('active','bg-gray-900','text-white');
            document.querySelectorAll('.tab-pane').forEach(p=>p.classList.add('hidden'));
            document.getElementById(`pane-${idx}`).classList.remove('hidden');
        });
    }

    async function renderClientTickets(container) {
        let data = await api('/api/tickets');
        let html = `<div class="card overflow-hidden"><table class="w-full table-optim"><thead><tr><th class="p-3 text-left">ID</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Дата</th><th>Оценка</th><th>Отзыв</th><th>Действие</th></tr></thead><tbody>`;
        for (let t of data.tickets) {
            let reviewHtml = t.review ? `<div class="max-w-xs text-xs text-gray-500">${t.review.substring(0,50)}${t.review.length>50?'…':''}</div>` : '—';
            let actionBtn = '';
            if (t.status === 'resolved' && !t.satisfaction) {
                actionBtn = `<button class="bg-green-50 text-green-600 hover:bg-green-100 px-3 py-1 rounded-full text-sm" onclick="openReviewModal(${t.id})">Оценить</button>`;
            }
            html += `<tr class="border-b"><td class="p-3">${t.id}</td><td class="p-3">${t.title}</td><td class="p-3"><span class="status-badge status-${t.status}">${t.status}</span></td><td class="p-3">${t.priority}</td><td class="p-3">${new Date(t.created_at).toLocaleDateString()}</td><td class="p-3">${t.satisfaction ? '⭐'+t.satisfaction : '—'}</td><td class="p-3">${reviewHtml}</td><td class="p-3">${actionBtn}</td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;

        window.openReviewModal = (id) => {
            const modalHtml = `
                <div id="reviewModal" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
                    <div class="bg-white rounded-2xl p-6 w-full max-w-md">
                        <h3 class="text-xl font-bold mb-4">Оцените качество обслуживания</h3>
                        <div class="flex justify-center gap-2 mb-4" id="starRating">${[1,2,3,4,5].map(v => `<span class="star text-3xl cursor-pointer text-gray-400 hover:text-yellow-400 transition" data-value="${v}">★</span>`).join('')}</div>
                        <textarea id="reviewText" rows="3" class="w-full border border-gray-300 rounded-lg p-2 mb-4" placeholder="Ваш отзыв (необязательно)"></textarea>
                        <div class="flex justify-end gap-2">
                            <button class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg" onclick="closeModal()">Отмена</button>
                            <button class="bg-green-600 text-white px-4 py-2 rounded-lg" onclick="submitReview(${id})">Отправить</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            document.querySelectorAll('.star').forEach(star => {
                star.addEventListener('click', () => {
                    document.querySelectorAll('.star').forEach(s => s.classList.remove('text-yellow-400', 'text-gray-400'));
                    star.classList.add('text-yellow-400');
                    star.style.color = '#facc15';
                    window.selectedRating = parseInt(star.dataset.value);
                });
            });
        };
        window.closeModal = () => { document.getElementById('reviewModal')?.remove(); };
        window.submitReview = async (id) => {
            const rating = window.selectedRating;
            const review = document.getElementById('reviewText')?.value || '';
            if (!rating) { notyf.error('Выберите оценку'); return; }
            await api(`/api/tickets/${id}?satisfaction=${rating}&review=${encodeURIComponent(review)}`, 'PUT');
            notyf.success('Спасибо за отзыв!');
            closeModal();
            renderUI();
        };
    }

    function renderNewTicket(container) {
        container.innerHTML = `
            <div class="card p-6">
                <h3 class="text-xl font-semibold mb-4 text-gray-800">➕ Новая заявка</h3>
                <form id="newTicketForm" class="space-y-4">
                    <div><label class="block text-sm text-gray-600 mb-1">Название</label><input type="text" id="newTitle" class="w-full" required></div>
                    <div><label class="block text-sm text-gray-600 mb-1">Описание</label><textarea id="newDesc" rows="3" class="w-full"></textarea></div>
                    <div><label class="block text-sm text-gray-600 mb-1">Приоритет</label><select id="newPriority" class="w-full"><option value="low">Низкий</option><option value="medium" selected>Средний</option><option value="high">Высокий</option><option value="critical">Критический</option></select></div>
                    <button type="submit" class="btn-primary px-4 py-2 rounded-lg transition">Создать заявку</button>
                </form>
            </div>
        `;
        const form = document.getElementById('newTicketForm');
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const title = document.getElementById('newTitle').value.trim();
            const description = document.getElementById('newDesc').value.trim();
            const priority = document.getElementById('newPriority').value;
            if (!title) { notyf.error('Введите название заявки'); return; }
            try {
                const body = new URLSearchParams({ title, description, priority });
                const resp = await fetch('/api/tickets', { method: 'POST', body });
                if (resp.ok) {
                    notyf.success('Заявка создана');
                    document.getElementById('newTitle').value = '';
                    document.getElementById('newDesc').value = '';
                    const myTicketsBtn = document.querySelector('.tab-btn[data-tab="0"]');
                    if (myTicketsBtn) myTicketsBtn.click();
                    else renderUI();
                } else { notyf.error('Ошибка создания'); }
            } catch(err) { notyf.error('Ошибка соединения'); }
        });
    }

    async function renderOperatorTickets(container) {
        let data = await api('/api/tickets');
        let html = `<div class="card overflow-hidden"><table class="w-full table-optim"><thead><tr><th class="p-3">ID</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Действия</th></tr></thead><tbody>`;
        for (let t of data.tickets) {
            html += `<tr class="border-b"><td class="p-3">${t.id}</td><td class="p-3">${t.title}</td><td class="p-3"><span class="status-badge status-${t.status}">${t.status}</span></td><td class="p-3">${t.priority}</td><td class="p-3 space-x-2">
                ${t.status==='new'?`<button class="bg-yellow-50 text-yellow-600 hover:bg-yellow-100 px-3 py-1 rounded-full text-sm" onclick="assignTicket(${t.id})">Принять</button>`:''}
                ${t.status==='in_progress'?`<button class="bg-green-50 text-green-600 hover:bg-green-100 px-3 py-1 rounded-full text-sm" onclick="openResolveModal(${t.id})">Решить</button>`:''}
                ${t.status==='resolved'?`<button class="bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1 rounded-full text-sm" onclick="closeTicket(${t.id})">Закрыть</button>`:''}
            </td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;

        window.assignTicket = async (id) => { await api(`/api/tickets/${id}?status=in_progress&assigned_to_id=${currentUser.id}`,'PUT'); notyf.success('Заявка принята'); renderUI(); };
        window.closeTicket = async (id) => { await api(`/api/tickets/${id}?status=closed`,'PUT'); notyf.success('Заявка закрыта'); renderUI(); };
        window.openResolveModal = (id) => {
            const modalHtml = `
                <div id="resolveModal" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
                    <div class="bg-white rounded-2xl p-6 w-full max-w-md">
                        <h3 class="text-xl font-bold mb-4">Решение заявки №${id}</h3>
                        <textarea id="resolveComment" rows="4" class="w-full border border-gray-300 rounded-lg p-2 mb-4" placeholder="Опишите выполненное решение..."></textarea>
                        <div class="flex justify-end gap-2">
                            <button class="bg-gray-200 text-gray-700 px-4 py-2 rounded-lg" onclick="closeResolveModal()">Отмена</button>
                            <button class="bg-green-600 text-white px-4 py-2 rounded-lg" onclick="resolveWithComment(${id})">Подтвердить решение</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
        };
        window.closeResolveModal = () => { document.getElementById('resolveModal')?.remove(); };
        window.resolveWithComment = async (id) => {
            const comment = document.getElementById('resolveComment')?.value || '';
            await api(`/api/tickets/${id}?status=resolved&review=${encodeURIComponent(comment)}`, 'PUT');
            notyf.success('Заявка решена, комментарий сохранён');
            closeResolveModal();
            renderUI();
        };
    }

    function renderExport(container) {
        container.innerHTML = `<div class="card p-6"><h3 class="text-xl font-semibold mb-4 text-gray-800">📎 Экспорт данных</h3><a href="/api/export/tickets" target="_blank"><button class="btn-primary px-4 py-2 rounded-lg transition">Скачать заявки CSV</button></a></div>`;
    }

    async function renderAdminUsers(container) {
        let users = await api('/api/users');
        let html = `<div class="card overflow-hidden"><h3 class="text-xl font-semibold p-4 text-gray-800">👥 Управление пользователями</h3><table class="w-full table-optim"><thead><tr><th class="p-3">ID</th><th>Email</th><th>ФИО</th><th>Роль</th><th>Новая роль</th><th>Действия</th></tr></thead><tbody>`;
        for (let u of users) {
            html += `<tr class="border-b"><td class="p-3">${u.id}</td><td class="p-3">${u.email}</td><td class="p-3">${u.full_name}</td><td class="p-3">${u.role}</td><td class="p-3"><select id="role-${u.id}" class="bg-gray-50 border border-gray-300 rounded p-1 text-sm"><option>client</option><option>operator</option><option>admin</option><option>quality</option></select></td>
            <td class="p-3 space-x-2"><button class="bg-blue-50 text-blue-600 hover:bg-blue-100 px-3 py-1 rounded-full text-sm" onclick="changeRole(${u.id})">Изменить</button>
            <button class="bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1 rounded-full text-sm" onclick="deleteUser(${u.id})">Удалить</button></td></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.changeRole = async (id) => { let newRole = document.getElementById(`role-${id}`).value; await api(`/api/users/${id}/role?new_role=${newRole}`,'PUT'); notyf.success('Роль изменена'); renderAdminUsers(container); };
        window.deleteUser = async (id) => {
            if (!confirm('Вы уверены, что хотите удалить этого пользователя?')) return;
            try {
                await fetch(`/api/users/${id}`, { method: 'DELETE' });
                notyf.success('Пользователь удалён');
                renderAdminUsers(container);
                if (id === currentUser.id) await logout();
            } catch(e) { notyf.error('Ошибка удаления'); }
        };
    }

    async function renderDashboard(container) {
        let m = await api('/api/dashboard/metrics');
        const statusLabels = Object.keys(m.status_counts);
        const statusData = Object.values(m.status_counts);
        const dailyLabels = m.daily_labels;
        const dailyData = m.daily_data;
        container.innerHTML = `<div class="card p-6"><h3 class="text-xl font-semibold mb-4 text-gray-800">📊 Дашборд качества</h3>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
            <div class="bg-gray-50 p-4 rounded-xl"><div class="text-sm text-gray-500">Всего заявок</div><div class="text-3xl font-bold text-gray-800">${m.total_tickets}</div></div>
            <div class="bg-gray-50 p-4 rounded-xl"><div class="text-sm text-gray-500">Решено/закрыто</div><div class="text-3xl font-bold text-gray-800">${m.resolved_tickets}</div></div>
            <div class="bg-gray-50 p-4 rounded-xl"><div class="text-sm text-gray-500">Средний CSAT</div><div class="text-3xl font-bold text-gray-800">${m.avg_csat}/5</div></div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div class="bg-gray-50 p-4 rounded-xl"><canvas id="statusChart" height="200"></canvas></div>
            <div class="bg-gray-50 p-4 rounded-xl"><canvas id="trendChart" height="200"></canvas></div>
        </div></div>`;
        setTimeout(() => {
            const statusCtx = document.getElementById('statusChart').getContext('2d');
            new Chart(statusCtx, { type: 'pie', data: { labels: statusLabels, datasets: [{ data: statusData, backgroundColor: ['#facc15', '#3b82f6', '#22c55e', '#64748b'] }] }, options: { responsive: true, maintainAspectRatio: true } });
            const trendCtx = document.getElementById('trendChart').getContext('2d');
            new Chart(trendCtx, { type: 'line', data: { labels: dailyLabels, datasets: [{ label: 'Заявки', data: dailyData, borderColor: '#15803d', fill: false }] }, options: { responsive: true } });
        }, 100);
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