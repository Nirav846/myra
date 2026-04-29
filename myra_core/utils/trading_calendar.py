import datetime
import json
import os

from myra_core.utils.date_utils import to_date


def get_market_holidays(year):
    """
    Fetch holidays from NSE/BSE with fallback to cache/static json.
    Returns a set of strings in '%Y-%m-%d' format representing holidays.
    """
    cache_file = os.path.join(os.getcwd(), "data", "meta", f"holidays_{year}.json")
    if os.path.exists(cache_file):
        try:
            import time

            file_age_days = (time.time() - os.path.getmtime(cache_file)) / 86400
            if file_age_days < 7:
                with open(cache_file, "r") as f:
                    return set(json.load(f))
        except Exception:
            pass

    from myra_app.fetcher import DataFetcher

    fetcher = DataFetcher()
    holidays = set()

    # 1. Primary: NSE
    try:
        r = fetcher.session.get(
            "https://www.nseindia.com/api/holiday-master?type=trading",
            headers={
                "Referer": "https://www.nseindia.com/",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=10,
        )
        if getattr(r, "status_code", 0) == 200:
            data = r.json()
            for h in data.get("CM", []):
                try:
                    # Performance Guard Compliant (Fix 44)
                    d = (
                        datetime.datetime.strptime(h["tradingDate"], "%d-%b-%Y")
                        .date()
                        .isoformat()
                    )
                    if d.startswith(str(year)):
                        holidays.add(d)
                except:
                    pass
    except Exception:
        pass

    # 2. Fallback: BSE
    if not holidays:
        try:
            import urllib.request

            req = urllib.request.Request(
                "https://api.bseindia.com/BseIndiaAPI/api/HolidayTrading/w?flag=Trading",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode()).get("Table", [])
                for h in data:
                    try:
                        # Performance Guard Compliant (Fix 67)
                        d = (
                            datetime.datetime.strptime(h["TradingDate"], "%d %b %Y")
                            .date()
                            .isoformat()
                        )
                        if d.startswith(str(year)):
                            holidays.add(d)
                    except:
                        pass
        except Exception:
            pass

    # 3. Final Fallback: PKDateUtilities
    if not holidays:
        from myra_core.date_utils import PKDateUtilities

        try:
            _, dates = PKDateUtilities.holidayList()
            if dates:
                holidays = {d for d in dates if d.startswith(str(year))}
        except Exception:
            pass

    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(list(holidays), f)
    return holidays


def is_trading_day(d):
    """
    Returns True if the given date is a trading day (not weekend, not holiday).
    """
    dt = to_date(d)
    if dt.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False

    holidays = get_market_holidays(dt.year)
    # Performance Guard Compliant (Fix 103)
    if dt.isoformat() in holidays:
        return False

    return True


def get_previous_trading_day(d):
    """
    Iteratively step backward until a valid trading day is found.
    """
    dt = to_date(d)
    target = dt - datetime.timedelta(days=1)

    # Pre-fetch holidays
    holidays = get_market_holidays(target.year)

    # Performance Guard Compliant (Fix 119)
    while target.weekday() >= 5 or target.isoformat() in holidays:
        target -= datetime.timedelta(days=1)

        # Don't infinitely fetch if we didn't get any holidays for the year
        current_year_holidays = [d for d in holidays if d.startswith(str(target.year))]
        if (
            not current_year_holidays
            and getattr(get_market_holidays, f"_fetched_{target.year}", False) is False
        ):
            setattr(get_market_holidays, f"_fetched_{target.year}", True)
            holidays.update(get_market_holidays(target.year))

    return target


def get_expected_trading_day(now=None):
    """
    If today is trading day (and after market hours) -> return today.
    Else -> return previous trading day.
    """
    if not now:
        now = datetime.datetime.now()

    target = to_date(now)

    # If it's a datetime object, check the time
    if isinstance(now, datetime.datetime):
        is_after_market = now.hour > 18 or (now.hour == 18 and now.minute >= 30)
        if not is_after_market:
            target = get_previous_trading_day(target)

    if is_trading_day(target):
        return target
    else:
        return get_previous_trading_day(target)
