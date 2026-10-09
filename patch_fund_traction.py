import sys

def main():
    filepath = r"D:\01screener\Myra\myra_app\fund_traction_sync.py"
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # lines are 0-indexed. We want to replace from line 1055 to 1089 inclusive (since start_line=1056 -> index 1055, end_line=1090 -> index 1089)
    start_idx = 1055  # because line numbers in the read were 1-based
    end_idx = 1089    # inclusive

    new_lines = [
        "    # Latest close + prior-month close from technical_data (read-only).\n",
        "    price_map: dict[str, dict[str, float | None]] = {}\n",
        "    try:\n",
        "        tech_path = os.path.join(DB_DIR, \"myra_technical.db\")\n",
        "        if os.path.exists(tech_path):\n",
        "            tconn = sqlite3.connect(f\"file:{tech_path}?mode=ro\", uri=True)\n",
        "            try:\n",
        "                prior_month = None\n",
        "                if target_month:\n",
        "                    y, m = (int(x) for x in target_month.split(\"\"))\n",
        "                    prior_month = _prev_month_label(y, m)\n",
        "                for i in range(0, len(symbols), 500):\n",
        "                    chunk = symbols[i : i + 500]\n",
        "                    ph = \",\".join(\"?\" for _ in chunk)\n",
        "                    base = f\"\"\"\n",
        "                        SELECT symbol, close FROM (\n",
        "                            SELECT symbol, close,\n",
        "                                   ROW_NUMBER() OVER (\n",
        "                                       PARTITION BY symbol ORDER BY date DESC\n",
        "                                   ) AS rn_latest,\n",
        "                                   ROW_NUMBER() OVER (\n",
        "                                       PARTITION BY symbol,\n",
        "                                           strftime('%Y-%m', date) ORDER BY date DESC\n",
        "                                   ) AS rn_prior\n",
        "                            FROM technical_data\n",
        "                            WHERE symbol IN ({ph}) AND close IS NOT NULL\n",
        "                        ) WHERE rn_latest = 1\"\"\"\n",
        "                    if prior_month:\n",
        "                        base += f\"\"\" OR rn_prior = 1 AND strftime('%Y-%m', date) = {prior_month!r}\"\"\"\n",
        "                    for sym, close in tconn.execute(base, chunk).fetchall():\n",
        "                        if sym not in price_map:\n",
        "                            price_map[sym] = {}\n",
        "                        price_map[sym][\"latest\"] = close\n",
        "                        if prior_month and \"prior\" not in price_map[sym]:\n",
        "                            price_map[sym][\"prior\"] = close\n",
        "            finally:\n",
        "                tconn.close()\n",
        "    except sqlite3.Error:\n",
        "        price_map = {}\n",
        "\n",
        "    for r in rows:\n",
        "        mc = mcap_map.get(r[\"symbol\"])\n",
        "        r[\"market_cap_cr\"] = round(mc / 1e7, 1) if mc else None\n",
        "        r[\"mcap_bucket\"] = _mcap_bucket(mc)\n",
        "        closes = price_map.get(r[\"symbol\"], {})\n",
        "        r[\"price\"] = closes.get(\"latest\")\n",
        "        r[\"prev_month_close\"] = closes.get(\"prior\")\n",
        "        latest = r[\"price\"]\n",
        "        prior = r[\"prev_month_close\"]\n",
        "        if latest and prior:\n",
        "            r[\"pct_vs_prev\"] = round((latest - prior) / prior * 100, 2)\n",
        "        else:\n",
        "            r[\"pct_vs_prev\"] = None\n",
        "\n"
    ]

    # Replace the lines
    lines[start_idx:end_idx+1] = new_lines

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)

if __name__ == "__main__":
    main()