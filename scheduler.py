"""
scheduler.py — APScheduler-based orchestrator.

Runs all pipeline modules on configurable schedules.
Each module is executed as a subprocess for crash isolation.

Usage:
    python scheduler.py          # Start the scheduler
    python scheduler.py --once   # Run all modules once and exit
"""

import sys
import subprocess
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from utils import get_logger

log = get_logger("scheduler")

# Base directory (same as this file)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable


def run_module(module_name: str):
    """Run a pipeline module as a subprocess. Spawns per active campaign if applicable."""
    script_path = os.path.join(BASE_DIR, f"{module_name}.py")

    if not os.path.exists(script_path):
        log.error(f"Module not found: {script_path}")
        return

    # reply_checker is a global module (it monitors mailboxes for all campaigns)
    is_global_module = (module_name == "reply_checker")

    if is_global_module:
        campaign_ids = [None]
    else:
        try:
            from database import get_session
            from utils import get_active_campaign_ids
            with get_session() as session:
                campaign_ids = get_active_campaign_ids(session)
        except Exception as e:
            log.error(f"Failed to fetch active campaigns for {module_name}: {e}")
            return
            
        if not campaign_ids:
            log.info(f">> Skipping {module_name} (no active campaigns)")
            return

    for cid in campaign_ids:
        cmd = [PYTHON, script_path]
        if cid is not None:
            cmd.append(f"--campaign-id={cid}")
            log_prefix = f"[{module_name} | Camp #{cid}]"
            log.info(f">> Running {module_name} for Campaign #{cid}...")
        else:
            log_prefix = f"[{module_name}]"
            log.info(f">> Running {module_name} (global)...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout per module
                cwd=BASE_DIR,
            )

            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if line: log.info(f"  {log_prefix} {line}")

            if result.returncode != 0:
                log.error(f"  {log_prefix} exited with code {result.returncode}")
                if result.stderr:
                    for line in result.stderr.strip().split("\n")[-5:]:
                        if line: log.error(f"  {log_prefix} {line}")
            else:
                log.info(f"[OK] {log_prefix} completed successfully")

        except subprocess.TimeoutExpired:
            log.error(f"  {log_prefix} timed out after 600s")
        except Exception as e:
            log.error(f"  {log_prefix} failed to run: {e}")


# ── Schedule Configuration ─────────────────────────────────
SCHEDULE = [
    # (module_name, trigger_kwargs)
    ("watcher",        {"hours": 6}),       # Every 6 hours
    ("scorer",         {"hours": 2}),       # Every 2 hours
    ("finder",         {"hours": 4}),       # Every 4 hours
    ("verifier",       {"hours": 4}),       # Every 4 hours
    ("research",       {"hours": 4}),       # Every 4 hours
    ("email_writer",   {"hours": 4}),       # Every 4 hours
    ("sender",         {"hours": 1}),       # Every 1 hour
    ("reply_checker",  {"minutes": 30}),    # Every 30 minutes
]


def run_all_once():
    """Run all modules in sequence once."""
    log.info("Running all modules once...")
    pipeline_order = [
        "watcher",
        "scorer",
        "finder",
        "verifier",
        "research",
        "email_writer",
        "sender",
        "reply_checker",
    ]
    for module in pipeline_order:
        run_module(module)
    log.info("All modules completed.")


def start_scheduler():
    """Start the APScheduler with configured intervals."""
    scheduler = BlockingScheduler()

    for module_name, trigger_kwargs in SCHEDULE:
        trigger = IntervalTrigger(**trigger_kwargs)
        scheduler.add_job(
            run_module,
            trigger=trigger,
            args=[module_name],
            id=module_name,
            name=f"Pipeline: {module_name}",
            max_instances=1,           # Don't overlap runs
            coalesce=True,             # Collapse missed runs
            misfire_grace_time=300,    # 5 minute grace
        )
        interval_str = ", ".join(f"{k}={v}" for k, v in trigger_kwargs.items())
        log.info(f"  Scheduled {module_name}: every {interval_str}")

    log.info("Scheduler started. Press Ctrl+C to exit.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler shutting down...")
        scheduler.shutdown()


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_all_once()
    else:
        start_scheduler()
