"""
BITRE Airline Fare Audit Engine
Data cleaning, anomaly detection, and trend visualization for domestic air fare indices.
"""

import logging
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent
OUTPUT_HTML = DATA_DIR / "trend_analysis.html"

# Column name constants
COL_BUSINESS = "Real Business Class"
COL_ECONOMY = "Real Restricted Economy"
COL_BEST_DISCOUNT = "Real Best Discount"
COL_MONTH = "Month"

# --- Revenue Leakage thresholds (parameterised for testability) ---
LEAKAGE_ECONOMY_DROP_THRESHOLD: float = -10.0   # Economy MoM % below this triggers flag
LEAKAGE_BUSINESS_STABLE_LOWER: float = -3.0     # Business MoM % lower bound for "stable"
LEAKAGE_BUSINESS_STABLE_UPPER: float = 3.0      # Business MoM % upper bound for "stable"

# Historical context for selected months (official notes)
HISTORICAL_CONTEXT: dict[str, str] = {
    "2011-06": (
        "Structural Change: Virgin & Jetstar introduced simplified, lower-cost Flexi fare "
        "structures; Qantas followed with competitive price cuts."
    ),
    "2012-01": (
        "Market Shift: Virgin Australia expanded Business Class; Full Economy index rose as "
        "Premium Economy was removed."
    ),
    "2015-03": (
        "Methodology Change: Qantas discontinued Full Economy fares; index tracking for this "
        "category ceased."
    ),
    "2017-11": (
        "Product Redefinition: Jetstar changed refund rules to vouchers, removing its product "
        "from the BITRE Restricted Economy definition."
    ),
    "2020-04": (
        "COVID-19 Impact: Massive reduction in services; indices based on limited available routes."
    ),
}

# Months that warrant elevated severity in audit output (driven by data, not hard-coded in renderer)
HIGH_PRIORITY_MONTHS: frozenset[str] = frozenset({"2011-06"})


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _find_air_fares_file() -> Path:
    """
    Find the air_fare(s)*.csv/xlsx file in DATA_DIR.

    Preference order:
      1. Files whose names contain an ISO date or version number (sorted descending).
      2. If no versioned name is found, fall back to the most recently *modified* file
         and emit a warning so the caller is aware of the ambiguity.

    Raises
    ------
    FileNotFoundError
        If no matching file exists in DATA_DIR.
    """
    patterns = (
        list(DATA_DIR.glob("air_fare*.csv"))
        + list(DATA_DIR.glob("air_fare*.xlsx"))
        + list(DATA_DIR.glob("air_fares*.csv"))
        + list(DATA_DIR.glob("air_fares*.xlsx"))
    )
    if not patterns:
        raise FileNotFoundError(
            f"No air_fare*.csv/xlsx or air_fares*.csv/xlsx found in {DATA_DIR}"
        )

    # Prefer lexicographic sort (captures YYYY-MM-DD or vX.Y in filenames)
    versioned = sorted(patterns, key=lambda p: p.stem, reverse=True)
    chosen = versioned[0]

    if len(patterns) > 1:
        warnings.warn(
            f"Multiple air-fare files found; selected '{chosen.name}' by filename sort. "
            f"All candidates: {[p.name for p in versioned]}. "
            "Rename files with ISO dates (e.g. air_fares_2024-06.csv) for unambiguous selection.",
            UserWarning,
            stacklevel=2,
        )
        logger.warning("Selected data file: %s (from %d candidates)", chosen.name, len(patterns))
    else:
        logger.info("Data file: %s", chosen.name)

    return chosen


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_raw(data_path: Path) -> pd.DataFrame:
    """
    Load raw data from a CSV or XLSX file.

    For CSV the function tries header rows 0–4 until it finds one that yields a
    recognisable Month column.  Only ``pd.errors.ParserError`` and
    ``UnicodeDecodeError`` are caught; all other exceptions propagate so that
    genuine I/O problems are not silently swallowed.

    Parameters
    ----------
    data_path:
        Path to the source file.

    Returns
    -------
    pd.DataFrame
        Raw (uncleaned) dataframe with ``COL_MONTH`` as first column name.
    """
    if data_path.suffix.lower() == ".csv":
        df = _load_csv(data_path)
    else:
        df = _load_xlsx(data_path)

    return df


