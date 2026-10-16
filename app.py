from __future__ import annotations

import httpx
import json
import math
import os
import random
import time
import firebase_admin
import razorpay
import requests
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv
from firebase_admin import auth as firebase_auth
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
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from google import genai
from google.genai import types
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timezone

# 1. Load Environment Variables
load_dotenv()

# 2. Initialize Flask App & Configurations
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "taskmate-demo-change-this-before-deploying")

# Gemini Setup
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

# SendGrid API Setup
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDER_EMAIL = os.getenv("MAIL_DEFAULT_SENDER", "taskmate26@gmail.com")

# Fast2SMS Setup
FAST2SMS_API_KEY = os.environ.get(
    "FAST2SMS_API_KEY",
    "Bixt0j2lyWXoGT7p9jiDv5BIAMutpQGdNJTdOaXJBShuP4ivXpCBdpQx4uRz",
)

# Database Setup (External/Render Postgres or local SQLite fallback)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///users.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Automatically enforce SSL mode for external PostgreSQL (Render, Supabase, etc.)
if "postgres" in DATABASE_URL and "sslmode" not in DATABASE_URL:
    if "?" in DATABASE_URL:
        DATABASE_URL += "&sslmode=require"
    else:
        DATABASE_URL += "?sslmode=require"

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 3. Initialize Firebase Admin SDK
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

# 4. Initialize SQLAlchemy & Flask-Login
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# 5. Initialize Razorpay Client
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_TjmYlCGss5BpcP")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "aSDlOi1qJYvrtMq5HQgjxoCJ")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# --- Database Models ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="user")
    skills = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)


class WorkerLocation(db.Model):
    __tablename__ = 'worker_locations'
    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, unique=True, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkerPayout(db.Model):
    __tablename__ = 'worker_payouts'
    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, nullable=False)
    task_id = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(32), default='PENDING')
    payout_ref_id = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

#privacy policy, terms, refund policy routes
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/refund-policy")
def refund_policy():
    return render_template("refund.html")


# --- Static Data ---
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
    "electrician": ["fan", "electric", "wire", "switch", "light", "power", "socket"],
    "plumber": ["tap", "leak", "pipe", "drain", "water", "toilet"],
    "cleaner": ["clean", "cleaning", "dust", "wash"],
    "carpenter": ["wood", "door", "furniture", "shelf", "cabinet"],
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
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.asin(math.sqrt(a))
    return round(R * c, 1)


def generate_otp():
    return str(random.randint(100000, 999999))


def send_realtime_sms(phone_number, otp):
    clean_phone = "".join(filter(str.isdigit, str(phone_number)))[-10:]
    if FAST2SMS_API_KEY:
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
    if not SENDGRID_API_KEY:
        return False

    message = Mail(
        from_email=SENDER_EMAIL,
        to_emails=recipient_email,
        subject="Your Registration Verification Code",
        html_content=f"<strong>Your OTP verification code is: {otp}</strong>. Do not share this with anyone."
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
        "category": "Inferred Category (e.g. Caregiver / Senior Care, Electrical, Plumbing, etc.)",
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
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
            )
            clean_json = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)
        except Exception as e:
            print(f"[GEMINI API FALLBACK ACTIVE]: {e}")

    text = f"{title} {description}".lower()
    category = next(
        (name for name, words in KEYWORDS.items() if any(word in text for word in words)),
        "General Service",
    )

    return {
        "category": category.title(),
        "skills": [category.title(), "General Assistance", "Direct Support"],
        "duration": "2–4 hours",
        "price_min": 400,
        "price_max": 1000,
        "urgency": "High" if any(w in text for w in ["urgent", "asap", "today", "immediately"]) else "Standard",
        "summary": f"Service request for: {title or 'Home Assistance'}.",
        "plan": [
            "Confirm service specifications and preferred schedule",
            "Match request with a verified local service provider",
            "Coordinate task details upon arrival",
        ],
    }


