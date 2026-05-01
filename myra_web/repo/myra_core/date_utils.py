import datetime
import json
import os
import requests
import pandas as pd
import pytz


class PKDateUtilities:
    _holidays_cache = None

    @staticmethod
    def holidayList():
        if PKDateUtilities._holidays_cache:
            return PKDateUtilities._holidays_cache

        url = "https://raw.githubusercontent.com/pkjmesra/PKScreener/main/.github/dependencies/nse-holidays.json"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code != 200:
                return None, None

            cm = res.json()["CM"]
            df = pd.DataFrame(cm)
            # Normalize dates to YYYY-MM-DD
            # Fix 27: Avoid .dt.strftime
            df["tradingDate"] = pd.to_datetime(
                df["tradingDate"], format="%d-%b-%Y"
            ).dt.date.astype(str)
            PKDateUtilities._holidays_cache = (df, df["tradingDate"].tolist())
            return PKDateUtilities._holidays_cache
        except Exception:
            return None, None

    @staticmethod
    def isHoliday(d1=None):
        if d1 is None:
            d1 = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))

        if isinstance(d1, str):
            try:
                d1 = datetime.datetime.strptime(d1, "%Y-%m-%d")
            except ValueError:
                return False, None

        today_ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        if isinstance(d1, datetime.datetime):
            curr = d1.replace(tzinfo=today_ist.tzinfo)
        else:
            # If it's a date object
            curr = datetime.datetime.combine(d1, datetime.time(0, 0)).replace(
                tzinfo=today_ist.tzinfo
            )

        holidays, _ = PKDateUtilities.holidayList()
        if holidays is None:
            return False, None

        # Performance Guard Compliant (Fix 59)
        date_str = curr.date().isoformat()
        match = holidays[holidays["tradingDate"] == date_str]
        if not match.empty:
            return True, match["description"].iloc[0]

        return False, None
