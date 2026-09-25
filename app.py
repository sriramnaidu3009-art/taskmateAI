from __future__ import annotations

import json
import os
import random
import time
import firebase_admin
import razorpay
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
    UserMixin,
    LoginManager,
    login_required,
    login_user,
    logout_user,
    current_user,
)
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from google import genai
from google.genai import types
from werkzeug.security import generate_password_hash, check_password_hash

# 1. Load Environment Variables
load_dotenv()

# 2. Initialize Flask App & Configurations
app = Flask(__name__)

# Gemini Setup
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

# Flask Mail Setup
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_SUPPRESS_SEND"] = os.getenv("MAIL_SUPPRESS_SEND", "False").lower() in ("true", "1", "t")

mail = Mail(app)

app.secret_key = os.getenv("SECRET_KEY", "taskmate-demo-change-this-before-deploying")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
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
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_Ten1DAFnOCg6e4")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "3EBlTp0mCvV0wqxp462JFJwV")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


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
    "caregiver": ["grandpa", "grandma", "elderly", "care", "look after", "patient", "nurse", "senior"],
}


# --- Helper & AI Functions ---

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
        max_retries = 3
        delay = 1
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                )
                clean_json = response.text.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_json)
            except Exception as e:
                print(f"[GEMINI ANALYZE ERROR Attempt {attempt + 1}]: {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2

    # Rule-Based Fallback
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


def generate_ai_workers(task_title: str, task_desc: str, category: str) -> list[dict]:
    """Generates workers whose occupations and skills strictly match the user's task requirement."""
    
    prompt = f"""
    You are an AI Workforce Dispatch Agent.
    A customer submitted the following service request:
    - Task Title: "{task_title}"
    - Description: "{task_desc}"
    - Inferred Category: "{category}"

    STRICT OCCUPATION MATCHING INSTRUCTIONS:
    1. Every generated worker's "role" (occupation) MUST directly correspond to the customer's request. 
       (e.g., if the user asks to look after a grandpa, roles MUST be "Senior Caregiver", "Elderly Care Assistant", or "Home Patient Assistant").
    2. "skills" must be specific and relevant to this job request.
    3. Do NOT generate generic or unrelated service roles (e.g., do not return an electrician if the task is caregiving or tutoring).

    Return ONLY a valid JSON array of 3 distinct worker objects (no extra text or markdown formatting):
    [
        {{
            "id": 301,
            "name": "Full Name",
            "initials": "FN",
            "role": "Exact Relevant Role/Occupation",
            "skills": ["Relevant Skill 1", "Relevant Skill 2", "Relevant Skill 3"],
            "rating": 4.9,
            "jobs": 52,
            "distance": 1.8,
            "rate": 450,
            "available": true,
            "color": "#1d7a68",
            "score": 96
        }}
    ]
    """

    if client:
        max_retries = 3
        delay = 1
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                )
                clean_json = response.text.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_json)
            except Exception as e:
                print(f"[AI WORKER GENERATION ATTEMPT {attempt + 1} FAILED]: {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2

    # Fallback profiles matching the category domain
    return [
        {
            "id": 401,
            "name": "Suresh Verma",
            "initials": "SV",
            "role": f"Certified {category} Specialist",
            "skills": [f"{category} Assistance", "Direct Support", "Verified"],
            "rating": 4.9,
            "jobs": 88,
            "distance": 1.5,
            "rate": 500,
            "available": True,
            "color": "#1d7a68",
            "score": 95,
        },
        {
            "id": 402,
            "name": "Ananya Rao",
            "initials": "AR",
            "role": f"Experienced {category} Provider",
            "skills": [f"{category} Care", "Personal Support", "Trusted"],
            "rating": 4.8,
            "jobs": 64,
            "distance": 2.8,
            "rate": 450,
            "available": True,
            "color": "#5e63c8",
            "score": 90,
        },
    ]


def ranked_workers(analysis: dict) -> list[dict]:
    """Combines hardcoded workers with dynamically generated AI workers based on task relevance."""
    required = [s.lower() for s in analysis.get("skills", [])]
    category = str(analysis.get("category", "")).lower()
    ranked = []

    # 1. Score hardcoded static workers
    for worker in WORKERS:
        worker_skills = [s.lower() for s in worker.get("skills", [])]
        worker_role = str(worker.get("role", "")).lower()

        skill_matches = sum(
            1 for req in required if any(req in w_s or w_s in req for w_s in worker_skills)
        )
        category_match = 1 if (category in worker_role or worker_role in category) else 0

        if skill_matches > 0 or category_match > 0:
            overlap_score = (skill_matches / len(required)) if required else 0.5
            distance_score = max(0, 1 - worker["distance"] / 8)
            rating_score = worker["rating"] / 5
            availability_score = 1.0 if worker["available"] else 0.0

            score = round(
                (
                    overlap_score * 0.40
                    + category_match * 0.20
                    + distance_score * 0.20
                    + rating_score * 0.10
                    + availability_score * 0.10
                )
                * 100
            )

            item = worker.copy()
            item["score"] = max(score, 65)
            item["skill_matches"] = skill_matches
            ranked.append(item)

    # 2. Dynamically fetch matching AI candidates to expand worker availability
    task_info = session.get("task", {})
    ai_generated = generate_ai_workers(
        task_info.get("title", "Service Request"),
        task_info.get("description", ""),
        analysis.get("category", "General Helper"),
    )

    combined = ranked + ai_generated
    combined.sort(key=lambda x: x.get("score", 80), reverse=True)
    return combined


def send_otp(email, otp):
    msg = Message(
        "Your Taskmate AI Verification Code",
        recipients=[email],
    )
    msg.body = f"Your 6-digit verification code is: {otp}"
    try:
        mail.send(msg)
    except Exception as e:
        print(f"[MAIL ERROR / RENDER BLOCK]: {e}")


# --- Application Routes ---

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        task = {
            key: request.form.get(key, "").strip()
            for key in ("title", "description", "location", "budget")
        }
        analysis = analyze_task(task["title"], task["description"])
        session["task"] = task
        session["analysis"] = analysis
        return redirect(url_for("matches"))
    return render_template("home.html", razorpay_key=RAZORPAY_KEY_ID)


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        if not client:
            return jsonify({
                "response": "API key missing. Please check GEMINI_API_KEY in your .env file."
            }), 400

        data = request.get_json() or {}
        prompt = data.get("prompt", "").strip()

        if not prompt:
            return jsonify({"response": "Please ask a question."}), 400

        max_retries = 3
        delay = 1

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=f"You are a helpful assistant for Taskmate, a home services platform. Answer concisely: {prompt}",
                )
                return jsonify({"response": response.text})
            except Exception as api_err:
                print(f"[GEMINI ATTEMPT {attempt + 1} FAILED]: {api_err}")
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2

        return jsonify({
            "response": f"I can help you schedule a qualified service provider for your request: '{prompt}'. Enter your task details in the form above to see matched local workers!"
        })

    except Exception as e:
        print(f"[CHAT ROUTE CRASH]: {e}")
        return jsonify({"response": "Internal server error occurred."}), 500


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if User.query.filter_by(email=email).first():
            flash("Email address already exists.")
            return redirect(url_for("register"))

        otp = str(random.randint(100000, 999999))
        session["temp_user"] = {
            "email": email,
            "password": generate_password_hash(password, method="scrypt"),
            "otp": otp,
        }

        try:
            send_otp(email, otp)
            print(f"\n==========================================")
            print(f"[DEBUG] OTP Code for {email}: {otp}")
            print(f"==========================================\n")

            flash("Verification code generated! Check terminal or email.")
            return redirect(url_for("verify"))
        except Exception as e:
            flash(f"Registration error: {e}")
            return redirect(url_for("register"))

    return render_template("register.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    temp_user = session.get("temp_user")
    if not temp_user:
        return redirect(url_for("register"))

    if request.method == "POST":
        user_code = request.form.get("otp")
        if user_code == temp_user["otp"]:
            new_user = User(
                email=temp_user["email"], password=temp_user["password"]
            )
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            session.pop("temp_user", None)
            return redirect(url_for("home"))
        else:
            flash("Invalid verification code. Please try again.")

    return render_template("verify.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            flash("Invalid credentials, please try again.")
            return redirect(url_for("login"))

        login_user(user)
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


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


@app.route("/matches")
def matches():
    analysis = session.get("analysis")
    if not analysis:
        return redirect(url_for("home"))
    
    # Store generated workers in session so their IDs remain fixed
    workers = ranked_workers(analysis)
    session["current_matches"] = workers
    
    return render_template(
        "matches.html",
        task=session.get("task", {}),
        analysis=analysis,
        workers=workers,
    )


@app.route("/assign/<int:worker_id>", methods=["POST"])
def assign(worker_id: int):
    # 1. Search hardcoded static workers first
    worker = next((item for item in WORKERS if item["id"] == worker_id), None)
    
    # 2. Search saved session matches second (for AI-generated workers)
    if not worker:
        saved_matches = session.get("current_matches", [])
        worker = next((item for item in saved_matches if item["id"] == worker_id), None)

    # 3. Handle missing session data gracefully
    if not session.get("task"):
        flash("Session expired. Please submit your request again.")
        return redirect(url_for("home"))

    if not worker:
        flash("Worker not found. Please try choosing again.")
        return redirect(url_for("matches"))

    session["assigned_worker"] = worker
    session["status"] = "Assigned"
    return redirect(url_for("tracker"))

@app.route("/tracker", methods=["GET", "POST"])
def tracker():
    task = session.get("task", {
        "title": "Fix Ceiling Fan",
        "description": "Fan is making noise and stopped spinning.",
        "location": "Meerpet, Hyderabad",
        "budget": "450"
    })
    
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


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("home"))


@app.route("/create-order", methods=["POST"])
def create_order():
    try:
        data = request.get_json() or {}
        amount = data.get("amount", 50000)

        order = razorpay_client.order.create(
            data={"amount": amount, "currency": "INR", "receipt": "task_booking"}
        )
        return jsonify(order)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/verify-payment", methods=["POST"])
def verify_payment():
    data = request.json
    try:
        razorpay_client.utility.verify_payment_signature(
            {
                "razorpay_order_id": data["razorpay_order_id"],
                "razorpay_payment_id": data["razorpay_payment_id"],
                "razorpay_signature": data["razorpay_signature"],
            }
        )
        return jsonify({"status": "success", "message": "Payment verified!"})
    except razorpay.errors.SignatureVerificationError:
        return (
            jsonify({"status": "failed", "message": "Invalid payment signature"}),
            400,
        )


# --- Worker Application Routes ---

@app.route("/worker/dashboard")
def worker_dashboard():
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
            "time_posted": "10 mins ago"
        }
    ]
    return render_template(
        "worker_dashboard.html", 
        tasks=pending_tasks, 
        active_job=active_job, 
        current_status=current_status
    )


@app.route("/api/worker/accept-task/<int:task_id>", methods=["POST"])
def worker_accept_task(task_id: int):
    worker = WORKERS[0]
    
    if "task" not in session:
        session["task"] = {
            "title": "Fix Ceiling Fan",
            "description": "Fan making high noise and stopped spinning.",
            "location": "Meerpet, Hyderabad",
            "budget": "450"
        }

    session["assigned_worker"] = worker
    session["status"] = "Worker On The Way"
    
    return jsonify({
        "status": "success",
        "message": f"Task #{task_id} accepted!",
        "redirect": url_for("worker_dashboard")
    })


@app.route("/api/worker/update-status", methods=["POST"])
def worker_update_status():
    data = request.get_json() or {}
    new_status = data.get("status")
    if new_status:
        session["status"] = new_status
    return jsonify({"status": "success", "current_status": session["status"]})


# --- Database Creation & Entry Point ---
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, port=5000)