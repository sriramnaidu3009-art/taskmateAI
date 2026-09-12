from __future__ import annotations

import os
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import razorpay
from dotenv import load_dotenv

# 1. Load environment variables from .env file
load_dotenv()

# 2. Fetch the Razorpay keys
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

# 3. Initialize Flask App & Razorpay Client
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "default-fallback-key")

razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ... rest of your routes below
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
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import razorpay

# Initialize App & Config
app = Flask(__name__)
app.secret_key = "taskmate-demo-change-this-before-deploying"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize Database & Auth Manager
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# assigning the razorpay api key and secret key to the variables 
RAZORPAY_KEY_ID = "rzp_test_Tb45QGTveeOPUS"
RAZORPAY_KEY_SECRET = "0UYm0h6exVHcp2KrewHRZAgz"
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


# Core Routes
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
        razorpay_key=RAZORPAY_KEY_ID,   #razorpay key for payment integration
    )


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("home"))


# User Auth Routes
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()
        if user:
            flash("Email address already exists.")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password, method="scrypt")
        new_user = User(email=email, password=hashed_password)

        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)
        return redirect(url_for("home"))

    return render_template("register.html")


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


# Razorpay Integration Endpoints
@app.route("/create-order", methods=["POST"])
def create_order():
    try:
        # Amount in paise (50000 paise = ₹500)
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