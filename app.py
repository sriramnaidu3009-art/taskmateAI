from __future__ import annotations

import json
import math
import os
import random
import re
from datetime import datetime, timezone

import firebase_admin
import razorpay
import requests
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from firebase_admin import credentials, firestore
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from groq import Groq
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

# 1. Load Environment Variables
load_dotenv()

# 2. Initialize Flask App & Configurations
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "taskmate-demo-change-this-before-deploying")

# Ensure correct HTTPS redirect URIs behind Render/Cloud reverse proxies for Google OAuth
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Groq AI Setup
groq_api_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=groq_api_key) if groq_api_key else None

# SendGrid API Setup
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDER_EMAIL = os.getenv("MAIL_DEFAULT_SENDER", "taskmate26@gmail.com")

# Fast2SMS Setup
FAST2SMS_API_KEY = os.environ.get(
    "FAST2SMS_API_KEY",
    "Bixt0j2lyWXoGT7p9jiDv5BIAMutpQGdNJTdOaXJBShuP4ivXpCBdpQx4uRz",
)

# 3. Configure Database URI & Engine Options BEFORE initializing SQLAlchemy
database_url = os.getenv("DATABASE_URL", "sqlite:///users.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

if "postgres" in database_url and "sslmode" not in database_url:
    database_url += "&sslmode=require" if "?" in database_url else "?sslmode=require"

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    "pool_timeout": 30,
    "pool_size": 5,
    "max_overflow": 5,
}

# 4. Initialize SQLAlchemy & Login Manager
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# 5. Initialize Firebase Admin SDK
base_dir = os.path.dirname(os.path.abspath(__file__))
key_path = os.path.join(base_dir, "firebase-key.json")

db_firestore = None
if os.path.exists(key_path):
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
        db_firestore = firestore.client()
        print("Firebase initialized successfully.")
    except Exception as e:
        print(f"Firebase initialization failed: {e}")
else:
    print("WARNING: firebase-key.json not found in root directory!")

# 6. Initialize Razorpay Client
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_TjmYlCGss5BpcP")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "aSDlOi1qJYvrtMq5HQgjxoCJ")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Active task statuses (before final completion)
ACTIVE_TASK_STATUSES = ("accepted", "Worker On The Way", "Arrived at Location")


# --- Database Models ---
class User(db.Model, UserMixin):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="user")
    skills = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lon = db.Column(db.Float, nullable=True)


class WorkerLocation(db.Model):
    __tablename__ = "worker_location"
    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, default="Service Request")
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lon = db.Column(db.Float, nullable=True)
    budget = db.Column(db.Float, nullable=True, default=450.0)
    status = db.Column(db.String(40), default="pending")
    customer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class WorkerPayout(db.Model):
    __tablename__ = "worker_payouts"
    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, nullable=False)
    task_id = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(32), default="PENDING")
    payout_ref_id = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# 7. Auto-Heal Database Schema & Tables on Startup
with app.app_context():
    try:
        db.create_all()
        if "postgres" in database_url:
            schema_patches = [
                'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION;',
                'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS title VARCHAR(200) DEFAULT \'Service Request\';',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS description TEXT;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS category VARCHAR(100);',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS location VARCHAR(200);',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS budget DOUBLE PRECISION DEFAULT 450.0;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS status VARCHAR(40) DEFAULT \'pending\';',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS customer_id INTEGER;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS worker_id INTEGER;',
                'ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;',
            ]
            for stmt in schema_patches:
                db.session.execute(text(stmt))
            db.session.commit()
        print("[DATABASE MIGRATION]: Schema verified and synced successfully.")
    except Exception as e:
        db.session.rollback()
        print(f"[DATABASE MIGRATION NOTICE]: {e}")


# 8. Initialize Authlib Google OAuth
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        db.session.rollback()
        return None


# --- Compliance Policy Routes ---
@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/refund-policy")
def refund_policy():
    return render_template("refund.html")


# --- Static Fallback Data ---
WORKERS = [
    {
        "id": 1,
        "name": "Arjun Kumar",
        "initials": "AK",
        "role": "Licensed Electrician",
        "skills": ["electrician", "ceiling fan repair", "wiring", "electrical safety"],
        "rating": 4.9,
        "jobs": 128,
        "distance": 1.2,
        "rate": 450,
        "available": True,
        "color": "#1d7a68",
    },
    {
        "id": 2,
        "name": "Priya Sharma",
        "initials": "PS",
        "role": "Home Repair Specialist",
        "skills": ["electrician", "ceiling fan repair", "plumbing", "handyman"],
        "rating": 4.8,
        "jobs": 91,
        "distance": 2.1,
        "rate": 400,
        "available": True,
        "color": "#e16d4c",
    },
    {
        "id": 3,
        "name": "Mohammed Rafi",
        "initials": "MR",
        "role": "Electrician",
        "skills": ["electrician", "wiring", "switch repair", "electrical safety"],
        "rating": 4.7,
        "jobs": 77,
        "distance": 3.4,
        "rate": 380,
        "available": True,
        "color": "#5e63c8",
    },
    {
        "id": 4,
        "name": "Neha Iyer",
        "initials": "NI",
        "role": "Appliance Technician",
        "skills": ["appliance repair", "ceiling fan repair", "electrician"],
        "rating": 4.9,
        "jobs": 63,
        "distance": 4.8,
        "rate": 500,
        "available": False,
        "color": "#a26cba",
    },
]

