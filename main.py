import uvicorn
from fastapi import FastAPI, HTTPException, Form, Cookie, Response, Request
from fastapi.responses import HTMLResponse, StreamingResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from datetime import datetime, timedelta
import sqlite3
import secrets
import csv
import io
import os
import sys
import traceback

app = FastAPI(title="Quality Monitor Pro")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=500)

# ------------------------------------------------------------
# Путь к базе данных (для Render используем /tmp)
# ------------------------------------------------------------
if os.environ.get("RENDER"):
    DB_PATH = os.path.join("/tmp", "monitoring.db")
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "monitoring.db")

print(f"📁 База данных: {DB_PATH}", file=sys.stderr)

# ------------------------------------------------------------
# Диагностический middleware (покажет ошибку в браузере)
# ------------------------------------------------------------
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        error_text = f"Ошибка: {type(e).__name__}: {str(e)}\n\n{traceback.format_exc()}"
        print(error_text, file=sys.stderr)
        return PlainTextResponse(error_text, status_code=500)

# ------------------------------------------------------------
# Инициализация базы данных
# ------------------------------------------------------------
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
    except Exception as e:
        print(f"❌ Ошибка подключения к {DB_PATH}: {e}", file=sys.stderr)
        conn = sqlite3.connect(":memory:")
        c = conn.cursor()

    try:
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
            created_at TEXT,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
        c.execute("INSERT OR IGNORE INTO users (email, full_name, hashed_password, role, created_at) VALUES ('client@example.com', 'Клиент', 'client', 'client', datetime('now'))")

        c.execute("SELECT id FROM users WHERE email='operator@mail.ru'")
        op_row = c.fetchone()
        operator_id = op_row[0] if op_row else 2

        c.execute("SELECT COUNT(*) FROM tickets")
        if c.fetchone()[0] == 0:
            now = datetime.now()
            test_tickets = [
                ("Не работает Wi-Fi в офисе", "С утра пропал Wi-Fi на всех устройствах. Роутер перезагружали – не помогло.", "resolved", "high", "Технические проблемы", (now - timedelta(days=1)).isoformat(), operator_id, "Проверили оборудование, проблема в настройках DNS. Восстановили доступ.", 4),
                ("Не грузит CRM-система", "При входе в CRM вылетает ошибка 500. Работа встала.", "resolved", "critical", "Технические проблемы", (now - timedelta(days=2)).isoformat(), operator_id, "Обнаружен сбой на сервере БД. Перезапустили службы. Ошибка устранена.", 5),
                ("Тормозит видеоконференция", "При звонках в Zoom постоянные задержки и разрывы.", "resolved", "medium", "Технические проблемы", (now - timedelta(days=3)).isoformat(), operator_id, "Проблема в настройках QoS вашего роутера. Оптимизировали трафик.", 4),
                ("Не отправляется почта через Outlook", "Исходящие письма зависают в очереди.", "resolved", "high", "Технические проблемы", (now - timedelta(days=4)).isoformat(), operator_id, "Обновили настройки SMTP-сервера. Всё должно работать.", 5),
                ("Не синхронизируется OneDrive", "Папка на рабочем столе не синхронизируется с облаком.", "resolved", "low", "Технические проблемы", (now - timedelta(days=5)).isoformat(), operator_id, "Сбросили кэш OneDrive. Рекомендуем обновить приложение.", 3),
                ("Как настроить автоответ в Outlook?", "Нужна инструкция по настройке автоответчика.", "resolved", "low", "Консультации", (now - timedelta(days=6)).isoformat(), operator_id, "Инструкция: Файл → Автоответчик → Включить. Настройте текст.", 5),
                ("Какие тарифы интернета для дома?", "Хочу подключить домашний интернет. Нужна консультация.", "resolved", "low", "Консультации", (now - timedelta(days=7)).isoformat(), operator_id, "Тарифы: 'Старт' 100 Мбит/с – 500 руб., 'Оптима' 300 Мбит/с – 700 руб.", 4),
                ("Как восстановить пароль от личного кабинета?", "Не приходит письмо для сброса.", "resolved", "medium", "Консультации", (now - timedelta(days=8)).isoformat(), operator_id, "Отправили одноразовую ссылку на резервный email.", 5),
                ("Выбор оборудования для офиса", "Нужен роутер и коммутаторы на 20 пользователей.", "resolved", "medium", "Консультации", (now - timedelta(days=9)).isoformat(), operator_id, "Рекомендуем MikroTik hAP ac2 + 2 коммутатора TP-Link.", 4),
                ("Обучение работе в CRM", "Нужна консультация по основным функциям.", "resolved", "low", "Консультации", (now - timedelta(days=10)).isoformat(), operator_id, "Запишитесь на вебинар в четверг в 11:00. Видеоуроки в базе знаний.", 5),
                ("Нет доступа к общей папке", "После смены пароля потерял доступ к \\\\server\\docs", "resolved", "high", "Доступ и права", (now - timedelta(days=11)).isoformat(), operator_id, "Ваша учётная запись повторно добавлена в группу доступа. Перезагрузите компьютер.", 5),
                ("Не могу установить программу", "При установке требует прав администратора.", "resolved", "medium", "Доступ и права", (now - timedelta(days=12)).isoformat(), operator_id, "Создали заявку на удалённую установку. Программа будет установлена в течение часа.", 4),
                ("Доступ к БД клиентов", "Менеджеру нужен доступ к таблице clients.", "resolved", "high", "Доступ и права", (now - timedelta(days=13)).isoformat(), operator_id, "Учётная запись с правами SELECT создана. Данные отправлены в личное сообщение.", 5),
                ("Не работает VPN после обновления", "Ошибка аутентификации при подключении.", "resolved", "critical", "Доступ и права", (now - timedelta(days=14)).isoformat(), operator_id, "Перевыпустили сертификат. Приложили новый файл. Установите его.", 5),
                ("Добавить сотрудника в группу Бухгалтерия", "Нужен доступ к 1С и общим папкам.", "resolved", "medium", "Доступ и права", (now - timedelta(days=15)).isoformat(), operator_id, "Создали учётную запись, добавили в группу. Логин отправлен руководителю.", 5),
            ]
            for t in test_tickets:
                c.execute("""INSERT INTO tickets 
                    (title, description, status, priority, category, created_at, assigned_to_id, review, satisfaction)
                    VALUES (?,?,?,?,?,?,?,?,?)""", t)
                ticket_id = c.lastrowid
                overall = t[8]
                if overall == 5:
                    speed = prof = politeness = 5
                elif overall == 4:
                    speed = prof = politeness = 4
                else:
                    speed = prof = politeness = 3
                c.execute("""INSERT INTO detailed_reviews 
                    (ticket_id, overall_rating, speed_rating, professionalism_rating, politeness_rating, comment, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (ticket_id, overall, speed, prof, politeness, t[7], (now - timedelta(days=1)).isoformat()))
                resolved_at = (now - timedelta(days=1)).isoformat()
                created_at = t[5]
                created_dt = datetime.fromisoformat(created_at)
                resolved_dt = datetime.fromisoformat(resolved_at)
                resolution_minutes = int((resolved_dt - created_dt).total_seconds() / 60)
                c.execute("UPDATE tickets SET resolved_at=?, resolution_time_minutes=? WHERE id=?", (resolved_at, resolution_minutes, ticket_id))
            c.execute("""INSERT INTO tickets (title, description, status, priority, category, created_at, created_by_id) 
                        VALUES ('Сайт не загружается', 'Ошибка 404 при открытии сайта', 'new', 'high', 'Технические проблемы', ?, 2)""", (now.isoformat(),))
            c.execute("""INSERT INTO tickets (title, description, status, priority, category, created_at, assigned_to_id) 
                        VALUES ('Проблема с биллингом', 'Двойное списание за услуги', 'in_progress', 'critical', 'Доступ и права', ?, 2)""", (now.isoformat(),))
        conn.commit()
        print("✅ База данных инициализирована успешно", file=sys.stderr)
    except Exception as e:
        print(f"❌ Ошибка при инициализации БД: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        raise
    finally:
        conn.close()

init_db()
sessions = {}

# ------------------------------------------------------------
# API эндпоинты (полный набор)
# ------------------------------------------------------------
@app.post("/api/register")
def register(email: str = Form(...), full_name: str = Form(...), password: str = Form(...), privacy_accepted: bool = Form(...)):
    if not privacy_accepted:
        raise HTTPException(400, "Необходимо согласие на обработку персональных данных")
    if email == "admin@mail.ru":
        role = "admin"
    elif email == "operator@mail.ru":
        role = "operator"
    elif email == "quality@mail.ru":
        role = "quality"
    else:
        role = "client"
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if user["role"] == "client":
        c.execute("SELECT id, title, description, status, priority, category, created_at, satisfaction, review FROM tickets WHERE created_by_id=?", (user["id"],))
    elif user["role"] == "operator":
        c.execute("SELECT id, title, description, status, priority, category, created_at, satisfaction, review FROM tickets WHERE assigned_to_id=? OR assigned_to_id IS NULL", (user["id"],))
    else:
        c.execute("SELECT id, title, description, status, priority, category, created_at, satisfaction, review FROM tickets")
    tickets = [{"id": row[0], "title": row[1], "description": row[2], "status": row[3], "priority": row[4],
                "category": row[5], "created_at": row[6], "satisfaction": row[7], "review": row[8]} for row in c.fetchall()]
    conn.close()
    return {"tickets": tickets}

@app.post("/api/tickets")
def create_ticket(title: str = Form(...), description: str = Form(...), priority: str = Form(...), category: str = Form(""), privacy_accepted: bool = Form(...), session: str = Cookie(None)):
    if not privacy_accepted:
        raise HTTPException(400, "Необходимо согласие на обработку персональных данных")
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updates = []
    params = []

    c.execute("SELECT status, first_response_at FROM tickets WHERE id=?", (ticket_id,))
    current = c.fetchone()
    if not current:
        conn.close()
        raise HTTPException(404, "Ticket not found")
    current_status, first_resp = current

    if status:
        updates.append("status=?")
        params.append(status)
        if status == "in_progress":
            if not first_resp:
                updates.append("first_response_at=?")
                params.append(datetime.now().isoformat())
            if assigned_to_id is None:
                assigned_to_id = user["id"]
        if status == "resolved":
            resolved_at = datetime.now().isoformat()
            updates.append("resolved_at=?")
            params.append(resolved_at)
            c.execute("SELECT created_at FROM tickets WHERE id=?", (ticket_id,))
            row = c.fetchone()
            if row and row[0]:
                created = datetime.fromisoformat(row[0])
                resolved = datetime.fromisoformat(resolved_at)
                resolution_minutes = int((resolved - created).total_seconds() / 60)
                updates.append("resolution_time_minutes=?")
                params.append(resolution_minutes)
        if status == "closed":
            updates.append("closed_at=?")
            params.append(datetime.now().isoformat())

    if assigned_to_id is not None:
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

@app.post("/api/tickets/{ticket_id}/comments")
def add_comment(ticket_id: int, comment: str = Form(...), session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT created_by_id, assigned_to_id FROM tickets WHERE id=?", (ticket_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Ticket not found")
    created_by, assigned_to = row
    if user["role"] not in ["admin", "quality"] and user["id"] not in (created_by, assigned_to):
        conn.close()
        raise HTTPException(403, "Access denied")
    c.execute("INSERT INTO comments (ticket_id, user_id, comment, created_at) VALUES (?,?,?,?)",
              (ticket_id, user["id"], comment, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"message": "OK"}

@app.get("/api/tickets/{ticket_id}/comments")
def get_comments(ticket_id: int, session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    user = sessions[session]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT created_by_id, assigned_to_id FROM tickets WHERE id=?", (ticket_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    created_by, assigned_to = row
    if user["role"] not in ["admin", "quality"] and user["id"] not in (created_by, assigned_to):
        conn.close()
        raise HTTPException(403)
    c.execute("""SELECT c.id, c.user_id, u.full_name, c.comment, c.created_at 
                 FROM comments c 
                 JOIN users u ON c.user_id = u.id 
                 WHERE c.ticket_id=? 
                 ORDER BY c.created_at ASC""", (ticket_id,))
    comments = [{"id": row[0], "user_id": row[1], "user_name": row[2], "comment": row[3], "created_at": row[4]} for row in c.fetchall()]
    conn.close()
    return comments

@app.post("/api/tickets/{ticket_id}/detailed_review")
def add_detailed_review(ticket_id: int, overall: int = Form(...), speed: int = Form(...),
                        professionalism: int = Form(...), politeness: int = Form(...),
                        comment: str = Form(""), session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO detailed_reviews (ticket_id, overall_rating, speed_rating, professionalism_rating, politeness_rating, comment, created_at) VALUES (?,?,?,?,?,?,?)",
              (ticket_id, overall, speed, professionalism, politeness, comment, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"message": "OK"}

@app.get("/api/dashboard/advanced_metrics")
def advanced_metrics(period: str = "month", operator_id: int = None, category: str = None, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] not in ["admin", "quality"]:
        raise HTTPException(403)
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    c.execute("SELECT AVG(resolution_time_minutes) FROM tickets WHERE resolution_time_minutes IS NOT NULL AND resolution_time_minutes > 0")
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
    if not session or session not in sessions or sessions[session]["role"] not in ["admin", "quality"]:
        raise HTTPException(403)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, email, full_name, role FROM users")
    users = [{"id": row[0], "email": row[1], "full_name": row[2], "role": row[3]} for row in c.fetchall()]
    conn.close()
    return users

@app.put("/api/users/{user_id}/role")
def change_role(user_id: int, new_role: str, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT event_time, user_id, action, details FROM system_logs ORDER BY event_time DESC LIMIT ?", (limit,))
    logs = [{"time": row[0], "user_id": row[1], "action": row[2], "details": row[3]} for row in c.fetchall()]
    conn.close()
    return logs

@app.get("/api/admin/sla")
def admin_get_sla(session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT param_key, param_value FROM sla_settings")
    rows = dict(c.fetchall())
    conn.close()
    return {"response_high_hours": rows.get("response_high_hours", 2), "response_medium_hours": rows.get("response_medium_hours", 8)}

@app.put("/api/admin/sla")
def admin_update_sla(response_high_hours: int, response_medium_hours: int, session: str = Cookie(None)):
    if not session or session not in sessions or sessions[session]["role"] != "admin":
        raise HTTPException(403)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE sla_settings SET param_value=? WHERE param_key=?", (response_high_hours, "response_high_hours"))
    c.execute("UPDATE sla_settings SET param_value=? WHERE param_key=?", (response_medium_hours, "response_medium_hours"))
    conn.commit()
    conn.close()
    return {"message": "OK"}

@app.get("/api/knowledge")
def get_knowledge(search: str = ""):
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name FROM categories")
    cats = [{"id": row[0], "name": row[1]} for row in c.fetchall()]
    conn.close()
    return cats

@app.get("/api/export/tickets")
def export_tickets(session: str = Cookie(None)):
    if not session or session not in sessions:
        raise HTTPException(401)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, description, status, priority, created_at, satisfaction, review FROM tickets")
    rows = c.fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Title", "Description", "Status", "Priority", "Created", "Satisfaction", "Operator Response"])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6] if r[6] else "", r[7] or ""])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tickets.csv"})

@app.get("/privacy")
def privacy_policy():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Политика обработки персональных данных</title>
    <style>
        body { font-family: system-ui; max-width: 800px; margin: 2rem auto; padding: 1rem; line-height: 1.5; }
        h1 { color: #15803d; }
    </style>
</head>
<body>
    <h1>Политика обработки персональных данных</h1>
    <p>Настоящая политика составлена в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных».</p>
    <h2>1. Какие данные собираются</h2>
    <p>При регистрации и создании заявок мы собираем: имя, email, текст обращения, приоритет, категорию, оценки и отзывы.</p>
    <h2>2. Цели обработки</h2>
    <p>Обработка данных осуществляется для предоставления услуг технической поддержки, улучшения качества обслуживания и формирования аналитики.</p>
    <h2>3. Сроки хранения</h2>
    <p>Данные хранятся в течение всего срока использования системы. По запросу пользователя данные могут быть удалены администратором.</p>
    <h2>4. Права пользователя</h2>
    <p>Вы можете запросить удаление своих данных, обратившись к администратору.</p>
    <h2>5. Контакты</h2>
    <p>Email: support@optimaset.ru</p>
    <p><a href="/">Вернуться на сайт</a></p>
</body>
</html>
    """)

