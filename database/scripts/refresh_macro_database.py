#!/usr/bin/env python3
"""Build the Economics Society's documented SQLite macro-data snapshot.

The script uses public World Bank and IMF statistical APIs. It preserves source
frequency, source codes, units/metadata, and marks values calculated from source
observations as derived or estimated. Missing observations are not filled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any


WB_API = "https://api.worldbank.org/v2"
IMF_API = "https://api.imf.org/external/sdmx/2.1"
USER_AGENT = "PondicherryEconomicsSociety-MacroResearchDB/0.1"
AS_OF = dt.date.today().isoformat()
START_YEAR = 2000

WB_SERIES: dict[str, dict[str, str]] = {
    "NY.GDP.MKTP.CD": {"slug": "gdp_nominal_current_usd_annual", "name": "GDP (current US$)", "unit": "current US dollars", "description": "Annual nominal GDP at current US dollars; exchange-rate movements affect cross-country comparisons."},
    "NY.GDP.MKTP.CN": {"slug": "gdp_nominal_current_lcu_annual", "name": "GDP (current LCU)", "unit": "current local currency units", "description": "Annual nominal GDP in local currency, useful for within-country nominal growth."},
    "NY.GDP.MKTP.KD": {"slug": "gdp_real_constant_2015_usd_annual", "name": "GDP (constant 2015 US$)", "unit": "constant 2015 US dollars", "description": "Annual real GDP at constant 2015 US dollars."},
    "NY.GDP.MKTP.KN": {"slug": "gdp_real_constant_lcu_annual", "name": "GDP (constant LCU)", "unit": "constant local currency units (source base year varies)", "description": "Annual real GDP in constant local currency; base year is source-country specific."},
    "NY.GDP.PCAP.CD": {"slug": "gdp_per_capita_nominal_usd_annual", "name": "GDP per capita (current US$)", "unit": "current US dollars per person", "description": "Annual nominal GDP per capita at current US dollars."},
    "NY.GDP.PCAP.CN": {"slug": "gdp_per_capita_nominal_lcu_annual", "name": "GDP per capita (current LCU)", "unit": "current local currency units per person", "description": "Annual nominal GDP per capita in local currency."},
    "NY.GDP.PCAP.KD": {"slug": "gdp_per_capita_real_2015_usd_annual", "name": "GDP per capita (constant 2015 US$)", "unit": "constant 2015 US dollars per person", "description": "Annual real GDP per capita at constant 2015 US dollars."},
    "NY.GDP.PCAP.KN": {"slug": "gdp_per_capita_real_lcu_annual", "name": "GDP per capita (constant LCU)", "unit": "constant local currency units per person (source base year varies)", "description": "Annual real GDP per capita in constant local currency."},
    "NY.GDP.MKTP.KD.ZG": {"slug": "gdp_real_growth_yoy_pct_annual", "name": "GDP growth (annual %)", "unit": "percent per year", "description": "World Bank reported annual real GDP growth."},
    "NY.GDP.PCAP.KD.ZG": {"slug": "gdp_per_capita_real_growth_yoy_pct_annual", "name": "GDP per capita growth (annual %)", "unit": "percent per year", "description": "World Bank reported annual real GDP per-capita growth."},
    "NY.GNP.PCAP.PP.CD": {"slug": "gni_per_capita_ppp_current_intl_dollar_annual", "name": "GNI per capita, PPP (current international $)", "unit": "current international dollars per person", "description": "Gross national income per capita converted at purchasing power parity."},
    "SP.DYN.LE00.IN": {"slug": "life_expectancy_years_annual", "name": "Life expectancy at birth, total (years)", "unit": "years", "description": "Annual life expectancy at birth, total population."},
    "SL.UEM.TOTL.ZS": {"slug": "unemployment_rate_ilo_modeled_pct_annual", "name": "Unemployment, total (% of total labor force) (modeled ILO estimate)", "unit": "percent of labor force", "description": "ILO-modeled annual unemployment estimate; retain modeled status and source notes."},
    "SI.POV.DDAY": {"slug": "poverty_headcount_at_3usd_2021_ppp_pct_annual", "name": "Poverty headcount ratio at $3.00 a day (2021 PPP) (% of population)", "unit": "percent of population", "description": "World Bank international poverty measure at $3.00/day in 2021 PPP; observations follow survey availability and revisions."},
    "FP.CPI.TOTL": {"slug": "cpi_index_annual_wdi", "name": "Consumer price index (2010 = 100)", "unit": "index points (2010=100)", "description": "World Bank annual CPI index; not comparable across country index bases."},
    "FP.CPI.TOTL.ZG": {"slug": "inflation_consumer_prices_yoy_pct_annual_wdi", "name": "Inflation, consumer prices (annual %)", "unit": "percent per year", "description": "World Bank reported annual CPI inflation."},
    "SP.POP.TOTL": {"slug": "population_total_annual", "name": "Population, total", "unit": "people", "description": "World Bank annual population estimate; used to estimate quarterly GDP per capita."},
}

DERIVED_INDICATORS = [
    ("gdp_nominal_growth_yoy_pct_annual_lcu", "Nominal GDP growth, year over year (annual)", "percent per year", "Annual percent change calculated from nominal GDP in current local currency."),
    ("gdp_per_capita_nominal_growth_yoy_pct_annual_lcu", "Nominal GDP per-capita growth, year over year (annual)", "percent per year", "Annual percent change calculated from nominal GDP per capita in current local currency."),
    ("gdp_nominal_lcu_quarterly_sa", "Nominal GDP, quarterly, seasonally adjusted", "domestic currency at current prices (as reported)", "IMF QNEA GDP at current prices, domestic currency, seasonally adjusted."),
    ("gdp_nominal_lcu_quarterly_nsa", "Nominal GDP, quarterly, not seasonally adjusted", "domestic currency at current prices (as reported)", "IMF QNEA GDP at current prices, domestic currency, not seasonally adjusted."),
    ("gdp_real_lcu_quarterly_sa", "Real GDP, quarterly, seasonally adjusted", "chain-linked volume in domestic currency (as reported)", "IMF QNEA GDP at constant prices, domestic currency, seasonally adjusted; preserve IMF volume-index metadata."),
    ("gdp_real_lcu_quarterly_nsa", "Real GDP, quarterly, not seasonally adjusted", "chain-linked volume in domestic currency (as reported)", "IMF QNEA GDP at constant prices, domestic currency, not seasonally adjusted; preserve IMF volume-index metadata."),
    ("gdp_nominal_per_capita_lcu_quarterly_sa_estimate", "Nominal GDP per capita, quarterly, seasonally adjusted (estimated)", "current domestic currency per person per quarter", "Quarterly nominal GDP divided by World Bank annual population interpolated between mid-year observations (nearest reported value at series edges within one year); not an official quarterly per-capita series."),
    ("gdp_nominal_per_capita_lcu_quarterly_nsa_estimate", "Nominal GDP per capita, quarterly, not seasonally adjusted (estimated)", "current domestic currency per person per quarter", "Quarterly nominal GDP divided by World Bank annual population interpolated between mid-year observations (nearest reported value at series edges within one year); not an official quarterly per-capita series."),
    ("gdp_real_per_capita_lcu_quarterly_sa_estimate", "Real GDP per capita, quarterly, seasonally adjusted (estimated)", "chain-linked volume per person per quarter", "Quarterly real GDP divided by World Bank annual population interpolated between mid-year observations (nearest reported value at series edges within one year); not an official quarterly per-capita series."),
    ("gdp_real_per_capita_lcu_quarterly_nsa_estimate", "Real GDP per capita, quarterly, not seasonally adjusted (estimated)", "chain-linked volume per person per quarter", "Quarterly real GDP divided by World Bank annual population interpolated between mid-year observations (nearest reported value at series edges within one year); not an official quarterly per-capita series."),
    ("gdp_nominal_growth_yoy_pct_quarterly", "Nominal GDP growth, year over year (quarterly)", "percent", "Percent change from the same quarter one year earlier, using not-seasonally-adjusted current-price GDP."),
    ("gdp_real_growth_yoy_pct_quarterly", "Real GDP growth, year over year (quarterly)", "percent", "Percent change from the same quarter one year earlier, using not-seasonally-adjusted constant-price GDP."),
    ("gdp_nominal_per_capita_growth_yoy_pct_quarterly", "Nominal GDP per-capita growth, year over year (quarterly)", "percent", "Percent change from the same quarter one year earlier in estimated nominal GDP per capita."),
    ("gdp_real_per_capita_growth_yoy_pct_quarterly", "Real GDP per-capita growth, year over year (quarterly)", "percent", "Percent change from the same quarter one year earlier in estimated real GDP per capita."),
    ("gdp_nominal_growth_qoq_pct_quarterly_sa", "Nominal GDP growth, quarter over quarter (seasonally adjusted)", "percent", "Percent change from the previous quarter in seasonally adjusted nominal GDP; not annualized."),
    ("gdp_real_growth_qoq_pct_quarterly_sa", "Real GDP growth, quarter over quarter (seasonally adjusted)", "percent", "Percent change from the previous quarter in seasonally adjusted real GDP; not annualized."),
    ("gdp_nominal_per_capita_growth_qoq_pct_quarterly_sa", "Nominal GDP per-capita growth, quarter over quarter (seasonally adjusted)", "percent", "Percent change from the previous quarter in estimated seasonally adjusted nominal GDP per capita; not annualized."),
    ("gdp_real_per_capita_growth_qoq_pct_quarterly_sa", "Real GDP per-capita growth, quarter over quarter (seasonally adjusted)", "percent", "Percent change from the previous quarter in estimated seasonally adjusted real GDP per capita; not annualized."),
]

IMF_SLUGS = {
    ("QNEA", "V", "SA"): "gdp_nominal_lcu_quarterly_sa",
    ("QNEA", "V", "NSA"): "gdp_nominal_lcu_quarterly_nsa",
    ("QNEA", "Q", "SA"): "gdp_real_lcu_quarterly_sa",
    ("QNEA", "Q", "NSA"): "gdp_real_lcu_quarterly_nsa",
}


def request_bytes(url: str, accept: str, attempts: int = 4) -> bytes:
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    imf_key = os.environ.get("IMF_API_SUBSCRIPTION_KEY")
    if imf_key and "api.imf.org" in url:
        headers["Ocp-Apim-Subscription-Key"] = imf_key
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"Could not fetch {url}: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}")


def wb_json(url: str) -> Any:
    return json.loads(request_bytes(url, "application/json").decode("utf-8"))


def imf_xml(url: str) -> ET.Element:
    body = request_bytes(url, "application/vnd.sdmx.data+json;version=1.0.0")
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise RuntimeError(f"IMF response was not readable SDMX XML for {url}") from exc


def q(value: str) -> str:
    return urllib.parse.quote(value, safe="-_.~;:")


def get_countries() -> list[dict[str, Any]]:
    payload = wb_json(f"{WB_API}/country?format=json&per_page=400")
    countries = [row for row in payload[1] if row["region"]["value"].strip() != "Aggregates"]
    countries.sort(key=lambda row: row["id"])
    return countries


def wb_observations(codes: list[str], country_codes: set[str], end_year: int) -> tuple[list[dict[str, Any]], list[tuple[str, str, int, str]]]:
    code_path = ";".join(codes)
    per_page = 30000
    base = f"{WB_API}/country/all/indicator/{urllib.parse.quote(code_path, safe='.;') }?format=json&source=2&date={START_YEAR}:{end_year}&per_page={per_page}"
    first = wb_json(base + "&page=1")
    meta = first[0]
    all_rows = list(first[1] or [])
    for page in range(2, int(meta.get("pages", 1)) + 1):
        payload = wb_json(base + f"&page={page}")
        all_rows.extend(payload[1] or [])
    result = []
    for row in all_rows:
        country = row.get("countryiso3code") or row.get("country", {}).get("id")
        if country not in country_codes or row.get("value") is None:
            continue
        code = row.get("indicator", {}).get("id")
        if code not in WB_SERIES:
            continue
        result.append({
            "country": country,
            "code": code,
            "period": str(row["date"]),
            "value": float(row["value"]),
            "raw": str(row["value"]),
            "status": ("forecast" if row.get("obs_status") == "F" else "estimated" if row.get("obs_status") else "observed"),
            "metadata": {"obs_status": row.get("obs_status"), "decimal": row.get("decimal")},
        })
    return result, [("WB_WDI", base, len(all_rows), str(meta.get("lastupdated") or ""))]


def parse_imf_series(root: ET.Element) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series in root.iter():
        if not series.tag.endswith("Series"):
            continue
        dims = dict(series.attrib)
        for obs in list(series):
            if not obs.tag.endswith("Obs"):
                continue
            attrs = dict(obs.attrib)
            if "OBS_VALUE" not in attrs or "TIME_PERIOD" not in attrs:
                continue
            try:
                val = float(attrs["OBS_VALUE"])
            except ValueError:
                continue
            if not math.isfinite(val):
                continue
            rows.append({"dims": dims, "period": attrs["TIME_PERIOD"], "value": val, "raw": attrs["OBS_VALUE"], "obs_attrs": attrs})
    return rows


def imf_request(flow: str, key: str, start: str, end: str) -> tuple[list[dict[str, Any]], str]:
    path = f"{IMF_API}/data/IMF.STA,{flow}/{key}"
    url = f"{path}?startPeriod={urllib.parse.quote(start)}&endPeriod={urllib.parse.quote(end)}"
    return parse_imf_series(imf_xml(url)), url


def create_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE countries (
        serial_no INTEGER PRIMARY KEY,
        wb_code TEXT NOT NULL UNIQUE,
        iso2_code TEXT NOT NULL,
        country_name TEXT NOT NULL,
        wb_region_code TEXT,
        wb_region TEXT,
        income_level_code TEXT,
        income_level TEXT,
        lending_type_code TEXT,
        lending_type TEXT
    );
    CREATE TABLE sources (
        source_id TEXT PRIMARY KEY,
        publisher TEXT NOT NULL,
        dataset_title TEXT NOT NULL,
        landing_url TEXT NOT NULL,
        api_url TEXT,
        terms TEXT NOT NULL,
        attribution TEXT NOT NULL,
        accessed_on TEXT NOT NULL
    );
    CREATE TABLE indicators (
        indicator_code TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        frequency TEXT NOT NULL,
        unit TEXT NOT NULL,
        definition TEXT NOT NULL,
        source_id TEXT REFERENCES sources(source_id),
        source_series_code TEXT,
        calculation TEXT,
        availability TEXT NOT NULL DEFAULT 'loaded'
    );
    CREATE TABLE source_snapshots (
        snapshot_id INTEGER PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        retrieved_at TEXT NOT NULL,
        request_url TEXT NOT NULL,
        returned_rows INTEGER NOT NULL,
        provider_update_date TEXT,
        notes TEXT
    );
    CREATE TABLE source_series_metadata (
        country_code TEXT NOT NULL REFERENCES countries(wb_code),
        indicator_code TEXT NOT NULL REFERENCES indicators(indicator_code),
        source_series_code TEXT NOT NULL,
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        seasonal_adjustment TEXT,
        series_metadata TEXT NOT NULL,
        PRIMARY KEY(country_code, indicator_code, source_series_code)
    );
    CREATE TABLE observations (
        country_code TEXT NOT NULL REFERENCES countries(wb_code),
        indicator_code TEXT NOT NULL REFERENCES indicators(indicator_code),
        period TEXT NOT NULL,
        frequency TEXT NOT NULL,
        value REAL NOT NULL,
        raw_value TEXT,
        value_status TEXT NOT NULL CHECK(value_status IN ('observed','estimated','derived','forecast')),
        source_id TEXT NOT NULL REFERENCES sources(source_id),
        source_series_code TEXT NOT NULL,
        seasonal_adjustment TEXT,
        observation_metadata TEXT,
        PRIMARY KEY(country_code, indicator_code, period, source_series_code)
    );
    CREATE INDEX idx_observations_period ON observations(period);
    CREATE INDEX idx_observations_indicator_period ON observations(indicator_code, period);
    CREATE INDEX idx_observations_country_indicator ON observations(country_code, indicator_code);
    CREATE VIEW indicator_coverage AS
        SELECT indicator_code, frequency, COUNT(*) AS observation_count,
               COUNT(DISTINCT country_code) AS economy_count,
               MIN(period) AS first_period, MAX(period) AS last_period
        FROM observations GROUP BY indicator_code, frequency;
    CREATE VIEW research_observations AS
        SELECT c.serial_no, c.wb_code, c.country_name, c.wb_region, c.income_level,
               i.indicator_code, i.display_name, o.period, o.frequency, o.value,
               i.unit, o.value_status, o.seasonal_adjustment, o.source_id,
               o.source_series_code, o.observation_metadata
        FROM observations o
        JOIN countries c ON c.wb_code=o.country_code
        JOIN indicators i ON i.indicator_code=o.indicator_code;
    """)
    return conn