KEYWORDS = {
    "electrician": ["fan", "electric", "wire", "switch", "light", "power", "socket", "ac", "appliance"],
    "plumber": ["tap", "leak", "pipe", "drain", "water", "toilet", "sink"],
    "cleaner": ["clean", "cleaning", "dust", "wash", "mop"],
    "carpenter": ["wood", "door", "furniture", "shelf", "cabinet", "lock"],
    "caregiver": [
        "grandpa",
        "grandma",
        "elderly",
        "care",
        "look after",
        "patient",
        "nurse",
        "senior",
    ],
}


# --- Helper Functions ---
def finalize_customer_login(user: User):
    """Logs in the customer, links any pre-submitted task to their account, and redirects to /matches if pending."""
    session["user"] = user.email or user.phone
    session["role"] = "user"

    # If the customer submitted a task right before logging in, attach their customer_id to that task in PostgreSQL
    pending_task_id = session.get("task_id") or session.get("task", {}).get("id")
    if pending_task_id and user.id:
        try:
            db_task = db.session.get(Task, int(pending_task_id))
            if db_task and not db_task.customer_id:
                db_task.customer_id = user.id
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[TASK CUSTOMER LINK NOTICE]: {e}")

    # If they clicked "Find my taskmate" before logging in, take them straight to /matches!
    if session.pop("pending_match_redirect", False) and session.get("analysis"):
        return redirect(url_for("matches"))

    return redirect(url_for("home"))


def build_worker_dict(db_worker: User, rate: float = 450.0, distance: float = 1.2) -> dict:
    """Formats a database User object into a complete frontend worker dictionary."""
    name_str = (db_worker.name or "Verified Partner").strip()
    initials = "".join(p[0].upper() for p in name_str.split()[:2]) or "VP"
    raw_skills = [
        s.strip().lower()
        for s in re.split(r"[,&/]+", db_worker.skills or "general service")
        if s.strip()
    ]
    return {
        "id": db_worker.id + 1000,
        "db_id": db_worker.id,
        "name": name_str,
        "initials": initials,
        "role": db_worker.skills or "Verified Service Partner",
        "skills": raw_skills,
        "rating": 4.9,
        "jobs": 15,
        "distance": distance,
        "rate": int(rate or 450),
        "available": True,
        "color": "#0d9488",
        "lat": db_worker.lat,
        "lon": db_worker.lon,
        "phone": db_worker.phone,
        "email": db_worker.email,
    }


def check_user_password(stored_password: str, provided_password: str) -> bool:
    """Safely verifies hashed passwords and falls back gracefully for legacy plaintext accounts."""
    if not stored_password or not provided_password:
        return False
    try:
        if check_password_hash(stored_password, provided_password):
            return True
    except ValueError:
        pass
    return stored_password == provided_password


def safe_user_query(contact_str: str, role_filter: str | None = None):
    """Safely queries User table with automatic reconnect if PostgreSQL drops idle connections."""
    if not contact_str:
        return None
    contact_str = str(contact_str).strip()
    digits = "".join(filter(str.isdigit, contact_str))
    clean_phone = digits[-10:] if len(digits) >= 10 else contact_str

    for attempt in range(2):
        try:
            if "@" in contact_str:
                query = User.query.filter(User.email.ilike(contact_str))
            else:
                query = User.query.filter(
                    (User.phone == clean_phone) | (User.email.ilike(contact_str))
                )
            if role_filter:
                query = query.filter(User.role == role_filter)
            return query.first()
        except (OperationalError, DBAPIError) as e:
            db.session.rollback()
            print(f"[DB RECONNECT ATTEMPT {attempt + 1}]: {e}")
    return None


def calculate_payment_breakdown(base_amount: float):
    platform_fee = round(base_amount * 0.05, 2)
    gst_on_fee = round(platform_fee * 0.18, 2)
    total_amount = round(base_amount + platform_fee + gst_on_fee, 2)
    total_amount_paisa = int(math.ceil(total_amount * 100))

    return {
        "base_amount": base_amount,
        "platform_fee": platform_fee,
        "gst_on_fee": gst_on_fee,
        "total_amount": total_amount,
        "total_amount_paisa": total_amount_paisa,
    }


def calculate_distance(lat1, lon1, lat2, lon2):
    try:
        R = 6371.0
        lat1, lon1, lat2, lon2 = map(
            math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)]
        )
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        return round(R * c, 1)
    except (ValueError, TypeError):
        return 1.5


def generate_otp():
    return str(random.randint(100000, 999999))


