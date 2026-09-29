# Macroeconomic research database

The society’s portable research snapshot is `macro_research.sqlite.gz`. It is a compressed SQLite database; decompress it once to use the SQLite file directly.

## What is in this release

Retrieved on **2026-09-29**, the snapshot includes:

- The 217 World Bank country/economy entries, with World Bank codes, ISO-2 codes, regions, income groups, and lending types.
- **96,475 annual observations** from World Development Indicators, from 2000 through each indicator’s latest available year.
- 19 populated indicator series, including source-reported data and clearly marked calculated nominal-growth series.

Populated indicators include nominal GDP and real GDP (in US dollars and local currency), nominal and real GDP per capita, annual real GDP growth, real GDP-per-capita growth, nominal GDP growth calculated in local currency, GNI per capita at PPP, life expectancy, ILO-modelled unemployment, the $3.00/day poverty measure in 2021 PPP, annual CPI and inflation, and population.

Coverage varies by indicator. For example, the current snapshot has life-expectancy observations through 2024 and most other WDI series through 2025. Missing values are absent, not filled with zero. Check actual coverage with the `indicator_coverage` view.

Use `wb_code` (the World Bank three-letter code) to join country data. `serial_no` is only a convenient row number, not a ranking or permanent identifier.

## Quarterly and monthly series

The indicator catalog also defines the planned quarterly GDP and GDP-per-capita series, monthly CPI and inflation, central-bank policy rate, and producer-price index. These **are not populated in this snapshot**: the IMF API returned HTTP 401 during retrieval in this environment. The refresh script supports them when IMF API access is configured. IMF identifies QNEA as its quarterly national-accounts source and publishes separate CPI, interest-rate, and producer-price datasets. [IMF data API](https://data.imf.org/en/Resource-Pages/IMF-API) · [QNEA](https://data.imf.org/en/datasets/IMF.STA%3AQNEA)

WPI is not filled using PPI: those are different price measures. The global catalog marks WPI as requiring country-specific sources, while IMF PPI is a separate registered indicator.

## Open and query the snapshot

From the repository root:

```sh
gzip -dk database/macro_research.sqlite.gz
sqlite3 database/macro_research.sqlite
```

Example query:

```sql
SELECT c.country_name, o.period, o.value
FROM observations AS o
JOIN countries AS c ON c.wb_code = o.country_code
WHERE o.country_code = 'IND'
  AND o.indicator_code = 'gdp_real_growth_yoy_pct_annual'
ORDER BY o.period;
```

The SQLite file includes `countries`, `sources`, `indicators`, `observations`, and `source_snapshots` tables, plus `indicator_coverage` and `research_observations` views. Observation rows retain the indicator code, original source-series code, raw numeric text, and whether the value is observed, estimated, derived, or forecast.

## Refresh the data

The builder uses Python’s standard library and the World Bank API; it needs an internet connection.

```sh
python3 database/scripts/refresh_macro_database.py --output database/macro_research.sqlite
```

That command refreshes the annual WDI release. IMF quarterly and monthly series require an IMF API subscription key. Configure `IMF_API_SUBSCRIPTION_KEY` in a local environment or secret manager, then run:

```sh
python3 database/scripts/refresh_macro_database.py --include-imf --output database/macro_research.sqlite
```

Never commit the key. The resulting IMF observations keep IMF source-series metadata and citations, and per-person quarterly GDP is marked estimated because it uses interpolated annual population. Growth definitions distinguish year-over-year rates from non-annualized quarter-over-quarter rates.

## Sources and reuse

The populated World Bank data are from [World Development Indicators](https://datacatalog.worldbank.org/search/dataset/0037712/world-development-indicators), licensed **CC BY 4.0**. Attribute the World Bank and the specific indicator codes when reusing the data. [World Bank API documentation](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392)

IMF data are not included in this release. When added, cite the IMF dataset and follow its [statistical-data usage terms](https://www.imf.org/en/about/copyright-and-terms).

`schema.sql` and `wdi_countries.sql` provide the PostgreSQL design/seed for a later hosted database. The SQLite snapshot is the database members can use locally or in project repositories now.
