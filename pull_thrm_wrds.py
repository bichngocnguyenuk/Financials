#!/usr/bin/env python3
"""
pull_thrm_wrds.py

Pull Compustat (via WRDS) fundamental data for Gentherm Inc. (ticker: THRM)
and save it to CSV for downstream financial-statement analysis.

Pulls:
  - Annual Compustat data (comp.funda), fiscal years 2010-2025
  - Quarterly Compustat data (comp.fundq), Q1 2025 and Q1 2026 (for YoY comparison)

Run on macOS from Terminal with:
    python3 pull_thrm_wrds.py

Requirements:
    pip install sqlalchemy psycopg2-binary pandas wrds
"""

import sys
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

# ---------------------------------------------------------------------------
# USER CONFIGURATION
# ---------------------------------------------------------------------------
WRDS_USERNAME = "YOUR_USERNAME_HERE"
WRDS_PASSWORD = "YOUR_PASSWORD_HERE"

WRDS_HOST = "wrds-pgdata.wharton.upenn.edu"
WRDS_PORT = 9737
WRDS_DBNAME = "wrds"

TICKER = "THRM"               # Gentherm Inc.
ANNUAL_START_FY = 2010
ANNUAL_END_FY = 2025

ANNUAL_OUTPUT_FILE = "thrm_annual.csv"
QUARTERLY_OUTPUT_FILE = "thrm_q1_2026.csv"

# ---------------------------------------------------------------------------
# COMPUSTAT FIELD MAPPING
# ---------------------------------------------------------------------------
# Maps each financial-statement line item to its Compustat variable name(s).
# Annual data comes from comp.funda; quarterly data comes from comp.fundq
# (quarterly variables carry the same root name with a "q" suffix, e.g.
# SALE -> SALEQ, and YTD cash-flow variables carry a "y" suffix, e.g.
# OANCF -> OANCFY).
#
# Financial Statement Label      | Compustat Variable (Annual / Quarterly)
# --------------------------------------------------------------------------
# Revenue / Sales                | SALE   / SALEQ
# COGS                           | COGS   / COGSQ
# Gross Profit                   | (computed: SALE - COGS)
# SG&A                           | XSGA   / XSGAQ
# R&D                            | XRD    / XRDQ
# Depreciation & Amortization    | DP     / DPQ
# EBITDA                         | (computed: OIBDP, also reported directly
#                                   as OIBDP / OIBDPQ)
# EBIT / Operating Income        | OIADP  / OIADPQ   (operating income after D&A)
# Non-operating income           | NOPI   / NOPIQ
# Interest expense               | XINT   / XINTQ
# Pre-tax income                 | PI     / PIQ
# Income tax                     | TXT    / TXTQ
# Net income                     | NI     / NIQ
# EPS (diluted)                  | EPSFX  / EPSFXQ   (EPS - diluted, excl. items)
# EPS (basic)                    | EPSPX  / EPSPXQ   (EPS - basic, excl. items)
# Cash and equivalents           | CHE    / CHEQ
# Accounts receivable            | RECT   / RECTQ
# Inventory                      | INVT   / INVTQ
# Current assets                 | ACT    / ACTQ
# PP&E (net)                     | PPENT  / PPENTQ
# Goodwill                       | GDWL   / GDWLQ
# Intangible assets              | INTAN  / INTANQ
# Total assets                   | AT     / ATQ
# Accounts payable               | AP     / APQ
# Current liabilities            | LCT    / LCTQ
# Short-term debt                | DLC    / DLCQ
# Long-term debt                 | DLTT   / DLTTQ
# Total liabilities              | LT     / LTQ
# Shareholders' equity           | SEQ    / SEQQ
# Operating cash flow            | OANCF  / OANCFY (YTD, quarterly fundq)
# Capital expenditures           | CAPX   / CAPXY  (YTD, quarterly fundq)
# Investing cash flow            | IVNCF  / IVNCFY (YTD, quarterly fundq)
# Financing cash flow            | FINCF  / FINCFY (YTD, quarterly fundq)
# --------------------------------------------------------------------------
# Notes:
#  - comp.fundq cash-flow-statement items (OANCF, CAPX, IVNCF, FINCF) are
#    only populated on a year-to-date basis via the *Y variables
#    (OANCFY, CAPXY, IVNCFY, FINCFY), reset each fiscal year at Q1. For
#    Q1, the YTD figure equals the quarter's own cash flow.
#  - EBITDA is not a native Compustat field; it is computed as
#    OIBDP (Operating Income Before Depreciation), which is Compustat's
#    standard EBITDA proxy, and cross-checked against SALE - COGS - XSGA.
#  - Missing/unreported items are left as NaN (no forced fill), consistent
#    with Compustat's as-reported convention.