def send_realtime_sms(phone_number, otp):
    clean_phone = "".join(filter(str.isdigit, str(phone_number)))[-10:]
    if FAST2SMS_API_KEY and clean_phone:
        url = "https://www.fast2sms.com/dev/bulkV2"
        payload = {
            "route": "otp",
            "variables_values": str(otp),
            "numbers": clean_phone,
        }
        headers = {
            "authorization": FAST2SMS_API_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
            res_data = response.json()
            return res_data.get("return", False)
        except Exception as e:
            print("[FAST2SMS ERROR]", e)
    return False


def send_realtime_email(recipient_email: str, otp: str) -> bool:
    if not SENDGRID_API_KEY or not recipient_email:
        return False

    message = Mail(
        from_email=SENDER_EMAIL,
        to_emails=recipient_email,
        subject="Your Taskmate Verification Code",
        html_content=f"<strong>Your OTP verification code is: {otp}</strong>. Do not share this with anyone.",
    )

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return response.status_code in [200, 201, 202]
    except Exception as e:
        print(f"[SENDGRID EXCEPTION] {e}")
        return False


def analyze_task(title: str, description: str) -> dict:
    prompt = f"""
    You are an autonomous Task Dispatch AI Agent.
    Analyze this service request:
    Title: {title}
    Description: {description}

    Return ONLY a JSON object with no extra text or markdown formatting:
    {{
        "category": "Inferred Category (e.g. Electrical, Plumbing, Cleaning, Carpentry, Caregiver)",
        "skills": ["Skill 1", "Skill 2", "Skill 3"],
        "duration": "2–4 hours",
        "price_min": 400,
        "price_max": 900,
        "urgency": "Standard",
        "summary": "1 sentence task summary",
        "plan": ["step 1", "step 2", "step 3"]
    }}
    """

    if client:
        for model_name in ("openai/gpt-oss-120b", "llama-3.1-8b-instant"):
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You are a JSON-only response generator for task analysis."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                clean_json = response.choices[0].message.content.strip()
                parsed = json.loads(clean_json)
                if isinstance(parsed, dict) and "category" in parsed:
                    if not parsed.get("skills"):
                        parsed["skills"] = [parsed["category"], "General Service"]
                    return parsed
            except Exception as e:
                print(f"[GROQ ANALYZE NOTICE ({model_name})]: {e}")

    text_content = f"{title} {description}".lower()
    category = next(
        (name for name, words in KEYWORDS.items() if any(word in text_content for word in words)),
        "General Service",
    )

    return {
        "category": category.title(),
        "skills": [category.title(), "General Assistance", "Direct Support"],
        "duration": "2–4 hours",
        "price_min": 400,
        "price_max": 1000,
        "urgency": "High"
        if any(w in text_content for w in ["urgent", "asap", "today", "immediately"])
        else "Standard",
        "summary": f"Service request for: {title or 'Home Assistance'}.",
        "plan": [
            "Confirm service specifications and preferred schedule",
            "Match request with a verified local service provider",
            "Coordinate task details upon arrival",
        ],
    }


def ranked_workers(analysis: dict, task_lat=None, task_lon=None) -> list[dict]:
    required = [s.lower() for s in analysis.get("skills", []) if s]
    category = str(analysis.get("category", "")).lower()

    candidate_pool = [w.copy() for w in WORKERS]
    try:
        db_workers = User.query.filter_by(role="worker").all()
        for u in db_workers:
            dist = 1.2
            if task_lat and task_lon and u.lat is not None and u.lon is not None:
                dist = calculate_distance(task_lat, task_lon, u.lat, u.lon)
            candidate_pool.append(build_worker_dict(u, rate=450, distance=dist))
    except Exception as e:
        db.session.rollback()
        print(f"[WORKER POOL QUERY NOTICE]: {e}")

    ranked = []
    for worker in candidate_pool:
        worker_skills = [s.lower() for s in worker.get("skills", [])]
        worker_role = str(worker.get("role", "")).lower()

        skill_matches = sum(
            1 for req in required if any(req in w_s or w_s in req for w_s in worker_skills)
        )
        cat_prefix = category[:4] if len(category) >= 4 else category
        category_match = 1 if (cat_prefix and (cat_prefix in worker_role or worker_role[:4] in category)) else 0

        if skill_matches > 0 or category_match > 0 or not required:
            overlap_score = (skill_matches / max(len(required), 1)) if required else 0.5
            distance_val = float(worker.get("distance", 5.0))
            distance_score = max(0.0, 1.0 - (distance_val / 15.0))
            rating_score = float(worker.get("rating", 4.0)) / 5.0
            availability_score = 1.0 if worker.get("available", True) else 0.1

            score = round(
                (
                    overlap_score * 0.40
                    + category_match * 0.25
                    + distance_score * 0.20
                    + rating_score * 0.10
                    + availability_score * 0.05
                )
                * 100
            )

            item = worker.copy()
            item["score"] = min(max(score, 45), 99)
            item["skill_matches"] = max(skill_matches, 1)
            ranked.append(item)

    ranked.sort(
        key=lambda x: (
            x.get("available", False),
            x.get("score", 0),
            -float(x.get("distance", 99)),
        ),
        reverse=True,
    )
    return ranked


def disburse_worker_payout(worker=None, task_id=1, base_amount=None):
    if not worker:
        worker = session.get("assigned_worker", {})
    if base_amount is None:
        base_amount = float(worker.get("rate", 500))

    worker_id = worker.get("db_id") or worker.get("id", 1)
    worker_fund_account = worker.get("fund_account_id")

    if not worker_fund_account:
        try:
            payout_record = WorkerPayout(
                worker_id=int(worker_id),
                task_id=int(task_id),
                amount=base_amount,
                status="PENDING_BANK_DETAILS",
            )
            db.session.add(payout_record)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[PAYOUT RECORD ERROR]: {e}")
        return {"status": "pending", "message": "Worker bank details missing. Payout queued."}

    RAZORPAYX_URL = "https://api.razorpay.com/v1/payouts"
    payload = {
        "account_number": os.getenv("RAZORPAYX_ACCOUNT_NUMBER", "YOUR_RAZORPAYX_ACCOUNT_NUMBER"),
        "fund_account_id": worker_fund_account,
        "amount": int(base_amount * 100),
        "currency": "INR",
        "mode": "IMPS",
        "purpose": "payout",
        "reference_id": f"task_payout_{task_id}_{worker_id}",
    }

    try:
        response = requests.post(
            RAZORPAYX_URL,
            json=payload,
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=10,
        )
        res_data = response.json()

        if response.status_code in [200, 201]:
            payout_record = WorkerPayout(
                worker_id=int(worker_id),
                task_id=int(task_id),
                amount=base_amount,
                status="PROCESSING",
                payout_ref_id=res_data.get("id"),
            )
            db.session.add(payout_record)
            db.session.commit()
            return {"status": "success", "payout_id": res_data.get("id")}
        else:
            return {"status": "failed", "error": res_data.get("error", {}).get("description")}
    except Exception as e:
        db.session.rollback()
        return {"status": "error", "message": str(e)}


# --- Google OAuth Routes ---
@app.route("/login/google")
def google_login():
    session["oauth_role"] = "user"
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/worker/login/google")
def worker_google_login():
    session["oauth_role"] = "worker"
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")

        if not user_info:
            flash("Google authentication failed.", "error")
            return redirect(url_for("login"))

        email = user_info.get("email")
        name = user_info.get("name", "Taskmate User")
        target_role = session.pop("oauth_role", "user")

        user = User.query.filter(User.email.ilike(email)).first()

        if not user:
            dummy_password = generate_password_hash(os.urandom(24).hex())
            user = User(
                name=name,
                email=email,
                password=dummy_password,
                role=target_role,
                skills="General Service" if target_role == "worker" else None,
            )
            db.session.add(user)
            db.session.commit()
            flash(f"Account created successfully! Welcome, {user.name}.", "success")
        else:
            if target_role == "worker" and user.role != "worker":
                user.role = "worker"
                if not user.skills:
                    user.skills = "General Service"
                db.session.commit()
            flash(f"Welcome back, {user.name}!", "success")

        if target_role == "worker":
            session["user"] = user.email
            session["role"] = "worker"
            return redirect(url_for("worker_dashboard"))

        # Route customer to /matches (if they just submitted a task) or /home
        return finalize_customer_login(user)

    except Exception as e:
        db.session.rollback()
        print(f"[GOOGLE OAUTH ERROR]: {e}")
        flash("An error occurred during Google authentication.", "error")
        return redirect(url_for("login"))


# --- Application Routes ---
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        task_data = {
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "location": request.form.get("location", "").strip(),
            "budget": request.form.get("budget", "").strip(),
            "lat": request.form.get("lat", "").strip(),
            "lon": request.form.get("lon", "").strip(),
        }
        analysis = analyze_task(task_data["title"], task_data["description"])

        try:
            budget_val = float(task_data["budget"]) if task_data["budget"] else 450.0
        except ValueError:
            budget_val = 450.0

        try:
            lat_val = float(task_data["lat"]) if task_data["lat"] else None
            lon_val = float(task_data["lon"]) if task_data["lon"] else None
        except ValueError:
            lat_val, lon_val = None, None

        customer_obj = safe_user_query(session.get("user")) if session.get("user") else None

        try:
            new_task = Task(
                title=task_data["title"] or "Service Request",
                description=task_data["description"] or task_data["title"] or "Home Service Request",
                category=analysis.get("category", "General"),
                location=task_data["location"] or "Exact GPS Location",
                lat=lat_val,
                lon=lon_val,
                budget=budget_val,
                status="pending",
                customer_id=customer_obj.id if customer_obj else None,
            )
            db.session.add(new_task)
            db.session.commit()
            session["task_id"] = new_task.id
            task_data["id"] = new_task.id
        except Exception as e:
            db.session.rollback()
            print(f"[TASK SAVE ERROR]: {e}")

        session["task"] = task_data
        session["analysis"] = analysis

        # If user is not logged in yet, remember that they want to go to /matches right after login
        if not session.get("user"):
            session["pending_match_redirect"] = True
            flash("Please sign in to view your matches and dispatch your worker.", "info")
            return redirect(url_for("login"))

        return redirect(url_for("matches"))
    return render_template("home.html", razorpay_key=RAZORPAY_KEY_ID)


@app.route("/submit", methods=["POST"])
def submit_task():
    description = request.form.get("description", "Service Request").strip()
    lat = request.form.get("lat")
    lon = request.form.get("lon")

    if not lat or not lon:
        flash("Location coordinates are required.", "error")
        return redirect(url_for("home"))

    analysis = analyze_task(description[:80], description)
    try:
        new_task = Task(
            title=description[:80] or "Service Request",
            description=description,
            category=analysis.get("category", "Electrical"),
            lat=float(lat),
            lon=float(lon),
            status="pending",
        )
        db.session.add(new_task)
        db.session.commit()
        session["task_id"] = new_task.id
    except Exception as e:
        db.session.rollback()
        print(f"[SUBMIT TASK ERROR]: {e}")

    return redirect(url_for("worker_dashboard"))


@app.route("/matches")
def matches():
    if not session.get("user"):
        session["pending_match_redirect"] = True
        flash("Please log in to view matches.", "error")
        return redirect(url_for("login"))

    try:
        analysis = session.get("analysis")
        if not analysis:
            return redirect(url_for("home"))

        task_data = session.get("task", {})
        workers = ranked_workers(analysis, task_data.get("lat"), task_data.get("lon"))
        session["current_matches"] = workers

        return render_template(
            "matches.html",
            task=task_data,
            analysis=analysis,
            workers=workers,
        )
    except Exception as e:
        print(f"Error in matches route: {e}")
        return "Internal Server Error. Please try again.", 500


@app.route("/assign/<int:worker_id>", methods=["POST"])
def assign(worker_id: int):
    saved_matches = session.get("current_matches", [])
    worker = next((item for item in saved_matches if item["id"] == worker_id), None)
    if not worker:
        worker = next((item for item in WORKERS if item["id"] == worker_id), None)

    if not session.get("task"):
        flash("Session expired. Please submit your request again.")
        return redirect(url_for("home"))

    if not worker:
        flash("Worker not found. Please try choosing again.")
        return redirect(url_for("matches"))

    task_id = session.get("task_id")
    if task_id:
        try:
            db_task = db.session.get(Task, int(task_id))
            if db_task:
                if worker.get("db_id"):
                    db_task.worker_id = worker["db_id"]
                db_task.status = "accepted"
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[ASSIGN DB UPDATE ERROR]: {e}")

    session["assigned_worker"] = worker
    session["status"] = "Worker On The Way"
    return redirect(url_for("tracker"))


@app.route("/api/task-status-check")
def api_task_status_check():
    task_id = session.get("task_id") or session.get("task", {}).get("id")
    if not task_id:
        return jsonify({"status": "pending"})

    try:
        task = db.session.get(Task, int(task_id))
        if not task:
            return jsonify({"status": "pending"})

        if task.status != "pending":
            if task.worker_id:
                db_worker = db.session.get(User, task.worker_id)
                if db_worker:
                    dist = 1.2
                    if (
                        task.lat is not None
                        and task.lon is not None
                        and db_worker.lat is not None
                        and db_worker.lon is not None
                    ):
                        dist = calculate_distance(task.lat, task.lon, db_worker.lat, db_worker.lon)
                    session["assigned_worker"] = build_worker_dict(
                        db_worker, rate=task.budget or 450, distance=dist
                    )

            if task.status == "completed":
                current_stage = "Job Completed"
            elif task.status in ("Worker On The Way", "Arrived at Location"):
                current_stage = task.status
            else:
                current_stage = "Worker On The Way"

            session["status"] = current_stage

            # Redirect directly to the unified /tracker page (which includes checkout)
            return jsonify(
                {
                    "status": "accepted" if task.status in ACTIVE_TASK_STATUSES else task.status,
                    "current_stage": current_stage,
                    "redirect_url": url_for("tracker"),
                }
            )
    except (OperationalError, DBAPIError) as e:
        db.session.rollback()
        print(f"[STATUS CHECK DB RECONNECT]: {e}")
    except Exception as e:
        db.session.rollback()
        print(f"[STATUS CHECK ERROR]: {e}")

    return jsonify({"status": "pending"})


@app.route("/api/worker/accept-task/<int:task_id>", methods=["POST"])
def worker_accept_task(task_id: int):
    user_key = session.get("user")
    if not user_key or session.get("role") != "worker":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    user_obj = safe_user_query(user_key, role_filter="worker")
    if not user_obj:
        return jsonify({"status": "error", "message": "Worker profile not found."}), 404

    try:
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"status": "error", "message": "Task not found."}), 404

        if task.status != "pending":
            return jsonify({"status": "error", "message": "Task is no longer available."}), 400

        task.status = "accepted"
        task.worker_id = user_obj.id
        db.session.commit()
        session["status"] = "Worker On The Way"

        return jsonify(
            {
                "status": "success",
                "message": f"Task #{task_id} accepted successfully!",
                "redirect": url_for("worker_dashboard"),
            }
        )
    except Exception as e:
        db.session.rollback()
        print(f"[ACCEPT TASK ERROR]: {e}")
        return jsonify({"status": "error", "message": "Database error occurred."}), 500