@app.get("/")
def index():
    return HTMLResponse("""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>Quality Monitor Pro</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.css">
    <script src="https://cdn.jsdelivr.net/npm/notyf@3/notyf.min.js"></script>
    <style>
        * { transition: all 0.2s ease; }
        body.light { background: #ffffff; color: #1e293b; }
        body.dark { background: #0a192f; color: #e6f1ff; }
        .card { background: var(--bg); border: 1px solid #e2e8f0; border-radius: 1rem; padding: 1.5rem; margin-bottom: 1.5rem; transition: transform 0.2s, box-shadow 0.2s; }
        .card:hover { transform: translateY(-2px); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1); }
        body.light .card { background: #ffffff; border-color: #e2e8f0; }
        body.dark .card { background: #1e293b; border-color: #334155; }
        .btn-primary { background: #15803d; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; cursor: pointer; border: none; transition: all 0.2s; }
        .btn-primary:hover { background: #166534; transform: scale(1.02); box-shadow: 0 4px 12px rgba(21,128,61,0.4); }
        .tab-btn { padding: 0.5rem 1rem; border-radius: 2rem; cursor: pointer; transition: all 0.2s; }
        .tab-btn:hover { background: #15803d20; transform: translateY(-1px); }
        .tab-btn.active { background: #15803d; color: white; box-shadow: 0 2px 8px rgba(21,128,61,0.3); }
        .status-badge { display: inline-block; padding: 0.2rem 0.7rem; border-radius: 2rem; font-size: 0.7rem; font-weight: 600; }
        .status-new { background: #facc1520; color: #facc15; }
        .status-in_progress { background: #3b82f620; color: #3b82f6; }
        .status-resolved { background: #22c55e20; color: #22c55e; }
        .status-closed { background: #64748b20; color: #94a3b8; }
        input, select, textarea { border-radius: 0.5rem; padding: 0.5rem; width: 100%; outline: none; transition: border 0.2s, box-shadow 0.2s; }
        body.light input, body.light select, body.light textarea { background: #f8fafc; border: 1px solid #cbd5e1; color: #1e293b; }
        body.dark input, body.dark select, body.dark textarea { background: #0f172a; border: 1px solid #334155; color: #e2e8f0; }
        input:focus, select:focus, textarea:focus { border-color: #15803d; box-shadow: 0 0 0 2px #15803d20; }
        .tab-pane { display: none; animation: fadeIn 0.3s ease-out; }
        .tab-pane.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        .ticket-modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .ticket-modal-content { background: white; border-radius: 1.5rem; padding: 1.5rem; width: 90%; max-width: 700px; max-height: 90vh; overflow-y: auto; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); color: #1e293b; }
        body.dark .ticket-modal-content { background: #1e293b; color: #e6f1ff; }
        .comments-list { max-height: 40vh; overflow-y: auto; margin-bottom: 1rem; }
        .comment-item { background: #f1f5f9; border-radius: 0.75rem; padding: 0.75rem; margin-bottom: 0.5rem; }
        body.dark .comment-item { background: #334155; }
        @media (max-width: 640px) {
            .ticket-modal-content { padding: 1rem; width: 95%; }
            .tab-btn { padding: 0.3rem 0.6rem; font-size: 0.8rem; }
            .btn-primary { padding: 0.4rem 0.8rem; font-size: 0.8rem; }
        }
        button:active { transform: scale(0.98); }
        .bg-blue-600:hover { background: #1d4ed8 !important; transform: scale(1.02); }
        .bg-green-600:hover { background: #166534 !important; transform: scale(1.02); }
        .bg-red-600:hover { background: #b91c1c !important; transform: scale(1.02); }
        .bg-yellow-500:hover { background: #ca8a04 !important; transform: scale(1.02); }
        
        /* Улучшенная читаемость для вкладки "База знаний" */
        .knowledge-title {
            font-size: 1.75rem !important;
            font-weight: 700 !important;
            margin-bottom: 1.25rem !important;
            color: #0f172a !important;
        }
        body.dark .knowledge-title {
            color: #f1f5f9 !important;
        }
        .knowledge-search {
            font-size: 1rem !important;
            padding: 0.75rem 1rem !important;
            border-radius: 0.75rem !important;
            border: 1px solid #cbd5e1 !important;
            background-color: #ffffff !important;
            color: #1e293b !important;
            margin-bottom: 1.5rem !important;
        }
        body.dark .knowledge-search {
            background-color: #1e293b !important;
            border-color: #475569 !important;
            color: #e2e8f0 !important;
        }
        .knowledge-search::placeholder {
            color: #64748b !important;
            opacity: 1 !important;
        }
        .article-card {
            background: #f8fafc !important;
            border-radius: 1rem !important;
            padding: 1.25rem !important;
            margin-bottom: 1rem !important;
            transition: all 0.2s;
            border: 1px solid #e2e8f0 !important;
        }
        body.dark .article-card {
            background: #1e293b !important;
            border-color: #334155 !important;
        }
        .article-title {
            font-size: 1.25rem !important;
            font-weight: 700 !important;
            margin-bottom: 0.5rem !important;
            color: #0f172a !important;
            line-height: 1.4 !important;
        }
        body.dark .article-title {
            color: #f1f5f9 !important;
        }
        .article-content {
            font-size: 1rem !important;
            line-height: 1.6 !important;
            color: #1e293b !important;
        }
        body.dark .article-content {
            color: #cbd5e1 !important;
        }
    </style>
</head>
<body class="light">
<div class="max-w-7xl mx-auto px-4 py-6 container">
    <div class="flex justify-between items-center mb-8 p-4 bg-white shadow rounded-2xl border">
        <div class="flex items-center gap-3"><div class="text-3xl">📊</div><div><h1 class="text-2xl font-bold text-gray-800">Quality Monitor Pro</h1><p class="text-xs text-gray-500">Мониторинг качества услуг и технической поддержки</p></div></div>
        <div class="flex items-center gap-4">
            <button id="qrButton" class="text-2xl hover:scale-110 transition">📱</button>
            <button id="themeToggle" class="text-2xl hover:scale-110 transition">🌙</button>
            <a href="/privacy" target="_blank" class="text-xs text-gray-500 hover:text-gray-700 transition">Политика конфиденциальности</a>
            <div id="userPanel"></div>
        </div>
    </div>
    <div id="app"></div>
</div>
<div id="qrModal" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 hidden">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 text-center max-w-sm">
        <h3 class="text-lg font-bold mb-2">QR-код ссылки на сайт</h3>
        <img id="qrImage" src="" alt="QR Code" class="mx-auto my-4">
        <p class="text-sm break-all" id="qrUrl"></p>
        <button class="mt-4 bg-gray-500 text-white px-4 py-2 rounded hover:bg-gray-600" onclick="document.getElementById('qrModal').classList.add('hidden')">Закрыть</button>
    </div>
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
    document.getElementById('qrButton').addEventListener('click', function() {
        let url = window.location.href;
        let qrSrc = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(url)}`;
        document.getElementById('qrImage').src = qrSrc;
        document.getElementById('qrUrl').innerText = url;
        document.getElementById('qrModal').classList.remove('hidden');
    });

    async function api(url, method='GET', body=null) {
        let opts = { method };
        if(body) {
            opts.body = body;
            opts.headers = {'Content-Type':'application/x-www-form-urlencoded'};
        }
        let res = await fetch(url, opts);
        if (res.status === 401) {
            document.cookie.split(";").forEach(c => {
                document.cookie = c.replace(/^ +/, "").replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/");
            });
            if (currentUser !== null) {
                currentUser = null;
                renderLogin();
            }
            throw new Error('Unauthorized');
        }
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
            document.getElementById('userPanel').innerHTML = `<span class="font-medium">${currentUser.name}</span><span class="bg-gray-200 dark:bg-gray-700 px-2 py-0.5 rounded-full text-sm">${currentUser.role}</span><button class="bg-red-50 text-red-600 hover:bg-red-100 px-3 py-1 rounded-full text-sm transition" onclick="logout()">Выйти</button>`;
            renderUI();
        } catch(e) {
            currentUser = null;
            document.cookie.split(";").forEach(c => {
                document.cookie = c.replace(/^ +/, "").replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/");
            });
            renderLogin();
        }
    }

    async function logout() {
        try { await api('/api/logout','POST'); } catch(e) {}
        currentUser = null;
        renderLogin();
    }

    function renderLogin() {
        document.getElementById('app').innerHTML = `
            <div class="max-w-md mx-auto card">
                <h2 class="text-2xl font-bold mb-4">Вход</h2>
                <form id="loginForm" class="space-y-4">
                    <div><label class="block text-sm font-medium">Email</label><input id="email" type="email" class="w-full"></div>
                    <div><label class="block text-sm font-medium">Пароль</label><input id="password" type="password" class="w-full"></div>
                    <button type="submit" class="btn-primary w-full">Войти</button>
                </form>
                <hr class="my-6 border-gray-200 dark:border-gray-700">
                <h3 class="text-xl font-semibold mb-4">Регистрация</h3>
                <form id="registerForm" class="space-y-4">
                    <div><label class="block text-sm font-medium">Email</label><input id="regEmail" type="email" class="w-full"></div>
                    <div><label class="block text-sm font-medium">ФИО</label><input id="regName" class="w-full"></div>
                    <div><label class="block text-sm font-medium">Пароль</label><input id="regPassword" type="password" class="w-full"></div>
                    <div class="flex items-center gap-2">
                        <input type="checkbox" id="regPrivacy" required>
                        <label class="text-sm">Я согласен с <a href="/privacy" target="_blank" class="text-blue-600 hover:underline">политикой обработки персональных данных</a></label>
                    </div>
                    <button type="submit" class="btn-primary w-full">Зарегистрироваться</button>
                </form>
            </div>
        `;
        document.getElementById('loginForm').onsubmit = async (e) => {
            e.preventDefault();
            try {
                await login(e.target.email.value, e.target.password.value);
                notyf.success('Вход выполнен');
            } catch(e) { notyf.error('Ошибка входа'); }
        };
        document.getElementById('registerForm').onsubmit = async (e) => {
            e.preventDefault();
            let privacy = document.getElementById('regPrivacy').checked;
            if(!privacy) { notyf.error('Необходимо согласие на обработку персональных данных'); return; }
            let form = new URLSearchParams({ email:e.target.regEmail.value, full_name:e.target.regName.value, password:e.target.regPassword.value, privacy_accepted:privacy });
            await fetch('/api/register', { method:'POST', body:form });
            notyf.success('Регистрация успешна, теперь войдите');
        };
    }

    async function renderUI() {
        if(!currentUser) return;
        let tabs = [];
        if(currentUser.role === 'client') tabs = [
            { id: 'myTickets', label: 'Мои заявки', render: renderClientTickets },
            { id: 'newTicket', label: 'Новая заявка', render: renderNewTicket },
            { id: 'knowledge', label: 'База знаний', render: renderKnowledge }
        ];
        else if(currentUser.role === 'operator') tabs = [
            { id: 'allTickets', label: 'Все заявки', render: renderOperatorTickets },
            { id: 'exportData', label: 'Экспорт', render: renderExport }
        ];
        else if(currentUser.role === 'admin') tabs = [
            { id: 'usersManage', label: 'Пользователи', render: renderAdminUsers },
            { id: 'slaSettings', label: 'Настройки SLA', render: renderAdminSLA },
            { id: 'logs', label: 'Логи', render: renderAdminLogs },
            { id: 'knowledge', label: 'База знаний', render: renderKnowledge },
            { id: 'dashboard', label: 'Дашборд', render: renderDashboard },
            { id: 'advancedAnalytics', label: 'Аналитика оценок', render: renderAdvancedDashboard },
            { id: 'exportData', label: 'Экспорт', render: renderExport }
        ];
        else if(currentUser.role === 'quality') tabs = [
            { id: 'dashboard', label: 'Дашборд', render: renderDashboard },
            { id: 'advancedAnalytics', label: 'Аналитика оценок', render: renderAdvancedDashboard },
            { id: 'knowledge', label: 'База знаний', render: renderKnowledge },
            { id: 'exportData', label: 'Экспорт', render: renderExport }
        ];

        let tabsHtml = `<div class="flex gap-2 mb-4 border-b pb-2 flex-wrap">${tabs.map((t,i)=>`<button class="tab-btn ${i===0?'active':''}" data-tab="${i}">${t.label}</button>`).join('')}</div><div id="panes"></div>`;
        document.getElementById('app').innerHTML = tabsHtml;
        let panesDiv = document.getElementById('panes');

        for(let i=0; i<tabs.length; i++) {
            let pane = document.createElement('div');
            pane.className = `tab-pane ${i===0?'active':''}`;
            pane.id = `pane-${i}`;
            panesDiv.appendChild(pane);
        }

        async function loadPane(index) {
            let pane = document.getElementById(`pane-${index}`);
            if (!pane) return;
            let tab = tabs[index];
            if (!tab) return;
            pane.innerHTML = '';
            await tab.render(pane);
        }

        await loadPane(0);
        document.querySelectorAll('.tab-btn').forEach((btn, idx) => {
            btn.onclick = async () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
                document.getElementById(`pane-${idx}`).classList.add('active');
                await loadPane(idx);
            };
        });
    }

    async function renderClientTickets(container) {
        let data = await api('/api/tickets');
        let html = '<div class="card overflow-x-auto"><table class="w-full"><thead><tr><th>Номер</th><th>Название</th><th>Статус</th><th>Приоритет</th><th>Дата</th><th>Оценка</th><th>Ответ</th><th>Действие</th></tr></thead><tbody>';
        for(let t of data.tickets) {
            let actionBtn = '';
            if(t.status === 'resolved' && !t.satisfaction) actionBtn = `<button class="bg-green-600 text-white px-2 py-1 rounded text-sm hover:bg-green-700" onclick="openDetailedReview(${t.id})">Оценить</button>`;
            html += `<tr><td data-label="Номер">${t.id}<td data-label="Название">${t.title}<td data-label="Статус"><span class="status-badge status-${t.status}">${t.status}</span><td data-label="Приоритет">${t.priority}<td data-label="Дата">${new Date(t.created_at).toLocaleDateString()}<td data-label="Оценка">${t.satisfaction?'⭐'+t.satisfaction:'—'}<td data-label="Ответ">${t.review||'—'}<td data-label="Действие"><button class="bg-blue-600 text-white px-2 py-1 rounded text-sm hover:bg-blue-700" onclick="viewTicket(${t.id})">Открыть</button> ${actionBtn}</td>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        
        window.viewTicket = async (id) => {
            let ticketsData = await api('/api/tickets');
            let ticket = ticketsData.tickets.find(t => t.id === id);
            if (!ticket) return;
            let comments = await api(`/api/tickets/${id}/comments`);
            let modalHtml = `
                <div id="ticketModal" class="ticket-modal">
                    <div class="ticket-modal-content">
                        <h2 class="text-xl font-bold mb-4">Заявка №${ticket.id}</h2>
                        <div class="space-y-2 mb-4">
                            <div><strong>Название:</strong> ${ticket.title}</div>
                            <div><strong>Описание:</strong> ${ticket.description || '—'}</div>
                            <div><strong>Статус:</strong> <span class="status-badge status-${ticket.status}">${ticket.status}</span></div>
                            <div><strong>Приоритет:</strong> ${ticket.priority}</div>
                            <div><strong>Категория:</strong> ${ticket.category || '—'}</div>
                            <div><strong>Создана:</strong> ${new Date(ticket.created_at).toLocaleString()}</div>
                        </div>
                        <hr class="my-2">
                        <h3 class="font-semibold mb-2">Комментарии</h3>
                        <div id="commentsList" class="comments-list">
                            ${comments.map(c => `<div class="comment-item"><span class="font-semibold">${c.user_name}</span> <span class="text-xs text-gray-500">${new Date(c.created_at).toLocaleString()}</span><div class="mt-1">${c.comment}</div></div>`).join('')}
                        </div>
                        <textarea id="newComment" rows="2" class="w-full border rounded-lg p-2 mb-2" placeholder="Напишите комментарий..."></textarea>
                        <div class="flex justify-end gap-2">
                            <button class="bg-gray-500 text-white px-4 py-2 rounded-lg hover:bg-gray-600" onclick="closeModal()">Закрыть</button>
                            <button class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700" onclick="addComment(${id})">Отправить</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            window.closeModal = () => document.getElementById('ticketModal')?.remove();
            window.addComment = async (id) => {
                let commentText = document.getElementById('newComment')?.value.trim();
                if (!commentText) { notyf.error('Введите текст'); return; }
                let form = new URLSearchParams({ comment: commentText });
                await api(`/api/tickets/${id}/comments`, 'POST', form);
                notyf.success('Комментарий отправлен');
                closeModal();
                renderUI();
            };
        };
        
        window.openDetailedReview = async (id) => {
            const modal = document.createElement('div'); modal.className = 'fixed inset-0 bg-black/70 flex items-center justify-center z-50';
            modal.innerHTML = `
                <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 w-full max-w-md">
                    <h3 class="text-xl font-bold mb-4">Оцените качество</h3>
                    <div class="space-y-4">
                        <div><label>Общая (1-5)</label><div class="flex gap-1 stars" data-crit="overall">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer hover:text-yellow-400" data-val="${v}">★</span>`).join('')}</div><input type="hidden" id="overallVal"></div>
                        <div><label>Скорость ответа</label><div class="flex gap-1 stars" data-crit="speed">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer hover:text-yellow-400" data-val="${v}">★</span>`).join('')}</div><input type="hidden" id="speedVal"></div>
                        <div><label>Профессионализм</label><div class="flex gap-1 stars" data-crit="prof">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer hover:text-yellow-400" data-val="${v}">★</span>`).join('')}</div><input type="hidden" id="profVal"></div>
                        <div><label>Вежливость</label><div class="flex gap-1 stars" data-crit="politeness">${[1,2,3,4,5].map(v=>`<span class="star text-2xl cursor-pointer hover:text-yellow-400" data-val="${v}">★</span>`).join('')}</div><input type="hidden" id="politenessVal"></div>
                        <div><label>Комментарий</label><textarea id="reviewComment" rows="3" class="w-full border rounded p-2"></textarea></div>
                        <div class="flex justify-end gap-2"><button class="bg-gray-500 px-4 py-2 rounded hover:bg-gray-600" onclick="this.closest('.fixed').remove()">Отмена</button><button class="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700" onclick="submitReview(${id})">Отправить</button></div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            modal.querySelectorAll('.stars').forEach(group => {
                let crit = group.dataset.crit;
                group.querySelectorAll('.star').forEach(star => {
                    star.onclick = () => {
                        group.querySelectorAll('.star').forEach(s=>s.style.color='');
                        star.style.color='#facc15';
                        document.getElementById(`${crit}Val`).value = star.dataset.val;
                    };
                });
            });
            window.submitReview = async (id) => {
                let overall = document.getElementById('overallVal')?.value;
                let speed = document.getElementById('speedVal')?.value;
                let prof = document.getElementById('profVal')?.value;
                let politeness = document.getElementById('politenessVal')?.value;
                let comment = document.getElementById('reviewComment')?.value || '';
                if(!overall || !speed || !prof || !politeness) { notyf.error('Заполните все оценки'); return; }
                let form = new URLSearchParams({ overall, speed, professionalism:prof, politeness, comment });
                await api(`/api/tickets/${id}/detailed_review`, 'POST', form);
                notyf.success('Спасибо за отзыв!');
                modal.remove();
                renderUI();
            };
        };
    }

    function renderNewTicket(container) {
        container.innerHTML = `<div class="card"><h3 class="text-xl font-semibold mb-4">Новая заявка</h3>
        <form id="newForm" class="space-y-4">
            <div><label>Название</label><input id="title" required></div>
            <div><label>Описание</label><textarea id="desc" rows="3"></textarea></div>
            <div><label>Приоритет</label><select id="priority"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></div>
            <div><label>Категория</label><select id="category"></select></div>
            <div class="flex items-center gap-2">
                <input type="checkbox" id="privacyConsent" required>
                <label class="text-sm">Я согласен с <a href="/privacy" target="_blank" class="text-blue-600 hover:underline">политикой обработки персональных данных</a></label>
            </div>
            <button type="submit" class="btn-primary">Создать</button>
        </form></div>`;
        fetch('/api/categories').then(r=>r.json()).then(cats=>{ let sel=document.getElementById('category'); cats.forEach(c=>{ let opt=document.createElement('option'); opt.value=c.name; opt.innerText=c.name; sel.appendChild(opt); }); });
        document.getElementById('newForm').onsubmit = async (e) => {
            e.preventDefault();
            let privacy = document.getElementById('privacyConsent').checked;
            if(!privacy) { notyf.error('Необходимо согласие на обработку персональных данных'); return; }
            let body = new URLSearchParams({ title:document.getElementById('title').value, description:document.getElementById('desc').value, priority:document.getElementById('priority').value, category:document.getElementById('category').value, privacy_accepted:privacy });
            await api('/api/tickets','POST',body);
            notyf.success('Заявка создана');
            renderUI();
        };
    }

    async function renderKnowledge(container) {
        let articles = await api('/api/knowledge');
        const wrapper = document.createElement('div');
        wrapper.className = 'card';
        wrapper.innerHTML = `
            <h3 class="knowledge-title">📚 База знаний</h3>
            <input type="text" id="kbSearchInput" class="knowledge-search w-full" placeholder="Поиск...">
            <div id="articlesList"></div>
        `;
        container.appendChild(wrapper);
        
        const searchInput = wrapper.querySelector('#kbSearchInput');
        const articlesDiv = wrapper.querySelector('#articlesList');
        
        function renderArticles(articlesArray) {
            if (articlesArray.length === 0) {
                articlesDiv.innerHTML = '<div class="text-center text-gray-500 py-4">Статей не найдено</div>';
                return;
            }
            articlesDiv.innerHTML = articlesArray.map(a => `
                <div class="article-card">
                    <div class="article-title">${escapeHtml(a.title)}</div>
                    <div class="article-content">${escapeHtml(a.content)}</div>
                </div>
            `).join('');
        }
        
        function escapeHtml(str) {
            return str.replace(/[&<>]/g, function(m) {
                if (m === '&') return '&amp;';
                if (m === '<') return '&lt;';
                if (m === '>') return '&gt;';
                return m;
            }).replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, function(c) {
                return c;
            });
        }
        
        renderArticles(articles);
        
        searchInput.addEventListener('input', async (e) => {
            const query = e.target.value.trim();
            let filtered;
            if (query === '') {
                filtered = articles;
            } else {
                const response = await api(`/api/knowledge?search=${encodeURIComponent(query)}`);
                filtered = response;
            }
            renderArticles(filtered);
        });
    }

    async function renderOperatorTickets(container) {
        let data = await api('/api/tickets');
        let html = '<div class="card overflow-x-auto"><table class="w-full"><thead><tr><th>Номер</th><th>Название</th><th>Описание</th><th>Статус</th><th>Приоритет</th><th>Действия</th></tr></thead><tbody>';
        for(let t of data.tickets) {
            let actions = '';
            if(t.status === 'new') actions = `<button class="bg-yellow-500 text-white px-2 py-1 rounded text-sm hover:bg-yellow-600" onclick="assign(${t.id})">Принять</button>`;
            if(t.status === 'in_progress') actions = `<button class="bg-green-600 text-white px-2 py-1 rounded text-sm hover:bg-green-700" onclick="resolve(${t.id})">Решить</button> <button class="bg-blue-600 text-white px-2 py-1 rounded text-sm hover:bg-blue-700" onclick="respond(${t.id})">Ответить</button>`;
            if(t.status === 'resolved') actions = `<button class="bg-red-600 text-white px-2 py-1 rounded text-sm hover:bg-red-700" onclick="closeTicket(${t.id})">Закрыть</button>`;
            html += `<tr><td data-label="Номер">${t.id}<td data-label="Название">${t.title}<td data-label="Описание">${t.description||'—'}<td data-label="Статус"><span class="status-badge status-${t.status}">${t.status}</span><td data-label="Приоритет">${t.priority}<td data-label="Действия"><button class="bg-blue-600 text-white px-2 py-1 rounded text-sm hover:bg-blue-700" onclick="viewTicket(${t.id})">Открыть</button> ${actions}</tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.assign = async (id) => { await api(`/api/tickets/${id}?status=in_progress&assigned_to_id=${currentUser.id}`,'PUT'); renderUI(); };
        window.resolve = async (id) => { let rev = prompt("Комментарий к решению (будет виден клиенту):"); if(rev !== null) { await api(`/api/tickets/${id}?status=resolved&review=${encodeURIComponent(rev)}`,'PUT'); renderUI(); } };
        window.closeTicket = async (id) => { await api(`/api/tickets/${id}?status=closed`,'PUT'); renderUI(); };
        window.respond = async (id) => { let msg = prompt("Ответ клиенту:"); if(msg) { await api(`/api/tickets/${id}?review=${encodeURIComponent(msg)}`,'PUT'); renderUI(); } };
        window.viewTicket = async (id) => {
            let ticketsData = await api('/api/tickets');
            let ticket = ticketsData.tickets.find(t => t.id === id);
            if (!ticket) return;
            let comments = await api(`/api/tickets/${id}/comments`);
            let modalHtml = `
                <div id="ticketModal" class="ticket-modal">
                    <div class="ticket-modal-content">
                        <h2 class="text-xl font-bold mb-4">Заявка №${ticket.id}</h2>
                        <div class="space-y-2 mb-4">
                            <div><strong>Название:</strong> ${ticket.title}</div>
                            <div><strong>Описание:</strong> ${ticket.description || '—'}</div>
                            <div><strong>Статус:</strong> <span class="status-badge status-${ticket.status}">${ticket.status}</span></div>
                            <div><strong>Приоритет:</strong> ${ticket.priority}</div>
                            <div><strong>Категория:</strong> ${ticket.category || '—'}</div>
                            <div><strong>Создана:</strong> ${new Date(ticket.created_at).toLocaleString()}</div>
                        </div>
                        <hr class="my-2">
                        <h3 class="font-semibold mb-2">Комментарии</h3>
                        <div id="commentsList" class="comments-list">
                            ${comments.map(c => `<div class="comment-item"><span class="font-semibold">${c.user_name}</span> <span class="text-xs">${new Date(c.created_at).toLocaleString()}</span><div>${c.comment}</div></div>`).join('')}
                        </div>
                        <textarea id="newComment" rows="2" class="w-full border rounded-lg p-2 mb-2" placeholder="Напишите комментарий..."></textarea>
                        <div class="flex justify-end gap-2">
                            <button class="bg-gray-500 text-white px-4 py-2 rounded-lg hover:bg-gray-600" onclick="closeModal()">Закрыть</button>
                            <button class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700" onclick="addComment(${id})">Отправить</button>
                        </div>
                    </div>
                </div>
            `;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            window.closeModal = () => document.getElementById('ticketModal')?.remove();
            window.addComment = async (id) => {
                let commentText = document.getElementById('newComment')?.value.trim();
                if (!commentText) { notyf.error('Введите текст'); return; }
                let form = new URLSearchParams({ comment: commentText });
                await api(`/api/tickets/${id}/comments`, 'POST', form);
                notyf.success('Комментарий отправлен');
                closeModal();
                renderUI();
            };
        };
    }

    function renderExport(container) {
        container.innerHTML = `<div class="card"><h3 class="text-xl font-semibold mb-4">Экспорт</h3><a href="/api/export/tickets" target="_blank"><button class="btn-primary">Скачать CSV</button></a></div>`;
    }

    async function renderAdminUsers(container) {
        let users = await api('/api/users');
        let html = '<div class="card overflow-x-auto"><h3 class="text-xl font-semibold mb-4">Управление пользователями</h3><table class="w-full"><thead><tr><th>ID</th><th>Email</th><th>ФИО</th><th>Роль</th><th>Новая роль</th><th></th></tr></thead><tbody>';
        for(let u of users) {
            html += `<tr><td data-label="ID">${u.id}<td data-label="Email">${u.email}<td data-label="ФИО">${u.full_name}<td data-label="Роль">${u.role}<td data-label="Новая роль"><select id="role-${u.id}"><option>client</option><option>operator</option><option>admin</option><option>quality</option></select><td data-label="Действия"><button class="bg-blue-600 text-white px-2 py-1 rounded text-sm hover:bg-blue-700" onclick="changeRole(${u.id})">Изменить</button> <button class="bg-red-600 text-white px-2 py-1 rounded text-sm hover:bg-red-700" onclick="delUser(${u.id})">Удалить</button></tr>`;
        }
        html += `</tbody></table></div>`;
        container.innerHTML = html;
        window.changeRole = async (id) => { let newRole = document.getElementById(`role-${id}`).value; await api(`/api/users/${id}/role?new_role=${newRole}`,'PUT'); notyf.success('Роль изменена'); renderAdminUsers(container); };
        window.delUser = async (id) => { if(confirm('Удалить пользователя?')) { await api(`/api/users/${id}`, 'DELETE'); notyf.success('Пользователь удалён'); renderAdminUsers(container); } };
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
        let html = `<div class="card overflow-x-auto"><h3 class="text-xl font-semibold mb-4">Логи</h3><table class="w-full"><thead><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Детали</th></tr></thead><tbody>`;
        for(let l of logs) html += `<tr><td data-label="Время">${new Date(l.time).toLocaleString()}<td data-label="Пользователь">${l.user_id}<td data-label="Действие">${l.action}<td data-label="Детали">${l.details||''}</tr>`;
        html += `</tbody><tr></div>`;
        container.innerHTML = html;
    }

    async function renderDashboard(container) {
        let m = await api('/api/dashboard/metrics');
        const statusLabels = Object.keys(m.status_counts);
        const statusData = Object.values(m.status_counts);
        const dailyLabels = m.daily_labels;
        const dailyData = m.daily_data;
        const slaComp = m.sla_compliance;
        const avgRes = m.avg_resolution_minutes;
        container.innerHTML = `
            <div class="card">
                <h3 class="text-xl font-semibold mb-4">📊 Дашборд качества</h3>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
                    <div class="bg-gradient-to-br from-blue-500 to-blue-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Всего заявок</div><div class="text-3xl font-bold">${m.total_tickets}</div></div>
                    <div class="bg-gradient-to-br from-green-500 to-green-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Решено/закрыто</div><div class="text-3xl font-bold">${m.resolved_tickets}</div><div class="text-xs mt-1">${((m.resolved_tickets/m.total_tickets)*100).toFixed(1)}%</div></div>
                    <div class="bg-gradient-to-br from-purple-500 to-purple-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Средний CSAT</div><div class="text-3xl font-bold">${m.avg_csat}/5</div></div>
                    <div class="bg-gradient-to-br from-orange-500 to-orange-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Соблюдение SLA</div><div class="text-3xl font-bold">${slaComp}%</div></div>
                    <div class="bg-gradient-to-br from-cyan-500 to-cyan-600 text-white p-4 rounded-xl shadow"><div class="text-sm opacity-90">Ср. время решения</div><div class="text-3xl font-bold">${avgRes}<span class="text-lg"> мин</span></div></div>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div class="bg-gray-50 dark:bg-gray-800 p-4 rounded-xl"><canvas id="statusChartDash" height="200"></canvas></div>
                    <div class="bg-gray-50 dark:bg-gray-800 p-4 rounded-xl"><canvas id="trendChartDash" height="200"></canvas></div>
                </div>
                <div class="text-xs text-gray-500 text-center mt-6">Данные обновлены: ${new Date().toLocaleString()}</div>
            </div>
        `;
        const colors = { 'new':'#facc15','in_progress':'#3b82f6','resolved':'#22c55e','closed':'#64748b'};
        new Chart(document.getElementById('statusChartDash'), { type:'pie', data:{ labels:statusLabels.map(s=>({new:'Новые',in_progress:'В работе',resolved:'Решённые',closed:'Закрытые'}[s]||s)), datasets:[{ data:statusData, backgroundColor:statusLabels.map(s=>colors[s]||'#94a3b8') }] }, options:{ responsive:true } });
        new Chart(document.getElementById('trendChartDash'), { type:'line', data:{ labels:dailyLabels, datasets:[{ label:'Заявки', data:dailyData, borderColor:'#3b82f6', fill:true }] }, options:{ responsive:true } });
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
        <div class="overflow-x-auto"><table class="w-full"><thead><tr><th>Оператор</th><th>Оценок</th><th>Общая</th><th>Скорость</th><th>Проф.</th><th>Вежливость</th></tr></thead><tbody id="operatorTable"></tbody></tr></div>
        </div>`;
        const users = await api('/api/users');
        const cats = await api('/api/categories');
        let opSelect = document.getElementById('operatorFilter');
        opSelect.innerHTML = '<option value="">Все</option>' + users.filter(u=>u.role==='operator').map(u=>`<option value="${u.id}">${u.full_name}</option>`).join('');
        let catSelect = document.getElementById('categoryFilter');
        catSelect.innerHTML = '<option value="">Все</option>' + cats.map(c=>`<option value="${c.name}">${c.name}</option>`).join('');
        const loadData = async () => {
            let period = document.getElementById('periodFilter').value;
            let op_id = document.getElementById('operatorFilter').value;
            let cat = document.getElementById('categoryFilter').value;
            let params = new URLSearchParams({ period });
            if(op_id) params.append('operator_id', op_id);
            if(cat) params.append('category', cat);
            let data = await api(`/api/dashboard/advanced_metrics?${params}`);
            document.getElementById('overallAvg').innerText = data.overall_avg;
            document.getElementById('speedAvg').innerText = data.speed_avg;
            document.getElementById('profAvg').innerText = data.prof_avg;
            document.getElementById('politenessAvg').innerText = data.politeness_avg;
            let tableHtml = '';
            const opColors = ['#3b82f6','#ef4444','#22c55e','#facc15','#a855f7','#ec4899','#14b8a6','#f97316'];
            for(let op of data.operator_stats) {
                let badge = op.overall_avg>=4.5?'bg-green-100 text-green-800':(op.overall_avg>=3?'bg-yellow-100 text-yellow-800':'bg-red-100 text-red-800');
                tableHtml += `<tr><td class="font-medium">${op.name}<td class="text-center">${op.count}<td class="text-center"><span class="px-2 py-1 rounded-full text-sm ${badge}">${op.overall_avg}</span><td class="text-center">${op.speed_avg}<td class="text-center">${op.prof_avg}<td class="text-center">${op.politeness_avg}</td>`;
            }
            document.getElementById('operatorTable').innerHTML = tableHtml;
            const ctx = document.getElementById('operatorChart').getContext('2d');
            if(window.opChart) window.opChart.destroy();
            let labels = data.operator_stats.map(o=>o.name);
            let overalls = data.operator_stats.map(o=>o.overall_avg);
            window.opChart = new Chart(ctx, { type:'bar', data:{ labels, datasets:[{ label:'Общая оценка', data:overalls, backgroundColor:labels.map((_,i)=>opColors[i%opColors.length]) }] } });
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
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🚀 Сервер Quality Monitor Pro запущен на порту {port}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port)