def _load_csv(data_path: Path) -> pd.DataFrame:
    """Try header rows 0-4 to locate the Month column in a CSV."""
    last_exc: Optional[Exception] = None

    for header_row in range(5):
        try:
            df = pd.read_csv(data_path, encoding="utf-8", header=header_row)
            df.columns = df.columns.str.strip()

            unnamed_count = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
            if unnamed_count >= len(df.columns) - 1 and header_row < 4:
                logger.debug("Skipping header_row=%d (too many unnamed columns)", header_row)
                continue

            month_col = next(
                (c for c in df.columns if "month" in str(c).lower() or "survey" in str(c).lower()),
                df.columns[0],
            )
            df = df.rename(columns={month_col: COL_MONTH})
            logger.info("CSV loaded with header_row=%d; Month column='%s'", header_row, month_col)
            return df

        except (pd.errors.ParserError, UnicodeDecodeError) as exc:
            # These are recoverable parse issues — try the next header row
            logger.debug("header_row=%d failed (%s): %s", header_row, type(exc).__name__, exc)
            last_exc = exc
            continue

    raise ValueError(
        f"Could not parse '{data_path.name}' with any header row 0–4."
    ) from last_exc


def _load_xlsx(data_path: Path) -> pd.DataFrame:
    """Load a BITRE XLSX file (title row 0, disclaimer row 1, headers row 2)."""
    df = pd.read_excel(data_path, header=2)
    df.columns = df.columns.str.strip()
    first_col = df.columns[0]
    if "month" in str(first_col).lower() or "survey" in str(first_col).lower():
        df = df.rename(columns={first_col: COL_MONTH})
    else:
        df = df.rename(columns={first_col: COL_MONTH})
    logger.info("XLSX loaded; first column renamed to '%s'", COL_MONTH)
    return df


def load_and_clean_data() -> pd.DataFrame:
    """
    Locate, load, and clean the BITRE air fare data file.

    Returns
    -------
    pd.DataFrame
        Cleaned dataframe sorted by Month.
    """
    data_path = _find_air_fares_file()
    df = _load_raw(data_path)
    return _clean_data(df)


# ---------------------------------------------------------------------------
# Data cleaning
# ---------------------------------------------------------------------------

def _resolve_column(
    df: pd.DataFrame,
    standard_name: str,
    *fallback_predicates,
    positional_index: Optional[int] = None,
) -> Optional[str]:
    """
    Resolve a column name with explicit fallback hierarchy and warnings.

    1. Exact match against ``standard_name``.
    2. Each predicate in ``fallback_predicates`` (checked in order).
    3. Positional index (last resort — emits a UserWarning).

    Returns the matched column name, or ``None`` if nothing matched.
    """
    if standard_name in df.columns:
        return standard_name

    for predicate in fallback_predicates:
        match = next((c for c in df.columns if predicate(str(c).lower())), None)
        if match:
            warnings.warn(
                f"Column '{standard_name}' not found; using '{match}' (matched by keyword). "
                "Verify this is the correct column.",
                UserWarning,
                stacklevel=3,
            )
            logger.warning("Column fallback: '%s' -> '%s' (keyword match)", standard_name, match)
            return match

    if positional_index is not None and len(df.columns) > positional_index:
        col = df.columns[positional_index]
        warnings.warn(
            f"Column '{standard_name}' not found by name or keyword; "
            f"falling back to positional column [{positional_index}] = '{col}'. "
            "This is a last resort — check that the file format matches expectations.",
            UserWarning,
            stacklevel=3,
        )
        logger.warning(
            "Column fallback: '%s' -> '%s' (positional index %d)",
            standard_name,
            col,
            positional_index,
        )
        return col

    return None


