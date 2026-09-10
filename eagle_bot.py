import os
import re
import json
import uuid
import sqlite3
import secrets
import datetime as dt
from threading import Lock
from urllib.parse import urljoin

import requests
from flask import Flask, request, jsonify, redirect, make_response, render_template_string

app = Flask(__name__)

# ============================================================
# EAGLE BOT — PRODUCT CONFIGURATION
# ============================================================
# Telegram is a communication channel, NOT Eagle Bot's core.
# Eagle can run independently as a web application/API and may
# later be embedded into larger products, including Alpha assets.
# ============================================================
BOT_TOKEN = os.environ.get("8560176445:AAGm_kUPsMBGUKpTcGYY2Gs436BQuAodweQ", "").strip()
YOUR_USER_ID = os.environ.get("6992393855").strip()
TELEGRAM_CHANNEL_ID =os.environ.get("1004378665713").strip ()
PUBLIC_WEB_APP_URL = os.environ.get("PUBLIC_WEB_APP_URL", "").strip().rstrip("/")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
EAGLE_AI_API_URL = os.environ.get("EAGLE_AI_API_URL", "").strip()
EAGLE_AI_API_KEY = os.environ.get("EAGLE_AI_API_KEY", "").strip()
DATABASE_PATH = os.environ.get("DATABASE_PATH", "eagle_bot.db").strip()
PORT = int(os.environ.get("PORT", "5000"))

# Eagle Bot is globally accessible. Human support availability can
# be layered later without making the core digital service unavailable.
WORK_START = 0
WORK_END = 24
TIMEZONE_NAME = "UTC"

DB_LOCK = Lock()
WEBHOOK_TIMEOUT_SECONDS = 12
AI_TIMEOUT_SECONDS = 45

# ============================================================
# APP IDENTITY / COWORKER SYSTEM
# ============================================================
PRODUCT_NAME = "Eagle Bot"
PRODUCT_TAGLINE = "One doorway to work, knowledge, execution, and progress."
PRODUCT_DESCRIPTION = (
    "Eagle Bot is an independent, globally accessible AI-assisted work platform. "
    "Telegram is one channel; the Eagle web app and API are first-class interfaces. "
    "Its Coworker architecture is designed to grow into a coordinated team of specialized agents."
)

COWORKERS = {
    "general": {
        "name": "Eagle Generalist",
        "mission": "Understand the user's goal, clarify the task, and coordinate the right Coworker."
    },
    "research": {
        "name": "Eagle Researcher",
        "mission": "Find, compare, synthesize, and structure knowledge for decisions and action."
    },
    "builder": {
        "name": "Eagle Builder",
        "mission": "Turn ideas into specifications, software plans, workflows, prototypes, and deliverables."
    },
    "analyst": {
        "name": "Eagle Analyst",
        "mission": "Break complex problems into evidence, models, trade-offs, and measurable next actions."
    },
    "operator": {
        "name": "Eagle Operator",
        "mission": "Coordinate execution, processes, tasks, follow-ups, and operational workflows."
    },
    "educator": {
        "name": "Eagle Educator",
        "mission": "Teach, explain, mentor, and convert difficult concepts into practical learning paths."
    },
}

# ============================================================
# DATABASE — persistent by default, Telegram-independent
# ============================================================

def db():
    connection = sqlite3.connect(DATABASE_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db():
    with DB_LOCK, db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_number TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                email TEXT NOT NULL,
                topic TEXT NOT NULL,
                word_count INTEGER NOT NULL,
                deadline TEXT NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Processing',
                source TEXT NOT NULL DEFAULT 'telegram',
                telegram_user_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                visitor_id TEXT NOT NULL,
                telegram_user_id TEXT,
                coworker_key TEXT NOT NULL DEFAULT 'general',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                coworker_key TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                visitor_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                coworker_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
            CREATE INDEX IF NOT EXISTS idx_orders_telegram_user ON orders(telegram_user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """
        )


init_db()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def utc_date():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def human_time():
    return dt.datetime.now(dt.timezone.utc).strftime("%I:%M %p UTC")


def generate_order_number():
    # Collision-resistant while retaining the familiar EAGLE-XXXX style.
    for _ in range(20):
        code = secrets.randbelow(900000) + 100000
        order_number = f"EAGLE-{code}"
        with DB_LOCK, db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE order_number = ?", (order_number,)
            ).fetchone()
        if not exists:
            return order_number
    raise RuntimeError("Unable to create a unique order number")


def save_order(order_data):
    with DB_LOCK, db() as conn:
        conn.execute(
            """
            INSERT INTO orders (
                order_number, customer_name, email, topic, word_count, deadline,
                date, status, source, telegram_user_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_data["order_number"],
                order_data["customer_name"],
                order_data["email"],
                order_data["topic"],
                order_data["word_count"],
                order_data["deadline"],
                order_data["date"],
                order_data.get("status", "Processing"),
                order_data.get("source", "telegram"),
                order_data.get("telegram_user_id"),
                order_data.get("created_at", now_iso()),
            ),
        )
        return True


def get_order(order_number):
    with DB_LOCK, db() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_number = ?", (order_number.upper(),)
        ).fetchone()
    return dict(row) if row else None


