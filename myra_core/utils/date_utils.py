import datetime
import pandas as pd


def to_date(d):
    """
    Converts string (YYYY-MM-DD), datetime.datetime, or datetime.date to datetime.date.
    Raises error for unsupported types.
    """
    if isinstance(d, datetime.date) and not isinstance(d, datetime.datetime):
        return d
    elif isinstance(d, datetime.datetime):
        return d.date()
    elif isinstance(d, str):
        # Handle string parsing
        d = d.strip()
        # Handle datetime strings like "YYYY-MM-DD HH:MM:SS"
        if " " in d:
            d = d.split(" ")[0]
        try:
            return datetime.datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            try:
                return datetime.datetime.strptime(d, "%d-%b-%Y").date()
            except ValueError:
                # Fallback to pandas
                try:
                    return pd.to_datetime(d).date()
                except Exception as e:
                    raise ValueError(f"Could not parse string to date: {d}") from e
    elif pd.notna(d) and isinstance(d, pd.Timestamp):
        return d.date()
    else:
        raise TypeError(f"Unsupported type for to_date: {type(d)}")


def ensure_date(d):
    """
    Strict version (used in critical systems).
    Asserts type correctness and returns it.
    """
    if not isinstance(d, datetime.date) or isinstance(d, datetime.datetime):
        raise TypeError(f"Expected datetime.date, got {type(d)}")
    return d


def parse_dataframe_dates(df, column_list):
    """
    Converts columns to datetime.date safely in a pandas DataFrame.
    """
    if df is None or df.empty:
        return df

    for col in column_list:
        if col in df.columns:
            # We use pd.to_datetime and then extract the date component
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df