def _clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parse the Month column and coerce numeric fare columns.

    Raises
    ------
    ValueError
        If required columns (Business Class, Restricted Economy) cannot be resolved.
    """
    df = df.copy()

    # Parse Month column to datetime
    df[COL_MONTH] = pd.to_datetime(df[COL_MONTH], errors="coerce")
    invalid_months = df[COL_MONTH].isna().sum()
    if invalid_months:
        logger.warning("Dropped %d rows with unparseable Month values.", invalid_months)
    df = df.dropna(subset=[COL_MONTH]).reset_index(drop=True)

    # Resolve column names with explicit fallback + warnings
    business_col = _resolve_column(
        df,
        COL_BUSINESS,
        lambda c: "business" in c and "restricted" not in c,
        positional_index=1,
    )
    economy_col = _resolve_column(
        df,
        COL_ECONOMY,
        lambda c: "restricted" in c and "economy" in c,
        positional_index=5,
    )
    discount_col = _resolve_column(
        df,
        COL_BEST_DISCOUNT,
        lambda c: "best" in c and "discount" in c,
        positional_index=7,
    )

    if business_col is None or economy_col is None:
        raise ValueError(
            f"Required columns could not be resolved. Available columns: {df.columns.tolist()}"
        )

    # Coerce to numeric (n.a., -, etc. become NaN)
    for col in filter(None, [business_col, economy_col, discount_col]):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Rename to standard names
    rename_map = {business_col: COL_BUSINESS, economy_col: COL_ECONOMY}
    if discount_col and discount_col != COL_BEST_DISCOUNT:
        rename_map[discount_col] = COL_BEST_DISCOUNT
    df = df.rename(columns=rename_map)

    # Keep only the columns we need
    keep_cols = [COL_MONTH, COL_BUSINESS, COL_ECONOMY]
    if COL_BEST_DISCOUNT in df.columns:
        keep_cols.append(COL_BEST_DISCOUNT)
    df = df[keep_cols].dropna(how="all", subset=[COL_BUSINESS, COL_ECONOMY])

    df = df.sort_values(COL_MONTH).reset_index(drop=True)

    # Warn if there are gaps in the monthly series (affects MoM calc accuracy)
    _warn_on_month_gaps(df)

    return df


def _warn_on_month_gaps(df: pd.DataFrame) -> None:
    """Emit a warning if the Month series is not strictly consecutive (monthly)."""
    if len(df) < 2:
        return
    months = pd.DatetimeIndex(df[COL_MONTH])
    # Expected: each step is exactly one calendar month
    diffs = pd.Series(months[1:]) - pd.Series(months[:-1].values)
    # Approx: flag gaps larger than ~35 days
    gaps = diffs[diffs > pd.Timedelta(days=35)]
    if not gaps.empty:
        gap_starts = df.loc[gaps.index, COL_MONTH].dt.strftime("%Y-%m").tolist()
        warnings.warn(
            f"Month-on-Month calculations may be misleading: {len(gaps)} gap(s) detected "
            f"in the monthly series after: {gap_starts}. "
            "Consider imputing or masking MoM values across gaps.",
            UserWarning,
            stacklevel=2,
        )
        logger.warning("Month gaps detected after: %s", gap_starts)


# ---------------------------------------------------------------------------
# Historical annotation
# ---------------------------------------------------------------------------

def add_historical_notes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach official_note from HISTORICAL_CONTEXT based on the Month column.

    This is a pure annotation step; it does not alter metric columns.
    """
    df = df.copy()
    month_keys = df[COL_MONTH].dt.strftime("%Y-%m")
    df["official_note"] = month_keys.map(HISTORICAL_CONTEXT)
    return df


# ---------------------------------------------------------------------------
# Audit metrics
# ---------------------------------------------------------------------------