def ranked_workers(analysis: dict) -> list[dict]:
    required = [s.lower() for s in analysis.get("skills", [])]
    category = str(analysis.get("category", "")).lower()
    ranked = []

    for worker in WORKERS:
        worker_skills = [s.lower() for s in worker.get("skills", [])]
        worker_role = str(worker.get("role", "")).lower()

        skill_matches = sum(
            1 for req in required if any(req in w_s or w_s in req for w_s in worker_skills)
        )
        category_match = 1 if (category in worker_role or worker_role in category) else 0

        if skill_matches > 0 or category_match > 0 or not required:
            overlap_score = (skill_matches / len(required)) if required else 0.5
            distance_val = worker.get("distance", 5.0)
            distance_score = max(0, 1 - (distance_val / 10.0))
            rating_score = worker.get("rating", 4.0) / 5.0
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
            item["score"] = max(score, 40)
            item["skill_matches"] = skill_matches
            ranked.append(item)

    ranked.sort(
        key=lambda x: (
            x.get("available", False),
            x.get("score", 0),
            -x.get("distance", 99)
        ), 
        reverse=True
    )
    return ranked


def disburse_worker_payout(worker=None, task_id=1, base_amount=None):
    if not worker:
        worker = session.get("assigned_worker", {})
    if base_amount is None:
        base_amount = float(worker.get("rate", 500))
        
    worker_id = worker.get("id", 1)
    worker_fund_account = worker.get("fund_account_id")
    
    if not worker_fund_account:
        payout_record = WorkerPayout(
            worker_id=worker_id,
            task_id=task_id,
            amount=base_amount,
            status='PENDING_BANK_DETAILS'
        )
        db.session.add(payout_record)
        db.session.commit()
        return {"status": "pending", "message": "Worker bank details missing. Payout queued."}

    RAZORPAYX_URL = "https://api.razorpay.com/v1/payouts"
    payload = {
        "account_number": "YOUR_RAZORPAYX_ACCOUNT_NUMBER",
        "fund_account_id": worker_fund_account,
        "amount": int(base_amount * 100),
        "currency": "INR",
        "mode": "IMPS",
        "purpose": "payout",
        "reference_id": f"task_payout_{task_id}_{worker_id}"
    }

    try:
        response = requests.post(
            RAZORPAYX_URL, 
            json=payload, 
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
        )
        res_data = response.json()

        if response.status_code in [200, 201]:
            payout_record = WorkerPayout(
                worker_id=worker_id,
                task_id=task_id,
                amount=base_amount,
                status='PROCESSING',
                payout_ref_id=res_data.get('id')
            )
            db.session.add(payout_record)
            db.session.commit()
            return {"status": "success", "payout_id": res_data.get('id')}
        else:
            return {"status": "failed", "error": res_data.get('error', {}).get('description')}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- Application Routes ---

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        task = {
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "location": request.form.get("location", "").strip(),
            "budget": request.form.get("budget", "").strip(),
            "lat": request.form.get("lat", "").strip(),
            "lon": request.form.get("lon", "").strip(),
        }
        analysis = analyze_task(task["title"], task["description"])
        session["task"] = task
        session["analysis"] = analysis
        return redirect(url_for("matches"))
    return render_template("home.html", razorpay_key=RAZORPAY_KEY_ID)


@app.route('/submit', methods=['POST'])
def submit_task():
    if not session.get("user"):
        flash("Please log in or register to find your taskmate.", "error")
        return redirect(url_for('login'))

    task_title = request.form.get('task')
    task_desc = request.form.get('description')
    location = request.form.get('location')
    
    session['task'] = {
        'title': task_title, 
        'description': task_desc, 
        'location': location
    }
    session['analysis'] = {
        'location': location,
        'query': task_title
    }
    return redirect(url_for('matches'))


@app.route("/matches")
def matches():
    if not session.get("user"):
        flash("Please log in to view matches.", "error")
        return redirect(url_for("login"))

    try:
        analysis = session.get("analysis")
        if not analysis:
            return redirect(url_for("home"))

        workers = ranked_workers(analysis)
        session["current_matches_ids"] = [w.get("id") for w in workers if isinstance(w, dict)]

        return render_template(
            "matches.html",
            task=session.get("task", {}),
            analysis=analysis,
            workers=workers,
        )
    except Exception as e:
        print(f"Error in matches route: {e}")
        return "Internal Server Error. Please try again.", 500


@app.route("/assign/<int:worker_id>", methods=["POST"])
def assign(worker_id: int):
    worker = next((item for item in WORKERS if item["id"] == worker_id), None)
    if not worker:
        saved_matches = session.get("current_matches", [])
        worker = next((item for item in saved_matches if item["id"] == worker_id), None)

    if not session.get("task"):
        flash("Session expired. Please submit your request again.")
        return redirect(url_for("home"))

    if not worker:
        flash("Worker not found. Please try choosing again.")
        return redirect(url_for("matches"))

    session["assigned_worker"] = worker
    session["status"] = "Assigned"
    return redirect(url_for("tracker"))