def quarter_index(period: str) -> int:
    year, quarter = period.split("-Q")
    return int(year) * 4 + int(quarter) - 1


def quarter_pop(pop: dict[int, float], period: str) -> float | None:
    year, quarter = period.split("-Q")
    y = int(year)
    qn = int(quarter)
    target = y + (qn - 0.5) / 4.0
    points = sorted((yr + 0.5, value) for yr, value in pop.items())
    if not points:
        return None
    if target <= points[0][0]:
        return points[0][1] if points[0][0] - target <= 0.5 else None
    if target >= points[-1][0]:
        return points[-1][1] if target - points[-1][0] <= 1.0 else None
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= target <= t1:
            weight = (target - t0) / (t1 - t0)
            return v0 + weight * (v1 - v0)
    return None


def add_indicator(conn: sqlite3.Connection, code: str, name: str, frequency: str, unit: str, definition: str, source_id: str | None, series_code: str | None, calculation: str | None = None, availability: str = "loaded") -> None:
    conn.execute("INSERT OR REPLACE INTO indicators VALUES(?,?,?,?,?,?,?,?,?)", (code,name,frequency,unit,definition,source_id,series_code,calculation,availability))


def add_observation(conn: sqlite3.Connection, *, country: str, indicator: str, period: str, frequency: str, value: float, raw: str | None, status: str, source: str, series_code: str, seasonal: str | None = None, metadata: dict[str, Any] | None = None, series_metadata: dict[str, Any] | None = None) -> None:
    if not math.isfinite(value):
        return
    if series_metadata is not None:
        conn.execute("""INSERT OR REPLACE INTO source_series_metadata
            (country_code,indicator_code,source_series_code,source_id,seasonal_adjustment,series_metadata)
            VALUES(?,?,?,?,?,?)""",
            (country,indicator,series_code,source,seasonal,json.dumps(series_metadata,ensure_ascii=False,sort_keys=True)))
    conn.execute("""INSERT OR REPLACE INTO observations
        (country_code,indicator_code,period,frequency,value,raw_value,value_status,source_id,source_series_code,seasonal_adjustment,observation_metadata)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (country,indicator,period,frequency,value,raw,status,source,series_code,seasonal,
         json.dumps(metadata,ensure_ascii=False,sort_keys=True) if metadata else None))


def main() -> int:
    global START_YEAR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("database/macro_research.sqlite"), help="SQLite output path (default: database/macro_research.sqlite)")
    parser.add_argument("--start-year", type=int, default=START_YEAR, help="First year/quarter/month to retrieve (default: 2000)")
    parser.add_argument("--include-imf", action="store_true", help="Also fetch IMF quarterly/monthly datasets; requires IMF_API_SUBSCRIPTION_KEY in the environment")
    args = parser.parse_args()
    START_YEAR = args.start_year
    if args.include_imf and not os.environ.get("IMF_API_SUBSCRIPTION_KEY"):
        parser.error("--include-imf requires IMF_API_SUBSCRIPTION_KEY; do not put the key in the repository or command history")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    end_year = dt.date.today().year
    end_month = dt.date.today().month
    countries = get_countries()
    country_codes = {row["id"] for row in countries}
    print(f"World Bank country/economy rows: {len(countries)}", flush=True)

    conn = create_database(args.output)
    conn.executemany("INSERT INTO countries VALUES(?,?,?,?,?,?,?,?,?,?)", [
        (i,row["id"],row["iso2Code"],row["name"],row["region"]["id"],row["region"]["value"].strip(),row["incomeLevel"]["id"],row["incomeLevel"]["value"].strip(),row["lendingType"]["id"],row["lendingType"]["value"].strip())
        for i,row in enumerate(countries,1)
    ])
    source_rows = [
        ("WB_WDI","World Bank","World Development Indicators","https://datacatalog.worldbank.org/search/dataset/0037712/world-development-indicators",f"{WB_API}/v2","CC BY 4.0","Source: World Bank, World Development Indicators, indicator codes shown in the database. Licensed under CC BY 4.0.",AS_OF),
        ("IMF_QNEA","International Monetary Fund","National Economic Accounts (NEA), Quarterly Data","https://data.imf.org/en/datasets/IMF.STA%3AQNEA",f"{IMF_API}/data/IMF.STA,QNEA","IMF statistical-data use terms; cite IMF and dataset; preserve any third-party attribution.","Source: International Monetary Fund, National Economic Accounts (NEA), Quarterly Data.",AS_OF),
        ("IMF_CPI","International Monetary Fund","Consumer Price Index (CPI)","https://data.imf.org/en/datasets/IMF.STA%3ACPI",f"{IMF_API}/data/IMF.STA,CPI","IMF statistical-data use terms; cite IMF and dataset; preserve any third-party attribution.","Source: International Monetary Fund, Consumer Price Index (CPI).",AS_OF),
        ("IMF_MFS_IR","International Monetary Fund","Monetary and Financial Statistics (MFS), Interest Rate","https://data.imf.org/en/datasets/IMF.STA%3AMFS_IR",f"{IMF_API}/data/IMF.STA,MFS_IR","IMF statistical-data use terms; cite IMF and dataset; preserve any third-party attribution.","Source: International Monetary Fund, Monetary and Financial Statistics (MFS), Interest Rate.",AS_OF),
        ("IMF_PPI","International Monetary Fund","Producer Price Index (PPI)","https://data.imf.org/en/datasets/IMF.STA%3APPI",f"{IMF_API}/data/IMF.STA,PPI","IMF statistical-data use terms; cite IMF and dataset; preserve any third-party attribution.","Source: International Monetary Fund, Producer Price Index (PPI). This is not a wholesale price index.",AS_OF),
    ]
    conn.executemany("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)", source_rows)
    conn.execute("INSERT INTO schema_meta VALUES('schema_version','1.0')")
    conn.execute("INSERT INTO schema_meta VALUES('release_date',?)", (AS_OF,))
    conn.execute("INSERT INTO schema_meta VALUES('historical_start',?)", (str(START_YEAR),))
    conn.execute("INSERT INTO schema_meta VALUES('country_source_order','World Bank three-letter code order')")
    conn.commit()

    # Catalog entries for World Bank indicators.
    wb_slug_by_code = {}
    for code, info in WB_SERIES.items():
        wb_slug_by_code[code] = info["slug"]
        add_indicator(conn, info["slug"], info["name"], "annual", info["unit"], info["description"], "WB_WDI", code,
                      "World Bank Indicators API, source=2 (WDI); calendar-year observation.")
    for code, name, unit, definition in DERIVED_INDICATORS:
        add_indicator(conn, code, name, "quarterly" if "quarterly" in code else "annual", unit, definition,
                      "IMF_QNEA" if "quarterly" in code else "WB_WDI", None, definition)
    add_indicator(conn,"cpi_index_all_items_monthly_imf","CPI, all items (monthly)","monthly","index points (national reference period varies)","IMF all-items CPI index. Index reference period is preserved in observation metadata; do not compare index levels across economies.","IMF_CPI","CPI._T.IX.M")
    add_indicator(conn,"inflation_consumer_prices_yoy_pct_monthly_imf","CPI inflation, year over year (monthly)","monthly","percent","IMF-reported year-on-year percent change in the all-items CPI.","IMF_CPI","CPI._T.YOY_PCH_PA_PT.M")
    add_indicator(conn,"central_bank_policy_rate_pct_pa_monthly_imf","Central bank policy rate","monthly","percent per annum","IMF MFS_IR series MFS166_RT_PT_A_PT; reported monthly policy/central-bank rate.","IMF_MFS_IR","MFS166_RT_PT_A_PT.M")
    add_indicator(conn,"producer_price_index_monthly_imf","Producer price index (monthly)","monthly","index points (reference period varies)","IMF PPI index. This is a related producer-price measure, not a wholesale price index (WPI).","IMF_PPI","PPI.IX.M")
    add_indicator(conn,"wholesale_price_index_monthly","Wholesale price index (WPI)","monthly","index points (country-specific base)","Country-specific WPI series are not harmonized into this global release. IMF PPI is stored separately and must not be relabelled as WPI.",None,None,availability="not_loaded_country_specific_sources_required")

    wb_codes = list(WB_SERIES)
    wb_data, wb_snapshots = wb_observations(wb_codes, country_codes, end_year)
    for rec in wb_data:
        info = WB_SERIES[rec["code"]]
        add_observation(conn,country=rec["country"],indicator=info["slug"],period=rec["period"],frequency="annual",value=rec["value"],raw=rec["raw"],status=rec["status"],source="WB_WDI",series_code=rec["code"],metadata=rec["metadata"])
    conn.executemany("INSERT INTO source_snapshots(source_id,retrieved_at,request_url,returned_rows,provider_update_date,notes) VALUES(?,?,?,?,?,?)",
                     [(sid,now,url,n,updated,"World Bank API page query; aggregates filtered to WDI country/economy list.") for sid,url,n,updated in wb_snapshots])
    conn.commit()
    print(f"World Bank observations loaded: {len(wb_data):,}", flush=True)

    # Preserve raw annual inputs for calculated nominal growth and quarterly per-capita estimates.
    wb_values: dict[tuple[str,str], dict[int,float]] = defaultdict(dict)
    for rec in wb_data:
        try:
            year = int(rec["period"])
        except ValueError:
            continue
        wb_values[(rec["country"],rec["code"])][year] = rec["value"]

    # Annual nominal growth is calculated in local currency, avoiding exchange-rate-driven USD changes.
    derived_pairs = [
        ("NY.GDP.MKTP.CN","gdp_nominal_growth_yoy_pct_annual_lcu"),
        ("NY.GDP.PCAP.CN","gdp_per_capita_nominal_growth_yoy_pct_annual_lcu"),
    ]
    for (country, code), values in wb_values.items():
        for input_code, out_code in derived_pairs:
            if code != input_code:
                continue
            for year, val in values.items():
                previous = values.get(year-1)
                if previous is None or previous == 0:
                    continue
                result = (val / previous - 1.0) * 100.0
                add_observation(conn,country=country,indicator=out_code,period=str(year),frequency="annual",value=result,raw=None,status="derived",source="WB_WDI",series_code=f"calculated:{input_code}:year_over_year",metadata={"formula":"100 * (current_year / prior_year - 1)","nominal_growth_basis":"current local currency"})
    conn.commit()

    if not args.include_imf:
        conn.execute("UPDATE indicators SET availability='awaiting_imf_api_access' WHERE source_id IN ('IMF_QNEA','IMF_CPI','IMF_MFS_IR','IMF_PPI')")
        conn.execute("UPDATE indicators SET availability='country_specific_sources_required' WHERE indicator_code='wholesale_price_index_monthly'")
        for source_id,url,note in [
            ("IMF_QNEA","https://data.imf.org/en/datasets/IMF.STA%3AQNEA","Not fetched: IMF API access was not configured for this build."),
            ("IMF_CPI","https://data.imf.org/en/datasets/IMF.STA%3ACPI","Not fetched: IMF API access was not configured for this build."),
            ("IMF_MFS_IR","https://data.imf.org/en/datasets/IMF.STA%3AMFS_IR","Not fetched: IMF API access was not configured for this build."),
            ("IMF_PPI","https://data.imf.org/en/datasets/IMF.STA%3APPI","Not fetched: IMF API access was not configured for this build."),
        ]:
            conn.execute("INSERT INTO source_snapshots(source_id,retrieved_at,request_url,returned_rows,provider_update_date,notes) VALUES(?,?,?,?,?,?)",(source_id,now,url,0,None,note))
        conn.execute("INSERT INTO schema_meta VALUES('frequency_policy','Populated release: annual WDI observations from 2000. Quarterly and monthly IMF indicators are registered but not populated until IMF API access is configured.')")
        conn.execute("INSERT INTO schema_meta VALUES('gap_policy','No zero-fill or unlabelled interpolation. Annual poverty observations retain their survey-based frequency. Missing IMF monthly and quarterly values remain absent.')")
        conn.commit()
        conn.execute("PRAGMA optimize")
        conn.close()
        print(f"SQLite release saved: {args.output}", flush=True)
        print("WDI annual data are populated. IMF indicators are registered and marked awaiting_imf_api_access.", flush=True)
        return 0

    # IMF QNEA: nominal (V) and real chain-volume (Q), in domestic currency.
    end_quarter = 4 if end_month in (10,11,12) else (end_month-1)//3 + 1
    q_end = f"{end_year}-Q{end_quarter}"
    q_start = f"{START_YEAR}-Q1"
    qne_records: list[dict[str,Any]] = []
    qne_snapshots = []
    qne_defs = [("V","SA"),("V","NSA"),("Q","SA"),("Q","NSA")]
    for price_type, adjustment in qne_defs:
        key=f".B1GQ.{price_type}.{adjustment}.XDC.Q"
        rows,url=imf_request("QNEA",key,q_start,q_end)
        qne_snapshots.append(("IMF_QNEA",now,url,len(rows),None,f"QNEA GDP; PRICE_TYPE={price_type}; S_ADJUSTMENT={adjustment}; XDC; Q."))
        slug=IMF_SLUGS[("QNEA",price_type,adjustment)]
        for row in rows:
            dims=row["dims"]
            country=dims.get("COUNTRY")
            if country not in country_codes:
                continue
            series_code=f"QNEA|{dims.get('INDICATOR')}|{price_type}|{adjustment}|{dims.get('TYPE_OF_TRANSFORMATION')}|{dims.get('FREQUENCY')}"
            attrs={k:v for k,v in row["obs_attrs"].items() if k not in ("TIME_PERIOD","OBS_VALUE")}
            add_observation(conn,country=country,indicator=slug,period=row["period"],frequency="quarterly",value=row["value"],raw=row["raw"],status="observed",source="IMF_QNEA",series_code=series_code,seasonal=adjustment,metadata=attrs,series_metadata=dims)
            qne_records.append({"country":country,"slug":slug,"period":row["period"],"value":row["value"],"series_code":series_code,"seasonal":adjustment})
    conn.executemany("INSERT INTO source_snapshots(source_id,retrieved_at,request_url,returned_rows,provider_update_date,notes) VALUES(?,?,?,?,?,?)",qne_snapshots)
    conn.commit()
    print(f"IMF quarterly GDP observations loaded: {len(qne_records):,}", flush=True)

    # Estimate quarterly per-capita levels by dividing GDP by linearly interpolated annual mid-year population.
    population_by_country = {country: wb_values.get((country,"SP.POP.TOTL"),{}) for country in country_codes}
    q_levels: dict[tuple[str,str,str],dict[str,float]] = defaultdict(dict)
    q_meta: dict[tuple[str,str,str],dict[str,Any]] = {}
    for rec in qne_records:
        q_levels[(rec["country"],rec["slug"],rec["seasonal"])][rec["period"]]=rec["value"]
        q_meta[(rec["country"],rec["slug"],rec["seasonal"])]=rec
    pc_slug = {
        ("gdp_nominal_lcu_quarterly_sa","SA"):"gdp_nominal_per_capita_lcu_quarterly_sa_estimate",
        ("gdp_nominal_lcu_quarterly_nsa","NSA"):"gdp_nominal_per_capita_lcu_quarterly_nsa_estimate",
        ("gdp_real_lcu_quarterly_sa","SA"):"gdp_real_per_capita_lcu_quarterly_sa_estimate",
        ("gdp_real_lcu_quarterly_nsa","NSA"):"gdp_real_per_capita_lcu_quarterly_nsa_estimate",
    }
    pc_levels: dict[tuple[str,str],dict[str,float]] = defaultdict(dict)
    for (country,slug,adjustment), values in q_levels.items():
        out_slug=pc_slug[(slug,adjustment)]
        for period,value in values.items():
            population=quarter_pop(population_by_country.get(country,{}),period)
            if not population or population <= 0:
                continue
            pc_value=value/population
            source_rec=q_meta[(country,slug,adjustment)]
            series_code=f"derived_per_capita|{source_rec['series_code']}|population=SP.POP.TOTL_interpolated"
            add_observation(conn,country=country,indicator=out_slug,period=period,frequency="quarterly",value=pc_value,raw=None,status="estimated",source="IMF_QNEA",series_code=series_code,seasonal=adjustment)
            pc_levels[(country,out_slug)][period]=pc_value

    def derive_quarter_growth(slug: str, result_slug: str, lag: int, suffix: str) -> None:
        if "per_capita" in slug:
            items = [(country, base_slug, values) for (country, base_slug), values in pc_levels.items() if base_slug == slug]
        else:
            items = [(country, base_slug, values) for (country, base_slug, adjustment), values in q_levels.items() if base_slug == slug and adjustment == suffix]
        for country, base_slug, values in items:
            for period,value in values.items():
                index=quarter_index(period)
                target=index-lag
                prev_year,rem=divmod(target,4)
                prev_period=f"{prev_year}-Q{rem+1}"
                old=values.get(prev_period)
                if old is None or old==0:
                    continue
                source_id="IMF_QNEA"
                basis="same quarter prior year" if lag==4 else "previous quarter"
                key=f"derived:{slug}:{basis}:{suffix}"
                add_observation(conn,country=country,indicator=result_slug,period=period,frequency="quarterly",value=(value/old-1)*100,raw=None,status="derived",source=source_id,series_code=key,seasonal=suffix)

    # Same-quarter-year growth uses NSA levels; quarter-over-quarter growth uses SA levels.
    for base,out in [
        ("gdp_nominal_lcu_quarterly_nsa","gdp_nominal_growth_yoy_pct_quarterly"),
        ("gdp_real_lcu_quarterly_nsa","gdp_real_growth_yoy_pct_quarterly"),
    ]:
        derive_quarter_growth(base,out,4,"NSA")
    for base,out in [
        ("gdp_nominal_per_capita_lcu_quarterly_nsa_estimate","gdp_nominal_per_capita_growth_yoy_pct_quarterly"),
        ("gdp_real_per_capita_lcu_quarterly_nsa_estimate","gdp_real_per_capita_growth_yoy_pct_quarterly"),
    ]:
        derive_quarter_growth(base,out,4,"NSA")
    for base,out in [
        ("gdp_nominal_lcu_quarterly_sa","gdp_nominal_growth_qoq_pct_quarterly_sa"),
        ("gdp_real_lcu_quarterly_sa","gdp_real_growth_qoq_pct_quarterly_sa"),
    ]:
        derive_quarter_growth(base,out,1,"SA")
    for base,out in [
        ("gdp_nominal_per_capita_lcu_quarterly_sa_estimate","gdp_nominal_per_capita_growth_qoq_pct_quarterly_sa"),
        ("gdp_real_per_capita_lcu_quarterly_sa_estimate","gdp_real_per_capita_growth_qoq_pct_quarterly_sa"),
    ]:
        derive_quarter_growth(base,out,1,"SA")
    conn.commit()

    # IMF monthly CPI: all-items index and source-reported year-on-year rate.
    month_end=f"{end_year}-M{end_month:02d}"
    month_start=f"{START_YEAR}-M01"
    cpi_defs=[(".CPI._T.IX.M","cpi_index_all_items_monthly_imf","IMF CPI all-items index (IX)."),
              (".CPI._T.YOY_PCH_PA_PT.M","inflation_consumer_prices_yoy_pct_monthly_imf","IMF all-items CPI year-on-year percentage change.")]
    cpi_snaps=[]
    for key,slug,note in cpi_defs:
        rows,url=imf_request("CPI",key,month_start,month_end)
        cpi_snaps.append(("IMF_CPI",now,url,len(rows),None,note))
        for row in rows:
            country=row["dims"].get("COUNTRY")
            if country not in country_codes:
                continue
            dims=row["dims"]
            series_code=f"CPI|{dims.get('INDEX_TYPE')}|{dims.get('COICOP_1999')}|{dims.get('TYPE_OF_TRANSFORMATION')}|{dims.get('FREQUENCY')}"
            ref={k:v for k,v in row["obs_attrs"].items() if k not in ("TIME_PERIOD","OBS_VALUE")}
            status="estimated" if row["obs_attrs"].get("DERIVATION_TYPE","O")!="O" else "observed"
            add_observation(conn,country=country,indicator=slug,period=row["period"],frequency="monthly",value=row["value"],raw=row["raw"],status=status,source="IMF_CPI",series_code=series_code,metadata=ref,series_metadata=dims)
    conn.executemany("INSERT INTO source_snapshots(source_id,retrieved_at,request_url,returned_rows,provider_update_date,notes) VALUES(?,?,?,?,?,?)",cpi_snaps)
    conn.commit()
    print(f"IMF monthly CPI observations loaded: {sum(x[3] for x in cpi_snaps):,}", flush=True)

    # IMF monthly central-bank policy rate and producer-price index.
    high_freq=[
        ("MFS_IR",".MFS166_RT_PT_A_PT.M","central_bank_policy_rate_pct_pa_monthly_imf","IMF_MFS_IR","MFS166_RT_PT_A_PT.M","Central bank policy rate, percent per annum."),
        ("PPI",".PPI.IX.M","producer_price_index_monthly_imf","IMF_PPI","PPI.IX.M","Producer price index; retained separately from WPI."),
    ]
    for flow,key,slug,source,series_prefix,note in high_freq:
        rows,url=imf_request(flow,key,month_start,month_end)
        conn.execute("INSERT INTO source_snapshots(source_id,retrieved_at,request_url,returned_rows,provider_update_date,notes) VALUES(?,?,?,?,?,?)",(source,now,url,len(rows),None,note))
        for row in rows:
            country=row["dims"].get("COUNTRY")
            if country not in country_codes:
                continue
            dims=row["dims"]
            series_code=f"{flow}|"+"|".join(f"{k}={dims[k]}" for k in sorted(dims) if k != "COUNTRY")
            attrs={k:v for k,v in row["obs_attrs"].items() if k not in ("TIME_PERIOD","OBS_VALUE")}
            status="estimated" if row["obs_attrs"].get("DERIVATION_TYPE","O")!="O" else "observed"
            add_observation(conn,country=country,indicator=slug,period=row["period"],frequency="monthly",value=row["value"],raw=row["raw"],status=status,source=source,series_code=series_code,metadata=attrs,series_metadata=dims)
        conn.commit()
        print(f"{flow} observations loaded: {len(rows):,} (before WDI-country filter)", flush=True)

    # A CPI series without monthly data or a quarterly series without a reported value stays absent.
    # Absence means unavailable; it is never converted to zero or silently interpolated.
    conn.execute("INSERT INTO schema_meta VALUES('retrieved_at_utc',?)", (now,))
    conn.execute("INSERT INTO schema_meta VALUES('frequency_policy','Quarterly national accounts from IMF QNEA where reported; monthly CPI, policy rate and PPI from IMF; annual WDI indicators from 2000; poverty retains survey-based observations.')")
    conn.execute("INSERT INTO schema_meta VALUES('gap_policy','No zero-fill or unlabelled interpolation. Derived quarterly GDP per capita is marked estimated and documents population interpolation.')")
    conn.commit()
    conn.execute("PRAGMA optimize")
    conn.close()
    print(f"SQLite release saved: {args.output}", flush=True)
    print("Economy counts and actual date ranges are available in the indicator_coverage view.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
