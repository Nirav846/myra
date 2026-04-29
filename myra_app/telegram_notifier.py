import os

import requests


class TelegramNotifier:
    """
    MYRA Telegram Notifier - Institutional Alert Delivery.
    Delivers high-conviction scan results directly to your phone.
    """

    def __init__(self):
        # Users should set these environment variables
        self.token = os.environ.get("MYRA_TG_TOKEN")
        self.chat_id = os.environ.get("MYRA_TG_CHAT_ID")

    def send_message(self, message: str):
        if not self.token or not self.chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}

        try:
            r = requests.post(url, json=payload, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def send_scan_results(self, scan_name: str, results: list):
        if not results:
            return

        msg = f"🚀 *MYRA Intelligence: {scan_name}*\n\n"
        msg += f"Top {min(10, len(results))} Candidates identified:\n\n"

        for r in results[:10]:
            sym = r.get("Stock", "Unknown")
            ltp = r.get("LTP", "-")
            stars = r.get("Stars", "")
            accuracy = r.get("Accuracy", "-")
            msg += f"• *{sym}*: ₹{ltp} ({stars}) | Acc: {accuracy}\n"

        msg += f"\n📊 Market Vibe: {results[0].get('Market_Mood', 'Neutral')}"

        return self.send_message(msg)