@app.route("/api/worker/accept-task/<int:task_id>", methods=["POST"])
def worker_accept_task(task_id: int):
    user_key = session.get("user")
    if not user_key or session.get("role") != "worker":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    if "task" not in session:
        session["task"] = {
            "id": task_id,
            "title": "Fix Ceiling Fan",
            "description": "Fan making high noise and stopped spinning.",
            "location": "Meerpet, Hyderabad",
            "budget": "450",
        }

    session["assigned_worker"] = user_key
    session["status"] = "Worker On The Way"

    return jsonify(
        {
            "status": "success",
            "message": f"Task #{task_id} accepted!",
            "redirect": url_for("worker_dashboard"),
        }
    )


@app.route("/api/worker/update-status", methods=["POST"])
def worker_update_status():
    if not session.get("user") or session.get("role") != "worker":
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json() or {}
    new_status = data.get("status")
    if new_status:
        session["status"] = new_status
        return jsonify({"status": "success", "current_status": session["status"]})

    return jsonify({"status": "error", "message": "Invalid status payload"}), 400


@app.route("/tracker", methods=["GET", "POST"])
def tracker():
    task = session.get(
        "task",
        {
            "title": "Fix Ceiling Fan",
            "description": "Fan is making noise and stopped spinning.",
            "location": "Meerpet, Hyderabad",
            "budget": "450",
        },
    )
    worker = session.get("assigned_worker", WORKERS[0])

    if request.method == "POST":
        session["status"] = request.form.get("status", session.get("status", "Worker On The Way"))

    status = session.get("status", "Worker On The Way")
    return render_template(
        "tracker.html",
        task=task,
        worker=worker,
        status=status,
        razorpay_key=RAZORPAY_KEY_ID,
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
            "key": os.environ.get("RAZORPAY_KEY_ID", ""),
        }
    )


@app.route("/create-checkout/<int:task_id>", methods=["GET", "POST"])
def create_checkout(task_id):
    assigned_worker = session.get("assigned_worker", {})
    base_price = float(assigned_worker.get("rate", 500.0))
    
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
    )


@app.route("/verify-payment", methods=["POST"])
def verify_payment():
    data = request.get_json() or {}
    razorpay_payment_id = data.get('razorpay_payment_id')
    
    assigned_worker = session.get("assigned_worker", {})
    base_amount = float(assigned_worker.get("rate", 500))

    payout_result = disburse_worker_payout(worker=assigned_worker, task_id=1, base_amount=base_amount)

    return jsonify({
        "status": "success", 
        "payment_id": razorpay_payment_id,
        "payout_status": payout_result
    })


@app.route("/api/worker/update-location", methods=["POST"])
def update_worker_location():
    data = request.get_json() or {}
    worker_id = session.get("user_id")
    lat = data.get("lat")
    lon = data.get("lon")

    if not worker_id or lat is None or lon is None:
        return jsonify({"error": "Missing coordinates or authentication"}), 400

    loc_record = WorkerLocation.query.filter_by(worker_id=worker_id).first()
    if not loc_record:
        loc_record = WorkerLocation(worker_id=worker_id, lat=float(lat), lon=float(lon))
        db.session.add(loc_record)
    else:
        loc_record.lat = float(lat)
        loc_record.lon = float(lon)

    db.session.commit()
    return jsonify({"status": "success"})