def get_orders_for_user(telegram_user_id):
    with DB_LOCK, db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE telegram_user_id = ? ORDER BY created_at DESC",
            (str(telegram_user_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def create_conversation(visitor_id, coworker_key="general", telegram_user_id=None):
    conversation_id = str(uuid.uuid4())
    timestamp = now_iso()
    with DB_LOCK, db() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, visitor_id, telegram_user_id, coworker_key, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, visitor_id, str(telegram_user_id) if telegram_user_id else None,
             coworker_key, timestamp, timestamp),
        )
    return conversation_id


def save_message(conversation_id, role, content, coworker_key=None):
    with DB_LOCK, db() as conn:
        conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, coworker_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), conversation_id, role, content, coworker_key, now_iso()),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now_iso(), conversation_id),
        )


def queue_task(visitor_id, title, description, coworker_key="general", conversation_id=None):
    task_id = str(uuid.uuid4())
    timestamp = now_iso()
    with DB_LOCK, db() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, conversation_id, visitor_id, title, description,
                coworker_key, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (task_id, conversation_id, visitor_id, title, description,
             coworker_key, timestamp, timestamp),
        )
    return task_id

# ============================================================
# TELEGRAM TRANSPORT — one channel among many
# ============================================================

def telegram_api(method):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def telegram_request(method, payload):
    response = requests.post(
        telegram_api(method), json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("description", "Telegram API request failed"))
    return body


def send_telegram_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_request("sendMessage", payload)


def send_telegram_file(chat_id, file_id):
    return telegram_request("sendDocument", {"chat_id": chat_id, "document": file_id})


def eagle_web_url(path="/app"):
    base = PUBLIC_WEB_APP_URL
    if base:
        return urljoin(base + "/", path.lstrip("/"))
    return None


def telegram_web_buttons():
    web_url = eagle_web_url("/app")
    buttons = []
    if web_url:
        buttons.append([{"text": "🦅 Open Eagle Bot Web App", "url": web_url}])
    # When the URL is HTTPS, Telegram clients can also open it as a Web App.
    if web_url and web_url.startswith("https://"):
        buttons.append([{"text": "⚡ Open Eagle Workspace", "web_app": {"url": web_url}}])
    return {"inline_keyboard": buttons} if buttons else None


def is_admin(telegram_user_id):
    return str(telegram_user_id) == str(YOUR_USER_ID)

# ============================================================
# CONVERSATION STATE
# ============================================================
user_states = {}
USER_STATE_LOCK = Lock()


def get_or_create_user_state(user_id):
    with USER_STATE_LOCK:
        if str(user_id) not in user_states:
            user_states[str(user_id)] = {"step": "start", "data": {}}
        return user_states[str(user_id)]


def reset_state(user_id):
    with USER_STATE_LOCK:
        user_states[str(user_id)] = {"step": "start", "data": {}}


# ============================================================
# COWORKER ROUTING — provider-agnostic and expandable
# ============================================================

