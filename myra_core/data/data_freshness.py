from myra_core.utils.date_utils import to_date

FRESHNESS_RULES = {
    "bhavcopy": 1,
    "insider": 5,
    "bulk": 2
}

def check_data_freshness(data_date, expected_date, data_type):
    """
    Checks data freshness against defined rules.
    Returns: { status: 'OK'|'STALE'|'FUTURE', lag_days: int, allowed_lag: int }
    """
    dt_data = to_date(data_date)
    dt_expected = to_date(expected_date)

    lag_days = (dt_expected - dt_data).days
    allowed_lag = FRESHNESS_RULES.get(data_type, 1) # default to 1

    if lag_days < 0:
        status = "FUTURE"
    elif lag_days <= allowed_lag:
        status = "OK"
    else:
        status = "STALE"

    return {
        "status": status,
        "lag_days": lag_days,
        "allowed_lag": allowed_lag
    }