@app.route("/api/worker/live-location")
def get_live_location():
    assigned_worker = session.get("assigned_worker")
    if not assigned_worker:
        return jsonify({"error": "No worker assigned"}), 404

    worker_id = assigned_worker.get("id")
    loc_record = WorkerLocation.query.filter_by(worker_id=worker_id).first()

    if not loc_record:
        return jsonify({
            "lat": assigned_worker.get("lat", 17.3850),
            "lon": assigned_worker.get("lon", 78.4867),
            "distance": assigned_worker.get("distance", "1.5")
        })

    task_info = session.get("task", {})
    distance = assigned_worker.get("distance", "1.5")
    
    if task_info.get("lat") and task_info.get("lon"):
        distance = calculate_distance(
            task_info.get("lat"), 
            task_info.get("lon"), 
            loc_record.lat, 
            loc_record.lon
        )

    return jsonify({
        "lat": loc_record.lat,
        "lon": loc_record.lon,
        "distance": distance
    })


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        if not client:
            return jsonify({"response": "API key missing. Please check GEMINI_API_KEY in your .env file."}), 400

        data = request.get_json() or {}
        prompt = data.get("prompt", "").strip()

        if not prompt:
            return jsonify({"response": "Please ask a question."}), 400

        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=f"You are a helpful assistant for Taskmate, a home services platform. Answer concisely: {prompt}",
            )
            return jsonify({"response": response.text})
        except Exception:
            return jsonify({"response": f"I can help you schedule a qualified provider for: '{prompt}'. Enter your task details above to match local workers!"})
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
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()

        clean_phone = "".join(filter(str.isdigit, phone))[-10:] if phone else None
        email_val = email if email else None

        filters = []
        if email_val:
            filters.append(User.email == email_val)
        if clean_phone:
            filters.append(User.phone == clean_phone)

        existing_user = None
        if filters:
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
        verified = False

        if user_otp and pending_user.get("otp") and user_otp == pending_user["otp"]:
            verified = True
        else:
            flash("Invalid OTP code. Try again.", "error")
            return render_template("verify.html")

        if verified:
            email_val = pending_user.get("email")
            phone_val = pending_user.get("phone")

            filters = []
            if email_val:
                filters.append(User.email == email_val)
            if phone_val:
                filters.append(User.phone == phone_val)

            existing_user = User.query.filter(db.or_(*filters)).first() if filters else None

            if not existing_user:
                new_user = User(
                    name=pending_user.get("name", "User"),
                    email=email_val,
                    phone=phone_val,
                    password=pending_user["password"],
                    role=pending_user.get("role", "user"),
                    skills=pending_user.get("skills", ""),
                    description=pending_user.get("description", ""),
                )
                db.session.add(new_user)
                db.session.commit()

            session["user"] = email_val or phone_val
            session["role"] = pending_user.get("role", "user")
            session.pop("pending_user", None)

            flash("Account verified and saved successfully!", "success")
            if session["role"] == "worker":
                return redirect(url_for("worker_dashboard"))
            return redirect(url_for("login"))

    return render_template("verify.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        login_input = request.form.get("login_input", "").strip()
        password = request.form.get("password", "").strip()

        digits_only = "".join(filter(str.isdigit, login_input))
        clean_input = digits_only[-10:] if len(digits_only) >= 10 else login_input

        user = User.query.filter(
            (User.email == login_input) | (User.phone == clean_input)
        ).first()

        if not user:
            flash("User not found.", "error")
        elif not (check_password_hash(user.password, password) or user.password == password):
            flash("Incorrect password", "error")
        else:
            session["user"] = user.email or user.phone
            session["role"] = user.role
            flash("Logged in successfully!", "success")
            return redirect(url_for("home"))

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
        session["pending_user"] = {
            "name": name,
            "contact": contact,
            "email": contact if is_email else None,
            "phone": "".join(filter(str.isdigit, contact))[-10:] if not is_email else None,
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
        clean_contact = "".join(filter(str.isdigit, contact))[-10:] if "@" not in contact else contact

        user = User.query.filter(
            ((User.email == clean_contact) | (User.phone == clean_contact)) & (User.role == "worker")
        ).first()

        if not user:
            flash("User not found.", "error")
        elif not (check_password_hash(user.password, password) or user.password == password):
            flash("Incorrect password", "error")
        else:
            session["user"] = contact
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

    clean_key = "".join(filter(str.isdigit, str(user_key)))[-10:] if "@" not in str(user_key) else user_key
    user_obj = User.query.filter((User.email == clean_key) | (User.phone == clean_key)).first()

    worker_profile = {
        "name": user_obj.name if user_obj else "Worker",
        "skills": user_obj.skills if user_obj else "General Service",
        "description": user_obj.description if user_obj else "No service bio provided.",
    }

    active_job = session.get("task") if session.get("assigned_worker") else None
    current_status = session.get("status", "Worker On The Way")

    pending_tasks = [
        {
            "id": 101,
            "title": session.get("task", {}).get("title", "Fix Ceiling Fan"),
            "description": session.get("task", {}).get("description", "Fan making high noise"),
            "location": session.get("task", {}).get("location", "Meerpet, Hyderabad"),
            "budget": session.get("task", {}).get("budget", "450"),
            "urgency": "High",
            "time_posted": "10 mins ago",
        }
    ]

    return render_template(
        "worker_dashboard.html",
        worker=worker_profile,
        tasks=pending_tasks,
        active_job=active_job,
        current_status=current_status,
    )


# --- Database Creation & Entry Point ---
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)