ANNUAL_FIELDS = [
    "gvkey", "datadate", "fyear", "tic", "conm", "cusip",
    "sale", "cogs", "xsga", "xrd", "dp", "oibdp", "oiadp",
    "nopi", "xint", "pi", "txt", "ni", "epsfx", "epspx",
    "che", "rect", "invt", "act", "ppent", "gdwl", "intan", "at",
    "ap", "lct", "dlc", "dltt", "lt", "seq",
    "oancf", "capx", "ivncf", "fincf",
]

QUARTERLY_FIELDS = [
    "gvkey", "datadate", "fyearq", "fqtr", "tic", "conm", "cusip",
    "saleq", "cogsq", "xsgaq", "xrdq", "dpq", "oibdpq", "oiadpq",
    "nopiq", "xintq", "piq", "txtq", "niq", "epsfxq", "epspxq",
    "cheq", "rectq", "invtq", "actq", "ppentq", "gdwlq", "intanq", "atq",
    "apq", "lctq", "dlcq", "dlttq", "ltq", "seqq",
    "oancfy", "capxy", "ivncfy", "fincfy",
]

# Friendly label -> Compustat column name, used to build the derived
# financial-statement columns and to rename output columns for clarity.
ANNUAL_LABEL_MAP = {
    "sale": "revenue",
    "cogs": "cogs",
    "xsga": "sga",
    "xrd": "rnd",
    "dp": "d_and_a",
    "oibdp": "ebitda",
    "oiadp": "ebit_operating_income",
    "nopi": "non_operating_income",
    "xint": "interest_expense",
    "pi": "pretax_income",
    "txt": "income_tax",
    "ni": "net_income",
    "epsfx": "eps_diluted",
    "epspx": "eps_basic",
    "che": "cash_and_equivalents",
    "rect": "accounts_receivable",
    "invt": "inventory",
    "act": "current_assets",
    "ppent": "ppe_net",
    "gdwl": "goodwill",
    "intan": "intangible_assets",
    "at": "total_assets",
    "ap": "accounts_payable",
    "lct": "current_liabilities",
    "dlc": "short_term_debt",
    "dltt": "long_term_debt",
    "lt": "total_liabilities",
    "seq": "shareholders_equity",
    "oancf": "operating_cash_flow",
    "capx": "capital_expenditures",
    "ivncf": "investing_cash_flow",
    "fincf": "financing_cash_flow",
}

QUARTERLY_LABEL_MAP = {
    "saleq": "revenue",
    "cogsq": "cogs",
    "xsgaq": "sga",
    "xrdq": "rnd",
    "dpq": "d_and_a",
    "oibdpq": "ebitda",
    "oiadpq": "ebit_operating_income",
    "nopiq": "non_operating_income",
    "xintq": "interest_expense",
    "piq": "pretax_income",
    "txtq": "income_tax",
    "niq": "net_income",
    "epsfxq": "eps_diluted",
    "epspxq": "eps_basic",
    "cheq": "cash_and_equivalents",
    "rectq": "accounts_receivable",
    "invtq": "inventory",
    "actq": "current_assets",
    "ppentq": "ppe_net",
    "gdwlq": "goodwill",
    "intanq": "intangible_assets",
    "atq": "total_assets",
    "apq": "accounts_payable",
    "lctq": "current_liabilities",
    "dlcq": "short_term_debt",
    "dlttq": "long_term_debt",
    "ltq": "total_liabilities",
    "seqq": "shareholders_equity",
    "oancfy": "operating_cash_flow_ytd",
    "capxy": "capital_expenditures_ytd",
    "ivncfy": "investing_cash_flow_ytd",
    "fincfy": "financing_cash_flow_ytd",
}


def log(msg):
    print(f"[pull_thrm_wrds] {msg}", flush=True)


