from flask import Flask, jsonify, render_template_string
import requests
import json
import threading
import time
import os
import csv
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

app = Flask(__name__)

URL = "https://platform-v2.ridges.ai/retrieval/top-agents"
PARAMS = {"number_of_agents": 20}
OUTPUT_FILE = "agent_id.json"
LOG_FILE = "diff_log.csv"


def round_half_up(value: float) -> int:
    """Round float to nearest integer (standard rounding)."""
    return int(Decimal(value).to_integral_value(rounding=ROUND_HALF_UP))


def load_existing_agents():
    """Load existing agents from JSON file if it exists."""
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return {a["agent_id"]: a["score"] for a in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def append_log(timestamp: str, total_diff: int):
    """Append total_diff and timestamp to CSV log."""
    header_needed = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if header_needed:
            writer.writerow(["timestamp", "total_diff"])
        writer.writerow([timestamp, total_diff])


def fetch_and_save():
    """Fetch new agents, compute total_diff, overwrite file, and log result."""
    try:
        response = requests.get(URL, params=PARAMS, timeout=30)
        response.raise_for_status()
        data = response.json()

        threshold = int(os.getenv("THRESHOLD_SCORE", "0"))
        existing = load_existing_agents()
        new_agents = {}
        total_diff = 0

        for item in data:
            if not isinstance(item, dict):
                continue

            agent_id = item.get("agent_id")
            score = item.get("final_score")

            if agent_id is None or score is None:
                continue

            int_score = round_half_up(float(score) * 100)
            new_agents[agent_id] = int_score

            if agent_id not in existing:
                diff = int_score - threshold
                total_diff += diff

        # Overwrite file with only new agents
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                [{"agent_id": aid, "score": score} for aid, score in new_agents.items()],
                f,
                indent=4,
            )

        # Log result
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        append_log(timestamp, total_diff)

        print(f"[{timestamp}] Saved {len(new_agents)} agents to {OUTPUT_FILE}")
        print(f"  → Sum of (rounded_score - threshold) for new agents = {total_diff}")

    except Exception as e:
        print(f"[ERROR] {e}")


def sleep_until_next_10min():
    """Sleep until the next exact 10-minute mark."""
    now = datetime.now()
    next_minute = (now.minute // 10 + 1) * 10
    if next_minute >= 60:
        next_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        next_time = now.replace(minute=next_minute, second=0, microsecond=0)
    return (next_time - now).total_seconds()


def background_task():
    """Run fetch_and_save() every 10-minute mark."""
    while True:
        fetch_and_save()
        sleep_sec = sleep_until_next_10min()
        print(f"[INFO] Sleeping for {int(sleep_sec)} seconds until next 10-min mark...")
        time.sleep(sleep_sec)


@app.route("/")
def index():
    """Frontend: show chart of total_diff over time."""
    # Load CSV data
    timestamps, diffs = [], []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                timestamps.append(row["timestamp"])
                diffs.append(int(row["total_diff"]))

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Total Diff Chart</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 40px; background: #fafafa; }
            canvas { max-width: 800px; margin: 20px auto; display: block; }
        </style>
    </head>
    <body>
        <h2>Total Diff Over Time</h2>
        <canvas id="diffChart"></canvas>
        <script>
            const ctx = document.getElementById('diffChart').getContext('2d');
            const chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: {{ timestamps | safe }},
                    datasets: [{
                        label: 'Total Diff',
                        data: {{ diffs | safe }},
                        borderColor: 'rgba(75, 192, 192, 1)',
                        borderWidth: 2,
                        fill: false,
                        tension: 0.2
                    }]
                },
                options: {
                    scales: {
                        x: { title: { display: true, text: 'Timestamp' }},
                        y: { title: { display: true, text: 'Total Diff' }}
                    }
                }
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html, timestamps=timestamps, diffs=diffs)


@app.route("/status")
def status():
    """Simple API status endpoint."""
    threshold = os.getenv("THRESHOLD_SCORE", "not set")
    return jsonify({
        "status": "running",
        "interval": "every 10 minutes (exact marks)",
        "threshold_score": threshold
    })


if __name__ == "__main__":
    # Start background worker
    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()

    # Run Flask web server
    app.run(host="0.0.0.0", port=5000)
