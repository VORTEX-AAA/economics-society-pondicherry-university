# Research data database

This directory contains the first PostgreSQL schema for the Economics Society's research starter pack.

## What it stores

- **Sources**: publisher, dataset links, citations, licenses, and access dates.
- **Datasets and releases**: subject, scope, review status, version, and changelog.
- **Series**: indicator definitions, units, frequency, geography, and adjustments.
- **Observations**: cleaned values alongside the original source value and any status or note.
- **Cleaning runs**: the script and code revision used to produce a dataset release.

## Apply the schema

Create an empty PostgreSQL database, then run:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/schema.sql
```

Keep credentials in a local environment file or a managed secret store. Never commit database passwords, API keys, or private participant data.

## Data conventions

- Store every observation's period as the first calendar date of its period: January 1 for annual data, the first day of the quarter for quarterly data, and the first day of the month for monthly data.
- Store values in the unit declared by the series. Preserve the source representation in `raw_value`.
- Use a null `value` with status `missing` for unavailable values; explain meaningful gaps or transformations in notes.
- Record source, license, citation, and cleaning details before marking a dataset `released`.
- Version releases when cleaning or source revisions change the delivered data.

## Next step

Choose the first public source datasets and confirm their redistribution terms. Then add reproducible import/cleaning scripts, a data dictionary, and versioned starter data.
