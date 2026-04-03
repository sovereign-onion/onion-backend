# FILE: backend/main.py
from __future__ import annotations

import threading
import time

from config import SCAN_INTERVAL_SECONDS
from db import expire_old_items, init_db
from scanner import scan_all_sources
from server import run_http_server


def scheduler_loop() -> None:
    while True:
        try:
            stats = scan_all_sources()
            expired = expire_old_items()
            print(
                "scan complete | "
                f"sources={stats['processed_sources']} "
                f"matched={stats['matched_items']} "
                f"errors={stats['errors']} "
                f"expired={expired}"
            )
        except Exception as exc:
            print(f"scan failed | {exc}")

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    init_db()

    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

    run_http_server()