def connect_to_wrds():
    """Open a SQLAlchemy/psycopg2 connection to the WRDS Postgres server."""
    if WRDS_USERNAME == "YOUR_USERNAME_HERE" or WRDS_PASSWORD == "YOUR_PASSWORD_HERE":
        log("ERROR: Please set WRDS_USERNAME and WRDS_PASSWORD near the top "
            "of this script before running it.")
        sys.exit(1)

    log(f"Connecting to WRDS ({WRDS_HOST}:{WRDS_PORT}/{WRDS_DBNAME}) as "
        f"'{WRDS_USERNAME}'...")
    try:
        url = URL.create(
            drivername="postgresql+psycopg2",
            username=WRDS_USERNAME,
            password=WRDS_PASSWORD,
            host=WRDS_HOST,
            port=WRDS_PORT,
            database=WRDS_DBNAME,
        )
        engine = create_engine(url, connect_args={"sslmode": "require"})
        # Cheap round-trip to confirm the connection actually works.
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        log("Connected to WRDS successfully.")
        return engine
    except Exception as exc:
        log(f"ERROR: Could not connect to WRDS: {exc}")
        log("Check your WRDS_USERNAME/WRDS_PASSWORD, network access, and "
            "that your WRDS account has Compustat (North America) access.")
        sys.exit(1)


def find_gvkey(engine, ticker):
    """Resolve the Compustat GVKEY for the given ticker via comp.security."""
    log(f"Looking up GVKEY for ticker '{ticker}'...")
    query = """
        SELECT DISTINCT gvkey, tic, iid
        FROM comp.security
        WHERE tic = %(ticker)s
    """
    try:
        df = pd.read_sql(query, engine, params={"ticker": ticker})
    except Exception as exc:
        log(f"ERROR: GVKEY lookup query failed: {exc}")
        sys.exit(1)

    if df.empty:
        log(f"ERROR: No GVKEY found for ticker '{ticker}' in comp.security.")
        sys.exit(1)

    gvkey = df["gvkey"].iloc[0]
    log(f"Found GVKEY '{gvkey}' for ticker '{ticker}'.")
    return gvkey


def pull_annual_data(engine, gvkey, start_fy, end_fy):
    """Pull annual Compustat fundamentals (comp.funda) for the given GVKEY."""
    log(f"Pulling annual Compustat data (FY{start_fy}-FY{end_fy})...")
    cols = ", ".join(ANNUAL_FIELDS)
    query = f"""
        SELECT {cols}
        FROM comp.funda
        WHERE gvkey = %(gvkey)s
          AND indfmt = 'INDL'
          AND datafmt = 'STD'
          AND popsrc = 'D'
          AND consol = 'C'
          AND fyear BETWEEN %(start_fy)s AND %(end_fy)s
        ORDER BY fyear
    """
    try:
        df = pd.read_sql(
            query, engine,
            params={"gvkey": gvkey, "start_fy": start_fy, "end_fy": end_fy},
        )
    except Exception as exc:
        log(f"ERROR: Annual data query failed: {exc}")
        sys.exit(1)

    if df.empty:
        log("WARNING: Annual query returned no rows.")
    else:
        log(f"Retrieved {len(df)} annual rows "
            f"(FY{df['fyear'].min()}-FY{df['fyear'].max()}).")
    return df


def pull_quarterly_data(engine, gvkey):
    """Pull Q1 2025 and Q1 2026 Compustat fundamentals (comp.fundq)."""
    log("Pulling quarterly Compustat data (Q1 2025 and Q1 2026)...")
    cols = ", ".join(QUARTERLY_FIELDS)
    query = f"""
        SELECT {cols}
        FROM comp.fundq
        WHERE gvkey = %(gvkey)s
          AND indfmt = 'INDL'
          AND datafmt = 'STD'
          AND popsrc = 'D'
          AND consol = 'C'
          AND fqtr = 1
          AND fyearq IN (2025, 2026)
        ORDER BY fyearq
    """
    try:
        df = pd.read_sql(query, engine, params={"gvkey": gvkey})
    except Exception as exc:
        log(f"ERROR: Quarterly data query failed: {exc}")
        sys.exit(1)

    if df.empty:
        log("WARNING: Quarterly query returned no rows.")
    else:
        years_found = sorted(df["fyearq"].unique().tolist())
        log(f"Retrieved {len(df)} quarterly rows for FYQ1: {years_found}.")
        missing = {2025, 2026} - set(years_found)
        if missing:
            log(f"WARNING: No Q1 data found for fiscal year(s): "
                f"{sorted(missing)}. Q1 2026 may not yet be filed/available "
                f"on WRDS.")
    return df


