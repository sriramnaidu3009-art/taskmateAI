from __future__ import annotations

from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = "taskmate-demo-change-this-before-deploying"

WORKERS = [
    {"id": 1, "name": "Arjun Kumar", "initials": "AK", "role": "Licensed Electrician", "skills": ["electrician", "ceiling fan repair", "wiring", "electrical safety"], "rating": 4.9, "jobs": 128, "distance": 1.2, "rate": 450, "available": True, "color": "#1d7a68"},
    {"id": 2, "name": "Priya Sharma", "initials": "PS", "role": "Home Repair Specialist", "skills": ["electrician", "ceiling fan repair", "plumbing", "handyman"], "rating": 4.8, "jobs": 91, "distance": 2.1, "rate": 400, "available": True, "color": "#e16d4c"},
    {"id": 3, "name": "Mohammed Rafi", "initials": "MR", "role": "Electrician", "skills": ["electrician", "wiring", "switch repair", "electrical safety"], "rating": 4.7, "jobs": 77, "distance": 3.4, "rate": 380, "available": True, "color": "#5e63c8"},
    {"id": 4, "name": "Neha Iyer", "initials": "NI", "role": "Appliance Technician", "skills": ["appliance repair", "ceiling fan repair", "electrician"], "rating": 4.9, "jobs": 63, "distance": 4.8, "rate": 500, "available": False, "color": "#a26cba"},
]

KEYWORDS = {
    "electrician": ["fan", "electric", "wire", "switch", "light", "power", "socket"],
    "plumber": ["tap", "leak", "pipe", "drain", "water", "toilet"],
    "cleaner": ["clean", "cleaning", "dust", "wash"],
    "carpenter": ["wood", "door", "furniture", "shelf", "cabinet"],
}


def analyze_task(title: str, description: str) -> dict:
    text = f"{title} {description}".lower()
    category = next((name for name, words in KEYWORDS.items() if any(word in text for word in words)), "home repair")
    if category == "electrician":
        skills, duration, price = ["electrician", "ceiling fan repair", "electrical safety"], "1–2 hours", (350, 650)
    elif category == "plumber":
        skills, duration, price = ["plumber", "pipe repair", "leak detection"], "1–3 hours", (300, 800)
    elif category == "cleaner":
        skills, duration, price = ["cleaning", "home sanitation"], "2–4 hours", (400, 900)
    elif category == "carpenter":
        skills, duration, price = ["carpentry", "furniture repair"], "2–4 hours", (500, 1200)
    else:
        skills, duration, price = ["handyman", "home repair"], "1–3 hours", (350, 900)
    urgency = "High" if any(word in text for word in ["urgent", "asap", "today", "immediately"]) else "Standard"
    return {
        "category": category.title(), "skills": skills, "duration": duration,
        "price_min": price[0], "price_max": price[1], "urgency": urgency,
        "summary": f"{category.title()} support needed for: {title or 'your home task'}.",
        "plan": ["Review the issue and bring the right tools", "Inspect and diagnose on arrival", "Complete the repair and safety-check the work"],
    }


def ranked_workers(analysis: dict) -> list[dict]:
    required = set(analysis["skills"])
    ranked = []
    for worker in WORKERS:
        overlap = len(required.intersection(worker["skills"])) / len(required)
        distance_score = max(0, 1 - worker["distance"] / 8)
        rating_score = worker["rating"] / 5
        availability_score = 1 if worker["available"] else 0
        score = round((overlap * .50 + distance_score * .25 + rating_score * .15 + availability_score * .10) * 100)
        if overlap:
            item = worker.copy()
            item["score"] = score
            item["skill_matches"] = len(required.intersection(worker["skills"]))
            ranked.append(item)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        task = {key: request.form.get(key, "").strip() for key in ("title", "description", "location", "budget")}
        analysis = analyze_task(task["title"], task["description"])
        session["task"] = task
        session["analysis"] = analysis
        return redirect(url_for("matches"))
    return render_template("home.html")


@app.route("/matches")
def matches():
    analysis = session.get("analysis")
    if not analysis:
        return redirect(url_for("home"))
    return render_template("matches.html", task=session["task"], analysis=analysis, workers=ranked_workers(analysis))


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
    return render_template("tracker.html", task=session["task"], worker=session["assigned_worker"], status=session["status"])


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