@app.route("/api/worker/update-status", methods=["POST"])
def worker_update_status():
    user_key = session.get("user")
    if not user_key or session.get("role") != "worker":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json() or {}
    new_status = str(data.get("status", "")).strip()
    if not new_status:
        return jsonify({"status": "error", "message": "Invalid status payload"}), 400

    session["status"] = new_status

    user_obj = safe_user_query(user_key, role_filter="worker")
    if user_obj:
        try:
            active_task = (
                Task.query.filter(
                    Task.worker_id == user_obj.id,
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                )
                .order_by(Task.created_at.desc())
                .first()
            )
            if active_task:
                if "complete" in new_status.lower():
                    active_task.status = "completed"
                else:
                    active_task.status = new_status[:40]
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[STATUS SYNC ERROR]: {e}")

    return jsonify({"status": "success", "current_status": new_status})


@app.route("/tracker", methods=["GET", "POST"])
def tracker():
    task = session.get(
        "task",
        {
            "id": 1,
            "title": "Fix Ceiling Fan",
            "description": "Fan is making noise and stopped spinning.",
            "location": "Meerpet, Hyderabad",
            "budget": "450",
            "lat": 17.3297,
            "lon": 78.5276,
        },
    )
    worker = session.get("assigned_worker", WORKERS[0])

    if request.method == "POST":
        session["status"] = request.form.get("status", session.get("status", "Worker On The Way"))

    status = session.get("status", "Worker On The Way")
    task_id = session.get("task_id") or task.get("id", 1)

    try:
        base_price = float(task.get("budget") or worker.get("rate") or 450.0)
    except (ValueError, TypeError):
        base_price = 450.0

    # Sync live task details, coordinates, budget, and worker info from PostgreSQL
    if task_id:
        try:
            db_task = db.session.get(Task, int(task_id))
            if db_task:
                task["id"] = db_task.id
                task["title"] = db_task.title or task.get("title", "Service Request")
                task["description"] = db_task.description or task.get("description", "")
                task["location"] = db_task.location or task.get("location", "Exact GPS Location")

                if db_task.budget:
                    base_price = float(db_task.budget)
                    task["budget"] = str(int(base_price))

                if db_task.lat is not None and db_task.lon is not None:
                    task["lat"] = db_task.lat
                    task["lon"] = db_task.lon

                if db_task.worker_id:
                    db_worker = db.session.get(User, db_task.worker_id)
                    if db_worker:
                        dist = 1.2
                        if (
                            db_task.lat is not None
                            and db_task.lon is not None
                            and db_worker.lat is not None
                            and db_worker.lon is not None
                        ):
                            dist = calculate_distance(
                                db_task.lat, db_task.lon, db_worker.lat, db_worker.lon
                            )
                        worker = build_worker_dict(
                            db_worker, rate=base_price, distance=dist
                        )
                        session["assigned_worker"] = worker

                if db_task.status == "completed":
                    status = "Job Completed"
                elif db_task.status in ("Worker On The Way", "Arrived at Location"):
                    status = db_task.status
                elif db_task.status == "accepted":
                    status = "Worker On The Way"

                session["task"] = task
                session["status"] = status
        except Exception as e:
            db.session.rollback()
            print(f"[TRACKER DB SYNC NOTICE]: {e}")

    # Generate Razorpay Order & Payment Breakdown directly for the Tracker view
    pricing = calculate_payment_breakdown(base_price)
    order_data = {
        "amount": pricing["total_amount_paisa"],
        "currency": "INR",
        "receipt": f"task_receipt_{task_id}",
        "payment_capture": 1,
    }

    try:
        razorpay_order = razorpay_client.order.create(data=order_data)
        order_id = razorpay_order["id"]
    except Exception as e:
        print(f"[RAZORPAY ERROR] {e}")
        order_id = "mock_order_id_for_testing"

    return render_template(
        "tracker.html",
        task=task,
        task_id=task_id,
        worker=worker,
        status=status,
        pricing=pricing,
        order_id=order_id,
        razorpay_key=RAZORPAY_KEY_ID,
        razorpay_key_id=RAZORPAY_KEY_ID,
    )