def calculate_audit_metrics(
    df: pd.DataFrame,
    economy_drop_threshold: float = LEAKAGE_ECONOMY_DROP_THRESHOLD,
    business_stable_lower: float = LEAKAGE_BUSINESS_STABLE_LOWER,
    business_stable_upper: float = LEAKAGE_BUSINESS_STABLE_UPPER,
) -> pd.DataFrame:
    """
    Calculate month-on-month changes and flag revenue leakage events.

    Parameters
    ----------
    df:
        Cleaned dataframe from ``load_and_clean_data``.
    economy_drop_threshold:
        Economy MoM % below which a drop is considered significant (default -10%).
    business_stable_lower / business_stable_upper:
        Inclusive bounds defining a "stable" Business Class MoM % (default -3% to +3%).

    Returns
    -------
    pd.DataFrame
        Input dataframe augmented with MoM columns, REVENUE_LEAKAGE flag, severity,
        and official_note.
    """
    df = df.copy()
    df["Business_MoM_pct"] = df[COL_BUSINESS].pct_change() * 100
    df["Economy_MoM_pct"] = df[COL_ECONOMY].pct_change() * 100

    economy_drop = df["Economy_MoM_pct"] < economy_drop_threshold
    business_stable = df["Business_MoM_pct"].between(business_stable_lower, business_stable_upper)
    df["REVENUE_LEAKAGE"] = economy_drop & business_stable

    # Severity is data-driven, not hard-coded in the renderer
    month_keys = df[COL_MONTH].dt.strftime("%Y-%m")
    df["severity"] = df["REVENUE_LEAKAGE"].map({True: "REVENUE_LEAKAGE", False: ""})
    df.loc[df["REVENUE_LEAKAGE"] & month_keys.isin(HIGH_PRIORITY_MONTHS), "severity"] = (
        "High Priority Anomaly"
    )

    # Annotation is a separate concern — applied after metrics are calculated
    df = add_historical_notes(df)

    logger.info(
        "Audit metrics calculated. REVENUE_LEAKAGE events: %d",
        df["REVENUE_LEAKAGE"].sum(),
    )
    return df


# ---------------------------------------------------------------------------
# Visualisation helpers
# ---------------------------------------------------------------------------

def _hover_notes(series: pd.Series) -> list[str]:
    """Convert an official_note Series to hover suffix strings."""
    return series.apply(
        lambda x: f"<br><br>Note: {x}" if pd.notna(x) else ""
    ).tolist()


