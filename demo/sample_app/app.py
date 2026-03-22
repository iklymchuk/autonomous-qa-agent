"""
Demo Flask application for integration testing and README demos.
Intentionally includes a broken page (/broken) so the QA agent has failures to classify.
"""

from __future__ import annotations

import os
import random

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-not-for-production")

VALID_EMAIL = "admin@test.com"
VALID_PASSWORD = "password123"

DASHBOARD_STATS = [
    {"label": "Total Users", "value": 1_234, "change": "+12%"},
    {"label": "Active Sessions", "value": 89, "change": "+5%"},
    {"label": "Revenue", "value": "$45,678", "change": "+8%"},
    {"label": "Error Rate", "value": "0.12%", "change": "-2%"},
]

TABLE_ROWS = [
    {"id": 1, "name": "Alice Johnson", "role": "Admin", "status": "Active", "joined": "2024-01-15"},
    {"id": 2, "name": "Bob Smith", "role": "Editor", "status": "Active", "joined": "2024-02-20"},
    {"id": 3, "name": "Carol Davis", "role": "Viewer", "status": "Inactive", "joined": "2024-03-10"},
    {"id": 4, "name": "Dan Wilson", "role": "Editor", "status": "Active", "joined": "2024-04-05"},
    {"id": 5, "name": "Eve Martinez", "role": "Admin", "status": "Active", "joined": "2024-05-18"},
]


@app.route("/")
def index() -> str:
    """Landing page with navigation and CTA button."""
    return render_template("index.html")


@app.route("/login", methods=["GET"])
def login_get() -> str:
    """Login form page."""
    return render_template("login.html", error=None)


@app.route("/login", methods=["POST"])
def login_post() -> object:
    """Process login credentials."""
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    if email == VALID_EMAIL and password == VALID_PASSWORD:
        session["user"] = email
        return redirect(url_for("dashboard"))

    return render_template("login.html", error="Invalid email or password. Please try again.")


@app.route("/logout")
def logout() -> object:
    """Clear session and redirect to home."""
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard() -> str:
    """Protected dashboard page with stats and table."""
    # In demo mode, don't require login so the agent can discover this page
    user = session.get("user", "demo@example.com")
    return render_template("dashboard.html", user=user, stats=DASHBOARD_STATS, rows=TABLE_ROWS)


@app.route("/form", methods=["GET"])
def form_get() -> str:
    """Multi-field form page."""
    return render_template("form.html", submitted=False, data=None)


@app.route("/form", methods=["POST"])
def form_post() -> object:
    """Process form submission and echo data back."""
    if request.is_json:
        data = request.get_json()
    else:
        data = {
            "name": request.form.get("name", ""),
            "email": request.form.get("email", ""),
            "department": request.form.get("department", ""),
            "subscribe": "subscribe" in request.form,
            "message": request.form.get("message", ""),
            "contact_date": request.form.get("contact_date", ""),
        }
    return render_template("form.html", submitted=True, data=data)


@app.route("/api/data")
def api_data() -> object:
    """JSON API endpoint returning paginated items."""
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 5))

    items = [
        {"id": i, "name": f"Item {i}", "value": round(random.uniform(10, 1000), 2), "active": i % 3 != 0}
        for i in range((page - 1) * per_page + 1, page * per_page + 1)
    ]

    return jsonify(
        {
            "items": items,
            "total": 100,
            "page": page,
            "per_page": per_page,
            "pages": 20,
        }
    )


@app.route("/broken")
def broken() -> str:
    """
    Intentionally broken page for QA agent failure classification.
    Contains: missing elements, JavaScript errors, and accessibility violations.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Broken Page</title>
    <style>body { font-family: sans-serif; padding: 24px; }</style>
</head>
<body>
    <h1>This Page Has Issues</h1>

    <!-- Missing alt text (accessibility violation) -->
    <img src="/static/missing-image.png">

    <!-- Button with no label (accessibility violation) -->
    <button id="unlabeled-btn" onclick="crashMe()"></button>

    <!-- Form with missing labels -->
    <form id="broken-form" action="/nonexistent" method="post">
        <input type="text" name="data" placeholder="No label here">
        <input type="submit" value="Submit to Nowhere">
    </form>

    <!-- JavaScript that throws an error -->
    <script>
        // This will throw a ReferenceError immediately
        undefinedVariable.doSomething();
    </script>

    <p id="dynamic-content">Loading...</p>

    <script>
        // This selector will fail because #real-element doesn't exist
        document.getElementById('real-element').textContent = 'This will throw';
    </script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