@app.route("/api/create-order", methods=["POST"])
def create_payment_order():
    data = request.get_json() or {}

    try:
        base_price = float(data.get("amount", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid price"}), 400

    task_title = data.get("title", "Custom Service")

    if base_price <= 0:
        return jsonify({"error": "Amount must be greater than zero"}), 400

    pricing = calculate_payment_breakdown(base_price)

    order_data = {
        "amount": pricing["total_amount_paisa"],
        "currency": "INR",
        "receipt": f"order_rcpt_{int(base_price)}",
        "payment_capture": 1,
    }

    try:
        razorpay_order = razorpay_client.order.create(data=order_data)
        order_id = razorpay_order["id"]
    except Exception as e:
        print(f"[RAZORPAY ERROR] {e}")
        order_id = "mock_order_id"

    return jsonify(
        {
            "order_id": order_id,
            "pricing": pricing,
            "title": task_title,
            "key": RAZORPAY_KEY_ID,
        }
    )


@app.route("/create-checkout/<int:task_id>", methods=["GET", "POST"])
def create_checkout(task_id):
    assigned_worker = session.get("assigned_worker", {})
    base_price = float(assigned_worker.get("rate", 450.0))

    try:
        db_task = db.session.get(Task, int(task_id))
        if db_task:
            if db_task.budget:
                base_price = float(db_task.budget)
            if db_task.worker_id and not assigned_worker.get("db_id"):
                db_worker = db.session.get(User, db_task.worker_id)
                if db_worker:
                    assigned_worker = build_worker_dict(db_worker, rate=base_price)
                    session["assigned_worker"] = assigned_worker
    except Exception as e:
        db.session.rollback()
        print(f"[CHECKOUT TASK FETCH NOTICE]: {e}")

    pricing = calculate_payment_breakdown(base_price)

    order_data = {
        "amount": pricing["total_amount_paisa"],
        "currency": "INR",
        "receipt": f"task_receipt_{task_id}",
        "payment_capture": 1,
    }

    try:
        razorpay_order = razorpay_client.order.create(data=order_data)
        order_id = razorpay_order["id"]
    except Exception as e:
        print(f"[RAZORPAY ERROR] {e}")
        order_id = "mock_order_id_for_testing"

    return render_template(
        "checkout.html",
        pricing=pricing,
        order_id=order_id,
        razorpay_key_id=RAZORPAY_KEY_ID,
        task_id=task_id,
        worker=assigned_worker,
    )


@app.route("/verify-payment", methods=["POST"])
def verify_payment():
    data = request.get_json() or {}
    razorpay_payment_id = data.get("razorpay_payment_id")
    task_id = data.get("task_id") or session.get("task_id", 1)

    assigned_worker = session.get("assigned_worker", {})
    base_amount = float(assigned_worker.get("rate", 450.0))

    if task_id:
        try:
            db_task = db.session.get(Task, int(task_id))
            if db_task:
                if db_task.budget:
                    base_amount = float(db_task.budget)
                db_task.status = "completed"
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[PAYMENT TASK COMPLETE ERROR]: {e}")

    payout_result = disburse_worker_payout(
        worker=assigned_worker, task_id=int(task_id), base_amount=base_amount
    )

    return jsonify(
        {
            "status": "success",
            "payment_id": razorpay_payment_id,
            "payout_status": payout_result,
        }
    )


@app.route("/api/worker/update-location", methods=["POST"])
def update_worker_location():
    try:
        data = request.get_json() or {}
        user_key = session.get("user")
        lat = data.get("lat")
        lon = data.get("lon")

        if not user_key or lat is None or lon is None:
            return jsonify({"error": "Missing coordinates or authentication"}), 400

        user_obj = safe_user_query(user_key)
        if not user_obj:
            return jsonify({"error": "Worker profile not found"}), 404

        user_obj.lat = float(lat)
        user_obj.lon = float(lon)
        db.session.commit()

        return jsonify({"status": "success", "lat": user_obj.lat, "lon": user_obj.lon})

    except Exception as e:
        db.session.rollback()
        print(f"[LOCATION UPDATE ERROR]: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/worker/live-location")
def get_live_location():
    assigned_worker = session.get("assigned_worker")
    if not assigned_worker:
        return jsonify({"error": "No worker assigned"}), 404

    worker_db_id = assigned_worker.get("db_id")
    worker_user = None
    if worker_db_id:
        try:
            worker_user = db.session.get(User, int(worker_db_id))
        except Exception:
            db.session.rollback()

    lat = (
        worker_user.lat
        if (worker_user and worker_user.lat is not None)
        else assigned_worker.get("lat", 17.3850)
    )
    lon = (
        worker_user.lon
        if (worker_user and worker_user.lon is not None)
        else assigned_worker.get("lon", 78.4867)
    )

    task_info = session.get("task", {})
    distance = assigned_worker.get("distance", "1.5")

    if task_info.get("lat") and task_info.get("lon"):
        distance = calculate_distance(task_info.get("lat"), task_info.get("lon"), lat, lon)

    return jsonify({"lat": lat, "lon": lon, "distance": distance})


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        if not client:
            return (
                jsonify(
                    {"response": "API key missing. Please check GROQ_API_KEY in your .env file."}
                ),
                400,
            )

        data = request.get_json() or {}
        prompt = data.get("prompt", "").strip()

        if not prompt:
            return jsonify({"response": "Please ask a question."}), 400

        system_instructions = """
        You are the official AI Assistant for Taskmate, a neighbourhood home services platform.
        
        FOUNDER & CREATOR INFORMATION:
        - Founder / Creator: Sriram
        - About the Founder: Taskmate was envisioned and built by Sriram to empower local service providers and simplify home assistance for everyday users. He is a 17-year-old student and self-taught programmer who developed TaskmateAI single-handedly.
        - Vision: To empower local service providers and bridge the gap between skilled workers and those in need of their services.
        
        PLATFORM DETAILS:
        - Services: Electricians, Plumbers, Cleaners, Carpenters, and Caregivers.
        - Pricing: 5% platform fee + 18% GST on the fee, calculated transparently via Razorpay.
        - Live Tracking: Real-time map tracking powered by MapLibre GL and Google Maps navigation.
        
        INSTRUCTIONS:
        - If a user asks who founded, built, or created Taskmate, enthusiastically introduce Sriram as the founder!
        - Keep answers concise, helpful, and friendly.
        """

        last_err = None
        for model_name in ("openai/gpt-oss-120b", "llama-3.1-8b-instant"):
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_instructions},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7,
                )
                return jsonify({"response": response.choices[0].message.content})
            except Exception as e:
                last_err = e
                print(f"[GROQ CHAT NOTICE ({model_name})]: {e}")

        print(f"[GROQ CHAT ERROR DETAILS]: {last_err}")
        return jsonify(
            {
                "response": "I'm having trouble connecting to Groq AI right now. Check your terminal logs for details!"
            }
        )

    except Exception as e:
        print(f"[CHAT ROUTE CRASH]: {e}")
        return jsonify({"response": "Internal server error occurred."}), 500