def _economy_trace(
    df: pd.DataFrame,
    row: int,
    show_legend: bool = True,
) -> go.Scatter:
    """Return a styled Restricted Economy scatter trace for the given subplot row."""
    return go.Scatter(
        x=df[COL_MONTH],
        y=df[COL_ECONOMY],
        name=COL_ECONOMY,
        line=dict(color="Coral", width=2),
        mode="lines",
        connectgaps=False,
        customdata=_hover_notes(df["official_note"]),
        hovertemplate="Economy: %{y:.2f}%{customdata}<extra></extra>",
        showlegend=show_legend,
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def generate_dashboard(
    df: pd.DataFrame,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Generate the Plotly dashboard and write to an HTML file.

    Parameters
    ----------
    df:
        Audited dataframe (output of ``calculate_audit_metrics``).
    output_path:
        Destination path for the HTML file.  Defaults to ``OUTPUT_HTML``
        (i.e. ``<script_dir>/trend_analysis.html``).

    Returns
    -------
    Path
        The path to the written HTML file.
    """
    resolved_output = output_path if output_path is not None else OUTPUT_HTML

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            "Premium Yield Protection & Buy-Down Audit",
            "Discount Fare Benchmarking & Market Dynamics",
        ),
    )

    hover_note = _hover_notes(df["official_note"])

    # --- Top chart: Business Class + Restricted Economy + Leakage markers ---
    fig.add_trace(
        go.Scatter(
            x=df[COL_MONTH],
            y=df[COL_BUSINESS],
            name=COL_BUSINESS,
            line=dict(color="RoyalBlue", width=2),
            mode="lines",
            connectgaps=False,
            customdata=hover_note,
            hovertemplate="Business: %{y:.2f}%{customdata}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(_economy_trace(df, row=1, show_legend=True), row=1, col=1)

    leakage = df[df["REVENUE_LEAKAGE"]]
    if not leakage.empty:
        fig.add_trace(
            go.Scatter(
                x=leakage[COL_MONTH],
                y=leakage[COL_ECONOMY],
                name="Revenue Integrity Alert",
                mode="markers",
                marker=dict(size=12, color="red", symbol="x", line=dict(width=2)),
                customdata=_hover_notes(leakage["official_note"]),
                hovertemplate=(
                    "Revenue Integrity Alert | Economy: %{y:.2f}%{customdata}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    # --- Bottom chart: Restricted Economy + Best Discount ---
    fig.add_trace(_economy_trace(df, row=2, show_legend=False), row=2, col=1)

    if COL_BEST_DISCOUNT in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df[COL_MONTH],
                y=df[COL_BEST_DISCOUNT],
                name=COL_BEST_DISCOUNT,
                line=dict(color="ForestGreen", width=2),
                mode="lines",
                connectgaps=False,
                customdata=hover_note,
                hovertemplate="Best Discount: %{y:.2f}%{customdata}<extra></extra>",
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        title="Australian Domestic Aviation Yield Integrity Dashboard",
        template="plotly_white",
        paper_bgcolor="white",
        plot_bgcolor="white",
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.15,
            xanchor="center",
            x=0.5,
            traceorder="normal",
        ),
    )
    fig.update_xaxes(title_text="Audit Timeline", row=2, col=1)
    fig.update_yaxes(title_text="Fare Index (Real)", row=1, col=1)
    fig.update_yaxes(title_text="Fare Index (Real)", row=2, col=1)

    fig.write_html(str(resolved_output))
    logger.info("Dashboard saved to: %s", resolved_output)
    return resolved_output


# ---------------------------------------------------------------------------
# Audit report (terminal)
# ---------------------------------------------------------------------------

def print_summary(df: pd.DataFrame) -> None:
    """
    Print audit findings to the terminal as a professional audit report.

    Severity labels are read from the ``severity`` column (set by
    ``calculate_audit_metrics``) rather than being hard-coded here.
    """
    leakage = df[df["REVENUE_LEAKAGE"]]
    date_min = df[COL_MONTH].min().strftime("%Y-%m")
    date_max = df[COL_MONTH].max().strftime("%Y-%m")

    sep = "=" * 70
    thin = "-" * 70

    print(f"\n{sep}")
    print("         BITRE AIR FARE AUDIT REPORT")
    print("    Aviation Revenue Integrity Assessment")
    print(f"{sep}\n")

    print("[AUDIT SCOPE]")
    print(thin)
    print(f"  Period covered:          {date_min} to {date_max}")
    print(f"  Observations analysed:   {len(df)} months")
    indices = f"{COL_BUSINESS}, {COL_ECONOMY}"
    if COL_BEST_DISCOUNT in df.columns:
        indices += f", {COL_BEST_DISCOUNT}"
    print(f"  Indices reviewed:        {indices}")
    print("  Methodology:             Month-on-Month % change analysis with")
    print(f"                           REVENUE_LEAKAGE flag (Economy drop >{abs(LEAKAGE_ECONOMY_DROP_THRESHOLD):.0f}%,")
    print(f"                           Business stable {LEAKAGE_BUSINESS_STABLE_LOWER:+.0f}% to {LEAKAGE_BUSINESS_STABLE_UPPER:+.0f}%)")
    print(f"{thin}\n")

    print("[KEY FINDINGS]")
    print(thin)
    print(f"  REVENUE_LEAKAGE events:  {leakage['REVENUE_LEAKAGE'].sum()}")
    print(thin)

    if not leakage.empty:
        for _, row in leakage.iterrows():
            month_str = row[COL_MONTH].strftime("%Y-%m")
            label = row["severity"]   # driven by data, not a string comparison here
            print(f"  {month_str}  |  {label}")
            print(
                f"       Economy MoM: {row['Economy_MoM_pct']:+.2f}%  |  "
                f"Business MoM: {row['Business_MoM_pct']:+.2f}%"
            )
            if pd.notna(row.get("official_note")):
                print(f"       Note: {row['official_note']}")
            print()
    else:
        print("  No REVENUE_LEAKAGE events detected in the audit period.")

    print(f"{thin}\n")

    # Historical context section
    notes_df = df[df["official_note"].notna()].sort_values(COL_MONTH)
    if not notes_df.empty:
        print("[HISTORICAL CONTEXT]")
        print(thin)
        for _, row in notes_df.iterrows():
            print(f"  {row[COL_MONTH].strftime('%Y-%m')}:")
            print(f"       {row['official_note']}")
            print()
        print(f"{thin}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    df = load_and_clean_data()
    df = calculate_audit_metrics(df)
    generate_dashboard(df)
    print_summary(df)