def choose_coworker(text):
    lower = text.lower()
    keyword_groups = {
        "research": ["research", "sources", "investigate", "study", "market", "competitor", "evidence"],
        "builder": ["build", "code", "website", "app", "software", "api", "prototype", "architecture"],
        "analyst": ["analyze", "analysis", "compare", "strategy", "forecast", "numbers", "data", "risk"],
        "operator": ["execute", "automate", "workflow", "operations", "process", "task", "organize"],
        "educator": ["learn", "teach", "explain", "mentor", "course", "lesson", "understand"],
    }
    scores = {key: 0 for key in keyword_groups}
    for key, words in keyword_groups.items():
        scores[key] = sum(1 for word in words if re.search(rf"\b{re.escape(word)}\b", lower))
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def call_ai_gateway(message, coworker_key, context=None):
    """Optional bridge to Eagle's future AI orchestration layer.

    This service remains independent from Telegram. Any compatible model/agent
    gateway can be connected later through EAGLE_AI_API_URL.
    """
    coworker = COWORKERS.get(coworker_key, COWORKERS["general"])
    payload = {
        "product": PRODUCT_NAME,
        "coworker": {
            "key": coworker_key,
            "name": coworker["name"],
            "mission": coworker["mission"],
        },
        "message": message,
        "context": context or {},
    }

    if not EAGLE_AI_API_URL:
        return None

    headers = {"Content-Type": "application/json"}
    if EAGLE_AI_API_KEY:
        headers["Authorization"] = f"Bearer {EAGLE_AI_API_KEY}"

    response = requests.post(
        EAGLE_AI_API_URL,
        json=payload,
        headers=headers,
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    answer = data.get("answer") or data.get("message") or data.get("text")
    if not answer:
        raise RuntimeError("AI gateway returned no answer")
    return {
        "answer": str(answer),
        "coworker": data.get("coworker", coworker_key),
        "task_id": data.get("task_id"),
    }


def generate_eagle_response(message, coworker_key, visitor_id="web-or-telegram", conversation_id=None):
    try:
        result = call_ai_gateway(message, coworker_key)
        if result:
            return result
    except Exception:
        # Fail safely without exposing internal infrastructure details.
        pass

    coworker = COWORKERS.get(coworker_key, COWORKERS["general"])
    task_id = queue_task(
        visitor_id=visitor_id,
        title="User request awaiting Coworker execution",
        description=message,
        coworker_key=coworker_key,
        conversation_id=conversation_id,
    )
    return {
        "answer": (
            f"🦅 **{coworker['name']}** has been selected for this request.\n\n"
            "Your task has been safely queued in Eagle's independent work layer. "
            "The execution gateway can be connected to your preferred AI models and tools without changing Telegram integration.\n\n"
            f"Task ID: `{task_id}`"
        ),
        "coworker": coworker_key,
        "task_id": task_id,
    }

# ============================================================
# WEB APP — independent front door
# ============================================================
WEB_APP_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Eagle Bot — a globally accessible AI-assisted work platform.">
<title>Eagle Bot</title>
<style>
:root { color-scheme: dark; font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin:0; background:#07110c; color:#ecf7ef; }
.shell { min-height:100vh; display:flex; flex-direction:column; }
header { padding:22px 24px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #183021; position:sticky; top:0; background:rgba(7,17,12,.94); backdrop-filter:blur(12px); z-index:2; }
.brand { display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:.2px; }
.logo { width:40px;height:40px;border-radius:12px;display:grid;place-items:center;background:#12351f;border:1px solid #29593a;font-size:22px; }
main { width:min(1100px, calc(100% - 32px)); margin:32px auto; flex:1; display:grid; grid-template-columns:1fr 1.4fr; gap:22px; }
.card { background:#0b1710; border:1px solid #193323; border-radius:20px; padding:24px; box-shadow:0 18px 55px rgba(0,0,0,.25); }
h1 { font-size:clamp(34px,6vw,68px); line-height:.98; margin:0 0 18px; }
p { color:#b8cabe; line-height:1.6; }
.badges { display:flex; flex-wrap:wrap; gap:8px; margin:18px 0; }
.badge { padding:8px 11px; border-radius:999px; background:#102519; color:#cde9d5; border:1px solid #21472f; font-size:13px; }
.chat { display:flex; flex-direction:column; height:68vh; min-height:480px; }
.messages { flex:1; overflow:auto; padding:6px; }
.msg { margin:10px 0; padding:12px 14px; border-radius:16px; max-width:86%; white-space:pre-wrap; }
.user { margin-left:auto; background:#173a24; }
.bot { background:#101d15; border:1px solid #1d3828; }
.composer { display:flex; gap:10px; margin-top:12px; }
textarea { flex:1; resize:none; min-height:52px; border-radius:14px; border:1px solid #254432; background:#07110c; color:#fff; padding:14px; outline:none; }
button { border:0; border-radius:14px; padding:0 18px; background:#b9ffcf; color:#0a1a0f; font-weight:800; cursor:pointer; }
button:disabled { opacity:.55; cursor:not-allowed; }
.meta { margin-top:18px; font-size:12px; color:#7fa088; }
@media(max-width:820px){ main{grid-template-columns:1fr;} .chat{height:62vh;min-height:420px;} }
</style>
</head>
<body>
<div class="shell">
<header>
  <div class="brand"><div class="logo">🦅</div><div>Eagle Bot</div></div>
  <div style="font-size:12px;color:#86a58f">Independent Web App</div>
</header>
<main>
<section class="card">
  <div class="badges">
    <span class="badge">🌍 Global</span>
    <span class="badge">🧠 Coworkers</span>
    <span class="badge">🔗 Telegram + Web</span>
    <span class="badge">🔐 Product-owned</span>
  </div>
  <h1>Get the work moving.</h1>
  <p>{{ tagline }}</p>
  <p>{{ description }}</p>
  <p>Start with a question, project, research request, build request, learning goal, or operational task. Eagle can route the request toward a specialized Coworker.</p>
  <div class="meta">This web app does not depend on Telegram to function.</div>
</section>
<section class="card chat">
  <div id="messages" class="messages">
    <div class="msg bot">🦅 Welcome to Eagle Bot. Tell me what you are trying to accomplish.</div>
  </div>
  <div class="composer">
    <textarea id="input" placeholder="Describe the task you need help with..."></textarea>
    <button id="send">Send</button>
  </div>
</section>
</main>
</div>
<script>
const visitorKey = localStorage.getItem('eagle_visitor_id') || crypto.randomUUID();
localStorage.setItem('eagle_visitor_id', visitorKey);
let conversationId = localStorage.getItem('eagle_conversation_id') || '';
const input = document.getElementById('input');
const send = document.getElementById('send');
const messages = document.getElementById('messages');
function add(role, text){ const div=document.createElement('div'); div.className='msg '+(role==='user'?'user':'bot'); div.textContent=text; messages.appendChild(div); messages.scrollTop=messages.scrollHeight; }
async function submit(){
  const text=input.value.trim(); if(!text || send.disabled) return;
  add('user', text); input.value=''; send.disabled=true;
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({visitor_id:visitorKey,conversation_id:conversationId,message:text})});
    const data=await r.json();
    if(data.conversation_id){conversationId=data.conversation_id;localStorage.setItem('eagle_conversation_id',conversationId);}
    add('assistant', data.answer || 'Eagle could not produce a response.');
  }catch(e){ add('assistant','Eagle is temporarily unable to complete that request. Please try again.'); }
  finally{send.disabled=false;input.focus();}
}
send.addEventListener('click',submit);
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit();}});
</script>
</body>
</html>
"""


@app.get("/")
def landing():
    return render_template_string(
        WEB_APP_HTML,
        tagline=PRODUCT_TAGLINE,
        description=PRODUCT_DESCRIPTION,
    )


@app.get("/app")
def web_app():
    return render_template_string(
        WEB_APP_HTML,
        tagline=PRODUCT_TAGLINE,
        description=PRODUCT_DESCRIPTION,
    )


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "product": PRODUCT_NAME,
        "telegram_configured": bool(BOT_TOKEN),
        "web_app_configured": bool(PUBLIC_WEB_APP_URL),
        "ai_gateway_configured": bool(EAGLE_AI_API_URL),
        "global_access": True,
        "timestamp": now_iso(),
    })


@app.post("/api/chat")
def api_chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    visitor_id = str(data.get("visitor_id", "")).strip() or str(uuid.uuid4())
    conversation_id = str(data.get("conversation_id", "")).strip()

    if not message:
        return jsonify({"error": "message is required"}), 400
    if len(message) > 12000:
        return jsonify({"error": "message is too long"}), 413

    coworker_key = choose_coworker(message)
    if not conversation_id:
        conversation_id = create_conversation(visitor_id, coworker_key)
    save_message(conversation_id, "user", message, coworker_key)

    result = generate_eagle_response(message, coworker_key, visitor_id, conversation_id)
    save_message(conversation_id, "assistant", result["answer"], coworker_key)

    return jsonify({
        "ok": True,
        "product": PRODUCT_NAME,
        "conversation_id": conversation_id,
        "coworker": {
            "key": coworker_key,
            "name": COWORKERS[coworker_key]["name"],
        },
        "task_id": result.get("task_id"),
        "answer": result["answer"],
    })


@app.post("/api/orders")
def api_create_order():
    data = request.get_json(silent=True) or {}
    name = str(data.get("customer_name", "")).strip()
    email = str(data.get("email", "")).strip()
    topic = str(data.get("topic", "")).strip()
    deadline = str(data.get("deadline", "")).strip()
    words = data.get("word_count")
    visitor_id = str(data.get("visitor_id", "")).strip() or str(uuid.uuid4())

    if not name or not email or not topic or not deadline:
        return jsonify({"error": "customer_name, email, topic and deadline are required"}), 400
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "invalid email address"}), 400
    try:
        words = int(words)
    except (TypeError, ValueError):
        return jsonify({"error": "word_count must be a number"}), 400
    if words <= 0 or words > 1000000:
        return jsonify({"error": "word_count is outside the supported range"}), 400

    order = {
        "customer_name": name,
        "email": email,
        "topic": topic,
        "word_count": words,
        "deadline": deadline,
        "order_number": generate_order_number(),
        "date": utc_date(),
        "status": "Processing",
        "source": "web",
        "telegram_user_id": None,
        "created_at": now_iso(),
    }
    save_order(order)
    queue_task(visitor_id, f"Review {order['order_number']}", topic, "operator")

    return jsonify({"ok": True, "order": order}), 201

# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

def authorize_webhook():
    if not TELEGRAM_WEBHOOK_SECRET:
        return True
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return secrets.compare_digest(supplied, TELEGRAM_WEBHOOK_SECRET)


def handle_document_message(msg):
    file_id = msg["document"]["file_id"]
    user_id = msg["from"]["id"]
    user_name = msg["from"].get("username") or msg["from"].get("first_name", "Unknown")
    text = (
        "📎 **File Received**\n"
        f"From: {user_name}\n"
        f"ID: `{user_id}`\n"
        "Saving to order..."
    )
    send_telegram_message(YOUR_USER_ID, text)
    send_telegram_file(YOUR_USER_ID, file_id)


def handle_telegram_message(msg):
    if not msg.get("from"):
        return

    user_id = msg["from"]["id"]
    user_name = msg["from"].get("username") or msg["from"].get("first_name", "Unknown")

    if "document" in msg:
        handle_document_message(msg)
        return

    text = msg.get("text", "").strip()
    if not text:
        return

    if text == "/start":
        keyboard = telegram_web_buttons()
        welcome = (
            "🦅 **Welcome to Eagle Bot!**\n\n"
            "I am a globally accessible AI-assisted work platform. Telegram is only one doorway — "
            "you can move into Eagle's own web app whenever you want.\n\n"
            "📝 `/new` — Start a new order\n"
            "🔍 `/status EAGLE-XXXXXX` — Check order status\n"
            "📊 `/analytics` — View statistics (Admin only)\n"
            "🕒 `/hours` — Service availability\n"
            "🧠 `/coworkers` — Meet Eagle's Coworkers\n"
            "🌍 `/web` — Open Eagle on the web\n"
            "ℹ️ `/help` — Show this menu\n\n"
            "How can Eagle help you today?"
        )
        send_telegram_message(user_id, welcome, keyboard)
        return

    if text == "/web":
        web_url = eagle_web_url("/app")
        if web_url:
            send_telegram_message(
                user_id,
                f"🌍 **Eagle Bot Web App**\n\nContinue your work outside Telegram:\n{web_url}",
                telegram_web_buttons(),
            )
        else:
            send_telegram_message(
                user_id,
                "⚠️ Eagle's public web address has not been configured yet. The bot is still operational in Telegram.",
            )
        return

    if text == "/coworkers":
        lines = ["🧠 **Eagle Coworkers**\n"]
        for key, coworker in COWORKERS.items():
            lines.append(f"• **{coworker['name']}** — {coworker['mission']}")
        lines.append("\nTell Eagle what you need. The router will select the best starting Coworker.")
        send_telegram_message(user_id, "\n".join(lines), telegram_web_buttons())
        return

    if text == "/help":
        help_text = (
            "📋 **Eagle Bot Commands**\n\n"
            "`/new` — Place a new order\n"
            "`/status EAGLE-XXXXXX` — Check your order\n"
            "`/analytics` — Stats (Admin only)\n"
            "`/hours` — Digital service availability\n"
            "`/coworkers` — View specialized Coworkers\n"
            "`/web` — Open Eagle's own web app\n"
            "`/help` — Show this menu"
        )
        send_telegram_message(user_id, help_text, telegram_web_buttons())
        return

    if text == "/hours":
        send_telegram_message(
            user_id,
            "🟢 **Eagle's digital service is OPEN 24/7 worldwide.**\n\n"
            "Human support and specialist execution can be layered on top without shutting down the platform.",
            telegram_web_buttons(),
        )
        return

    if text.startswith("/status"):
        parts = text.split()
        if len(parts) < 2:
            send_telegram_message(user_id, "⚠️ Please provide an order number. Example: `/status EAGLE-123456`")
            return
        order_num = parts[1].upper()
        order = get_order(order_num)
        if not order:
            send_telegram_message(user_id, f"❌ Order {order_num} not found. Please check and try again.")
            return
        owner = str(order.get("telegram_user_id") or "")
        if not is_admin(user_id) and owner and owner != str(user_id):
            send_telegram_message(user_id, "🔒 That order belongs to another customer account.")
            return
        if not owner and not is_admin(user_id):
            send_telegram_message(user_id, "🔒 This order can only be viewed from its original account or by an administrator.")
            return

        status_text = (
            f"📋 **Order {order_num}**\n\n"
            f"👤 **Customer:** {order['customer_name']}\n"
            f"📧 **Email:** {order['email']}\n"
            f"📝 **Topic:** {order['topic']}\n"
            f"📄 **Words:** {order['word_count']}\n"
            f"📅 **Deadline:** {order['deadline']}\n"
            f"📊 **Status:** {order.get('status', 'Processing')}"
        )
        send_telegram_message(user_id, status_text)
        return

    if text == "/analytics" and is_admin(user_id):
        with DB_LOCK, db() as conn:
            total_orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
            today_orders = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE date = ?", (utc_date(),)
            ).fetchone()["n"]
            open_tasks = conn.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('queued','running')"
            ).fetchone()["n"]
            conversations = conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]

        stats = (
            "📊 **Eagle Bot Analytics**\n\n"
            f"📦 **Total Orders:** {total_orders}\n"
            f"📅 **Today's Orders:** {today_orders}\n"
            f"🧠 **Open Coworker Tasks:** {open_tasks}\n"
            f"💬 **Conversations:** {conversations}\n"
            f"🕒 **Current Time:** {human_time()}\n"
            "🌍 **Digital Availability:** 24/7 Worldwide"
        )
        send_telegram_message(user_id, stats)
        return

    if text == "/analytics" and not is_admin(user_id):
        send_telegram_message(user_id, "🔒 Analytics is available to administrators only.")
        return

    # --------------------------------------------------------
    # Existing order flow, now persisted and Telegram-aware.
    # --------------------------------------------------------
    state = get_or_create_user_state(user_id)

    if state["step"] == "start":
        if text == "/new" or text.lower() == "new order":
            state["step"] = "name"
            state["data"] = {}
            send_telegram_message(
                user_id,
                "📝 **New Order Form**\n\n"
                "Please enter your **full name** to get started 👇\n\n"
                "*(You can say 'cancel' anytime to stop)*",
            )
        else:
            # Eagle can now accept conversational requests directly instead
            # of forcing every interaction into the legacy order form.
            coworker_key = choose_coworker(text)
            conversation_id = create_conversation(str(user_id), coworker_key, user_id)
            save_message(conversation_id, "user", text, coworker_key)
            result = generate_eagle_response(text, coworker_key, str(user_id), conversation_id)
            save_message(conversation_id, "assistant", result["answer"], coworker_key)
            send_telegram_message(user_id, result["answer"], telegram_web_buttons())
        return

    if text.lower() == "cancel":
        reset_state(user_id)
        send_telegram_message(user_id, "✅ Order cancelled. Use `/new` to start again when ready.")
        return

    if state["step"] == "name":
        state["data"]["customer_name"] = text
        state["step"] = "email"
        send_telegram_message(user_id, "📧 **Great!** Now enter your **email address** 👇")
        return

    if state["step"] == "email":
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
            send_telegram_message(user_id, "⚠️ Please enter a valid email address.")
            return
        state["data"]["email"] = text
        state["step"] = "topic"
        send_telegram_message(user_id, "📝 **What topic** do you need help with? 👇")
        return

    if state["step"] == "topic":
        state["data"]["topic"] = text
        state["step"] = "word_count"
        send_telegram_message(user_id, "📄 **How many words** do you need? (Approximately) 👇\n\n*Example: 2000*")
        return

    if state["step"] == "word_count":
        try:
            words = int(text)
            if words <= 0:
                raise ValueError
        except ValueError:
            send_telegram_message(user_id, "⚠️ Please enter a valid number of words. Example: `2000`")
            return
        state["data"]["word_count"] = words
        state["step"] = "deadline"
        send_telegram_message(user_id, "📅 **When is your deadline?** 👇\n\n*Example: Friday, 11:59 PM*")
        return

    if state["step"] == "deadline":
        data = state["data"]
        order = {
            "customer_name": data["customer_name"],
            "email": data["email"],
            "topic": data["topic"],
            "word_count": data["word_count"],
            "deadline": text,
            "order_number": generate_order_number(),
            "date": utc_date(),
            "status": "Processing",
            "source": "telegram",
            "telegram_user_id": str(user_id),
            "created_at": now_iso(),
        }
        save_order(order)
        queue_task(
            visitor_id=str(user_id),
            title=f"Order {order['order_number']}",
            description=order["topic"],
            coworker_key="operator",
        )

        admin_text = (
            f"📨 **NEW ORDER #{order['order_number']}**\n\n"
            f"👤 **Customer:** {order['customer_name']}\n"
            f"📧 **Email:** {order['email']}\n"
            f"📝 **Topic:** {order['topic']}\n"
            f"📄 **Words:** {order['word_count']}\n"
            f"📅 **Deadline:** {order['deadline']}\n"
            f"🕒 **Received:** {human_time()}\n\n"
            "🔍 Use `/analytics` to view stats."
        )
        send_telegram_message(YOUR_USER_ID, admin_text, telegram_web_buttons())

        customer_text = (
            "✅ **ORDER CONFIRMED!**\n\n"
            f"📋 **Order Number:** `{order['order_number']}`\n"
            f"👤 **Name:** {order['customer_name']}\n"
            f"📧 **Email:** {order['email']}\n"
            f"📝 **Topic:** {order['topic']}\n"
            f"📄 **Words:** {order['word_count']}\n"
            f"📅 **Deadline:** {order['deadline']}\n\n"
            "📌 **What's next?**\n"
            "1️⃣ Eagle receives and records the request\n"
            "2️⃣ The task enters the work layer\n"
            "3️⃣ The right Coworker/team can be assigned\n"
            "4️⃣ Work progresses toward the requested outcome\n\n"
            f"🔍 Check status: `/status {order['order_number']}`\n"
            "🌍 Continue outside Telegram using Eagle's web app."
        )
        send_telegram_message(user_id, customer_text, telegram_web_buttons())
        reset_state(user_id)
        return


@app.post("/telegram/webhook")
def telegram_webhook():
    if not authorize_webhook():
        return "forbidden", 403
    try:
        data = request.get_json(silent=True) or {}
        if "message" in data:
            handle_telegram_message(data["message"])
        return "ok", 200
    except Exception as exc:
        # Do not leak stack traces or infrastructure details to Telegram.
        try:
            if BOT_TOKEN and YOUR_USER_ID:
                send_telegram_message(YOUR_USER_ID, "⚠️ Eagle Bot encountered an internal processing error.")
        except Exception:
            pass
        app.logger.exception("Telegram webhook failure: %s", exc)
        return "error", 500


# Backward-compatible endpoint: the original webhook posted to '/'.
@app.post("/")
def legacy_webhook():
    return telegram_webhook()


if __name__ == "__main__":
    # Development fallback only. Use gunicorn/uwsgi or another WSGI server in production.
    app.run(host="0.0.0.0", port=PORT)
