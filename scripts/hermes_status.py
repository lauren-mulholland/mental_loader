"""Print non-secret native scheduler metadata using Hermes' own environment."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/hermes-runtime"))
os.environ["HERMES_HOME"] = str(ROOT / ".local/hermes-home")
os.environ["HERMES_RUNTIME_DIR"] = str(ROOT / ".local/hermes-managed")


def main():
    from cron.jobs import get_ticker_heartbeat_age, list_jobs, ticker_heartbeat_writer_alive
    from tools.skills_tool import find_all_skills

    jobs = [
        job
        for job in list_jobs(include_disabled=True)
        if job.get("name") == "mental-loader-reminders"
    ]
    age = get_ticker_heartbeat_age()
    print(
        json.dumps(
            {
                "job_registered": len(jobs) == 1,
                "ticker_fresh": age is not None and age < 120 and ticker_heartbeat_writer_alive(),
                "household_skills": sorted(
                    skill["name"]
                    for skill in find_all_skills()
                    if skill["name"].startswith("household-")
                ),
                "heartbeat_age_seconds": age,
                "jobs": [
                    {
                        key: job.get(key)
                        for key in (
                            "id",
                            "enabled",
                            "paused",
                            "last_run_at",
                            "last_status",
                            "next_run_at",
                            "no_agent",
                        )
                    }
                    for job in jobs
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
