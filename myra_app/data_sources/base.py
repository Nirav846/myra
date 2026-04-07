# myra_app/data_sources/base.py
import time
import requests


class RateLimiter:
    def __init__(self, rate_per_sec=2):
        self.delay = 1.0 / rate_per_sec
        self.last_call = 0

    def wait(self):
        now = time.time()
        elapsed = now - self.last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_call = time.time()


class SourceManager:
    def __init__(self):
        self.sources = {
            "screener_in": {"score": 10, "cooldown_until": 0},
            "google_finance": {"score": 9, "cooldown_until": 0},
            "finology": {"score": 8.5, "cooldown_until": 0},
            "nse": {"score": 8, "cooldown_until": 0},
            "yahoo": {"score": 7, "cooldown_until": 0},
        }

    def get_available_sources(self):
        now = time.time()
        active = [
            (name, data["score"])
            for name, data in self.sources.items()
            if data["cooldown_until"] < now
        ]
        return sorted(active, key=lambda x: -x[1])

    def mark_failure(self, name, is_rate_limit=False):
        if name not in self.sources:
            self.sources[name] = {"score": 5, "cooldown_until": 0}

        # If rate limited, cool off for 1 hour. Otherwise 10 mins.
        cooldown = 3600 if is_rate_limit else 600
        # Fix 42, 43: Avoid chained indexing
        source_entry = self.sources[name]
        source_entry["score"] -= 1
        source_entry["cooldown_until"] = time.time() + cooldown
        print(f"[!] Source {name} failing. Cooling off for {cooldown//60} mins.")

    def mark_success(self, name):
        if name not in self.sources:
            self.sources[name] = {"score": 5, "cooldown_until": 0}
        # Fix 49: Avoid chained indexing
        source_entry = self.sources[name]
        source_entry["score"] = min(10, source_entry["score"] + 0.5)


class BaseDataSource:
    def fetch(self, symbol):
        raise NotImplementedError