@app.route("/api/test-db", methods=["GET", "POST"])
def api_test_db():
    try:
        if not db_firestore:
            return jsonify({"error": "Firebase instance not initialized."}), 500
        doc_ref = db_firestore.collection("users").document("test_user")
        doc_ref.set({"status": "Firebase connected successfully!"})
        return jsonify({"message": "Data written to Firestore successfully!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or request.form.get("name") or "Customer").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()

        if not (email or phone) or not password:
            flash("Please provide an email or phone number and password.", "error")
            return redirect(url_for("register"))

        digits = "".join(filter(str.isdigit, phone))
        clean_phone = digits[-10:] if len(digits) >= 10 else None
        email_val = email if email else None

        filters = []
        if email_val:
            filters.append(User.email.ilike(email_val))
        if clean_phone:
            filters.append(User.phone == clean_phone)

        existing_user = None
        if filters:
            try:
                existing_user = User.query.filter(db.or_(*filters)).first()
            except (OperationalError, DBAPIError):
                db.session.rollback()
                existing_user = User.query.filter(db.or_(*filters)).first()

        if existing_user:
            flash("Email or Phone Number is already registered.", "error")
            return redirect(url_for("register"))

        otp = generate_otp()
        if clean_phone:
            send_realtime_sms(clean_phone, otp)
        if email_val:
            send_realtime_email(email_val, otp)

        session["pending_user"] = {
            "name": username,
            "email": email_val,
            "phone": clean_phone,
            "password": generate_password_hash(password),
            "role": "user",
            "otp": otp,
        }
        return redirect(url_for("verify"))
    return render_template("register.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    pending_user = session.get("pending_user")
    if not pending_user:
        flash("No pending registration session found.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        user_otp = request.form.get("otp", "").strip()

        if not (user_otp and pending_user.get("otp") and user_otp == pending_user["otp"]):
            flash("Invalid OTP code. Try again.", "error")
            return render_template("verify.html")

        email_val = pending_user.get("email") or None
        phone_val = pending_user.get("phone") or None

        filters = []
        if email_val:
            filters.append(User.email.ilike(email_val))
        if phone_val:
            filters.append(User.phone == phone_val)

        try:
            user = User.query.filter(db.or_(*filters)).first() if filters else None

            if not user:
                user = User(
                    name=pending_user.get("name") or "User",
                    email=email_val,
                    phone=phone_val,
                    password=pending_user["password"],
                    role=pending_user.get("role", "user"),
                    skills=pending_user.get("skills") or None,
                    description=pending_user.get("description") or None,
                )
                db.session.add(user)
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"[VERIFY USER SAVE ERROR]: {e}")
            flash("Error saving account. Please try logging in.", "error")
            return redirect(url_for("login"))

        target_role = pending_user.get("role", "user")
        session.pop("pending_user", None)

        flash("Account verified and saved successfully!", "success")
        if target_role == "worker":
            session["user"] = email_val or phone_val
            session["role"] = "worker"
            return redirect(url_for("worker_dashboard"))

        return finalize_customer_login(user)

    return render_template("verify.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_input = request.form.get("login_input", "").strip()
        password = request.form.get("password", "").strip()

        user = safe_user_query(login_input)

        if not user:
            flash("User not found.", "error")
        elif not check_user_password(user.password, password):
            flash("Incorrect password", "error")
        else:
            flash("Logged in successfully!", "success")
            return finalize_customer_login(user)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    logout_user()
    return redirect(url_for("home"))


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("home"))


# --- Worker Application Routes ---
@app.route("/worker/register", methods=["GET", "POST"])
def worker_register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact = request.form.get("contact", "").strip()
        password = request.form.get("password", "").strip()
        skills = request.form.get("skills", "").strip()
        description = request.form.get("description", "").strip()

        if not contact or not password:
            flash("Contact and password are required.", "error")
            return redirect(url_for("worker_register"))

        otp = generate_otp()
        is_email = "@" in contact
        digits = "".join(filter(str.isdigit, contact))
        clean_phone = digits[-10:] if (not is_email and len(digits) >= 10) else None

        session["pending_user"] = {
            "name": name or "Service Partner",
            "contact": contact,
            "email": contact if is_email else None,
            "phone": clean_phone,
            "password": generate_password_hash(password),
            "skills": skills,
            "description": description,
            "role": "worker",
            "otp": otp,
        }

        if is_email:
            send_realtime_email(contact, otp)
        else:
            send_realtime_sms(contact, otp)

        return redirect(url_for("verify"))
    return render_template("worker_register.html")


@app.route("/worker/login", methods=["GET", "POST"])
def worker_login():
    if request.method == "POST":
        contact = request.form.get("contact", "").strip()
        password = request.form.get("password", "").strip()

        user = safe_user_query(contact, role_filter="worker")

        if not user:
            flash("User not found.", "error")
        elif not check_user_password(user.password, password):
            flash("Incorrect password", "error")
        else:
            session["user"] = user.email or user.phone
            session["role"] = "worker"
            flash("Worker logged in successfully!", "success")
            return redirect(url_for("worker_dashboard"))

    return render_template("worker_login.html")


@app.route("/worker/dashboard")
def worker_dashboard():
    user_key = session.get("user")
    if not user_key or session.get("role") != "worker":
        flash("Please log in as a worker to access this page.", "error")
        return redirect(url_for("worker_login"))

    user_obj = safe_user_query(user_key, role_filter="worker")

    worker_profile = {
        "id": user_obj.id if user_obj else None,
        "name": user_obj.name if user_obj else "Worker",
        "skills": user_obj.skills if user_obj else "General Service",
        "description": user_obj.description if user_obj else "No service bio provided.",
        "lat": user_obj.lat if user_obj else None,
        "lon": user_obj.lon if user_obj else None,
    }

    pending_tasks = []
    active_job = None
    customer_contact_info = None

    try:
        if user_obj and user_obj.skills:
            skill_tokens = [
                tok.lower()
                for tok in re.split(r"[,&/\s]+", user_obj.skills)
                if len(tok) >= 3
            ]
            primary_keyword = skill_tokens[0][:5] if skill_tokens else ""
            if primary_keyword:
                pending_tasks = (
                    Task.query.filter(
                        Task.status == "pending",
                        Task.category.ilike(f"%{primary_keyword}%"),
                    )
                    .order_by(Task.created_at.desc())
                    .all()
                )

        if not pending_tasks:
            pending_tasks = (
                Task.query.filter_by(status="pending")
                .order_by(Task.created_at.desc())
                .all()
            )

        if user_obj and user_obj.lat is not None and user_obj.lon is not None:
            for t in pending_tasks:
                if t.lat is not None and t.lon is not None:
                    t.distance_km = calculate_distance(user_obj.lat, user_obj.lon, t.lat, t.lon)
                else:
                    t.distance_km = None
            pending_tasks.sort(key=lambda x: x.distance_km if x.distance_km is not None else 9999.0)

        if user_obj:
            active_job = (
                Task.query.filter(
                    Task.worker_id == user_obj.id,
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                )
                .order_by(Task.created_at.desc())
                .first()
            )
            if active_job and active_job.customer_id:
                cust = db.session.get(User, active_job.customer_id)
                if cust:
                    customer_contact_info = {
                        "name": cust.name or "Customer",
                        "phone": cust.phone or "Not provided",
                        "email": cust.email or "Not provided",
                    }
    except Exception as e:
        db.session.rollback()
        print(f"[DASHBOARD TASK QUERY ERROR]: {e}")

    if active_job:
        current_status = (
            "Worker On The Way" if active_job.status == "accepted" else active_job.status
        )
    else:
        current_status = "Looking for tasks"

    return render_template(
        "worker_dashboard.html",
        worker=worker_profile,
        tasks=pending_tasks,
        active_job=active_job,
        current_status=current_status,
        customer_contact=customer_contact_info,
        customer_contact_info=customer_contact_info,
    )


# --- Safe Production Entry Point ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)