def add_derived_fields(df, is_quarterly):
    """Add Gross Profit and a sanity-checked EBITDA column, handling NaNs."""
    sale_col = "saleq" if is_quarterly else "sale"
    cogs_col = "cogsq" if is_quarterly else "cogs"
    oibdp_col = "oibdpq" if is_quarterly else "oibdp"

    if sale_col in df.columns and cogs_col in df.columns:
        df["gross_profit"] = df[sale_col] - df[cogs_col]
    else:
        df["gross_profit"] = pd.NA

    # EBITDA: Compustat's OIBDP(Q) is the standard EBITDA proxy; keep it
    # as the primary figure and don't overwrite with a recomputation that
    # could introduce additional missing values.
    if oibdp_col in df.columns:
        df["ebitda"] = df[oibdp_col]
    else:
        df["ebitda"] = pd.NA

    return df


def finalize_output(df, label_map, is_quarterly):
    """Rename raw Compustat columns to friendly financial-statement labels."""
    if df.empty:
        return df

    df = add_derived_fields(df, is_quarterly)

    rename_map = {src: dst for src, dst in label_map.items() if src in df.columns}
    df = df.rename(columns=rename_map)

    # Reorder: identifiers first, then financial-statement fields.
    id_cols = [c for c in [
        "gvkey", "tic", "conm", "cusip", "datadate",
        "fyear", "fyearq", "fqtr",
    ] if c in df.columns]
    other_cols = [c for c in df.columns if c not in id_cols]
    df = df[id_cols + other_cols]

    return df


def report_missing_summary(df, label):
    """Print a short summary of how many values are missing per column."""
    if df.empty:
        log(f"{label}: no data to summarize.")
        return
    missing_counts = df.isna().sum()
    missing_cols = missing_counts[missing_counts > 0]
    if missing_cols.empty:
        log(f"{label}: no missing values across retrieved fields.")
    else:
        log(f"{label}: missing values detected in "
            f"{len(missing_cols)} column(s):")
        for col, cnt in missing_cols.items():
            log(f"    - {col}: {cnt} missing of {len(df)} rows")


def main():
    log(f"Starting Compustat/WRDS pull for {TICKER} (Gentherm Inc.)")

    engine = connect_to_wrds()

    try:
        gvkey = find_gvkey(engine, TICKER)

        annual_df = pull_annual_data(engine, gvkey, ANNUAL_START_FY, ANNUAL_END_FY)
        annual_df = finalize_output(annual_df, ANNUAL_LABEL_MAP, is_quarterly=False)
        report_missing_summary(annual_df, "Annual data")

        quarterly_df = pull_quarterly_data(engine, gvkey)
        quarterly_df = finalize_output(quarterly_df, QUARTERLY_LABEL_MAP, is_quarterly=True)
        report_missing_summary(quarterly_df, "Quarterly data")

    except Exception as exc:
        log(f"ERROR: Unexpected failure while querying WRDS: {exc}")
        sys.exit(1)
    finally:
        engine.dispose()
        log("WRDS connection closed.")

    try:
        annual_df.to_csv(ANNUAL_OUTPUT_FILE, index=False)
        log(f"Saved annual data to '{ANNUAL_OUTPUT_FILE}' ({len(annual_df)} rows).")
    except Exception as exc:
        log(f"ERROR: Failed to write '{ANNUAL_OUTPUT_FILE}': {exc}")
        sys.exit(1)

    try:
        quarterly_df.to_csv(QUARTERLY_OUTPUT_FILE, index=False)
        log(f"Saved quarterly data to '{QUARTERLY_OUTPUT_FILE}' "
            f"({len(quarterly_df)} rows).")
    except Exception as exc:
        log(f"ERROR: Failed to write '{QUARTERLY_OUTPUT_FILE}': {exc}")
        sys.exit(1)

    log("Done.")


if __name__ == "__main__":
    main()
