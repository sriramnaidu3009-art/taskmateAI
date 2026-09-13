from __future__ import annotations

import os
import random
from dotenv import load_dotenv
import firebase_admin
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
from werkzeug.security import generate_password_hash, check_password_hash
import razorpay

# 1. Load Environment Variables
load_dotenv()

# 2. Initialize Single Flask App & Config
app = Flask(__name__)

# Mail Setup
app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_USERNAME")

# TOGGLE THIS FOR LOCAL TESTING:
# Set to True to skip Gmail sending entirely and print OTP to terminal
# Set to False once your Google App Password is generated
app.config["MAIL_SUPPRESS_SEND"] = os.getenv("MAIL_SUPPRESS_SEND", "False").lower() in ("true", "1", "t")

mail = Mail(app)

app.secret_key = os.getenv("SECRET_KEY", "taskmate-demo-change-this-before-deploying")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 3. Initialize Firebase Admin SDK safely
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
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_TbCyUbsFbZ8aot")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "RDmlYXPaWNumvI1NWcId6Aos")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# User Database Model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Static Data
WORKERS = [
    {
        "id": 1,
        "name": "Arjun Kumar",
        "initials": "AK",
        "role": "Licensed Electrician",
        "skills": [
            "electrician",
            "ceiling fan repair",
            "wiring",
            "electrical safety",
        ],
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
        "skills": [
            "electrician",
            "wiring",
            "switch repair",
            "electrical safety",
        ],
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
    "electrician": [
        "fan",
        "electric",
        "wire",
        "switch",
        "light",
        "power",
        "socket",
    ],
    "plumber": ["tap", "leak", "pipe", "drain", "water", "toilet"],
    "cleaner": ["clean", "cleaning", "dust", "wash"],
    "carpenter": ["wood", "door", "furniture", "shelf", "cabinet"],
}


# Helper Functions
def analyze_task(title: str, description: str) -> dict:
    text = f"{title} {description}".lower()
    category = next(
        (
            name
            for name, words in KEYWORDS.items()
            if any(word in text for word in words)
        ),
        "home repair",
    )
    if category == "electrician":
        skills, duration, price = (
            ["electrician", "ceiling fan repair", "electrical safety"],
            "1–2 hours",
            (350, 650),
        )
    elif category == "plumber":
        skills, duration, price = (
            ["plumber", "pipe repair", "leak detection"],
            "1–3 hours",
            (300, 800),
        )
    elif category == "cleaner":
        skills, duration, price = (
            ["cleaning", "home sanitation"],
            "2–4 hours",
            (400, 900),
        )
    elif category == "carpenter":
        skills, duration, price = (
            ["carpentry", "furniture repair"],
            "2–4 hours",
            (500, 1200),
        )
    else:
        skills, duration, price = (
            ["handyman", "home repair"],
            "1–3 hours",
            (350, 900),
        )
    urgency = (
        "High"
        if any(
            word in text for word in ["urgent", "asap", "today", "immediately"]
        )
        else "Standard"
    )
    return {
        "category": category.title(),
        "skills": skills,
        "duration": duration,
        "price_min": price[0],
        "price_max": price[1],
        "urgency": urgency,
        "summary": f"{category.title()} support needed for: {title or 'your home task'}.",
        "plan": [
            "Review the issue and bring the right tools",
            "Inspect and diagnose on arrival",
            "Complete the repair and safety-check the work",
        ],
    }


def ranked_workers(analysis: dict) -> list[dict]:
    required = set(analysis["skills"])
    ranked = []
    for worker in WORKERS:
        overlap = len(required.intersection(worker["skills"])) / len(required)
        distance_score = max(0, 1 - worker["distance"] / 8)
        rating_score = worker["rating"] / 5
        availability_score = 1 if worker["available"] else 0
        score = round(
            (
                overlap * 0.50
                + distance_score * 0.25
                + rating_score * 0.15
                + availability_score * 0.10
            )
            * 100
        )
        if overlap:
            item = worker.copy()
            item["score"] = score
            item["skill_matches"] = len(
                required.intersection(worker["skills"])
            )
            ranked.append(item)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


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
        # Allows testing/registering even if outbound SMTP ports are blocked by host


# Authentication Routes
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
            # Always log code to terminal as a reliable fallback
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


# Database Test Route
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


# Core Application Routes
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


@app.route("/matches")
def matches():
    analysis = session.get("analysis")
    if not analysis:
        return redirect(url_for("home"))
    return render_template(
        "matches.html",
        task=session["task"],
        analysis=analysis,
        workers=ranked_workers(analysis),
    )


@app.route("/assign/<int:worker_id>", methods=["POST"])
def assign(worker_id: int):
    worker = next((item for item in WORKERS if item["id"] == worker_id), None)
    if not worker or "task" not in session:
        return redirect(url_for("home"))
    session["assigned_worker"] = worker
    session["status"] = "Assigned"
    return redirect(url_for("tracker"))


@app.route("/tracker", methods=["GET", "POST"])
def tracker():
    if "assigned_worker" not in session:
        return redirect(url_for("home"))
    if request.method == "POST":
        session["status"] = request.form.get("status", session["status"])
    return render_template(
        "tracker.html",
        task=session["task"],
        worker=session["assigned_worker"],
        status=session["status"],
        razorpay_key=RAZORPAY_KEY_ID,
    )


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("home"))


# Razorpay Integration Endpoints
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


# Database Creation & Application Launch
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, port=5000)