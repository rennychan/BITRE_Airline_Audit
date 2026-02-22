"""
BITRE Airline Fare Audit Engine
Data cleaning, anomaly detection, and trend visualization for domestic air fare indices.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- Configuration ---
DATA_DIR = Path(__file__).parent
OUTPUT_HTML = DATA_DIR / "trend_analysis.html"


def _find_air_fares_file() -> Path:
    """Find the most recent air_fares*.csv or air_fares*.xlsx in the project folder."""
    patterns = list(DATA_DIR.glob("air_fares*.csv")) + list(DATA_DIR.glob("air_fares*.xlsx"))
    if not patterns:
        raise FileNotFoundError(
            f"No air_fares*.csv or air_fares*.xlsx found in {DATA_DIR}"
        )
    return max(patterns, key=lambda p: p.stat().st_mtime)
COL_BUSINESS = "Real Business Class"
COL_ECONOMY = "Real Restricted Economy"
COL_BEST_DISCOUNT = "Real Best Discount"
COL_MONTH = "Month"

# Historical context for selected months (official notes)
HISTORICAL_CONTEXT = {
    "2011-06": "Structural Change: Virgin & Jetstar introduced simplified, lower-cost Flexi fare structures; Qantas followed with competitive price cuts.",
    "2012-01": "Market Shift: Virgin Australia expanded Business Class; Full Economy index rose as Premium Economy was removed.",
    "2015-03": "Methodology Change: Qantas discontinued Full Economy fares; index tracking for this category ceased.",
    "2017-11": "Product Redefinition: Jetstar changed refund rules to vouchers, removing its product from the BITRE Restricted Economy definition.",
    "2020-04": "COVID-19 Impact: Massive reduction in services; indices based on limited available routes.",
}


def add_historical_notes(df: pd.DataFrame) -> pd.DataFrame:
    """Add official_note column from HISTORICAL_CONTEXT based on Month."""
    df = df.copy()
    df["month_key"] = df[COL_MONTH].dt.strftime("%Y-%m")
    df["official_note"] = df["month_key"].map(HISTORICAL_CONTEXT)
    df = df.drop(columns=["month_key"])
    return df


def load_data() -> pd.DataFrame:
    """Load air fares data from the most recent air_fares*.csv or air_fares*.xlsx in the folder."""
    data_path = _find_air_fares_file()

    if data_path.suffix.lower() == ".csv":
        # CSV: BITRE often has title row 0, headers in row 1
        for header_row in range(5):
            try:
                df = pd.read_csv(data_path, encoding="utf-8", header=header_row)
                df.columns = df.columns.str.strip()
                unnamed_count = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
                if unnamed_count >= len(df.columns) - 1 and header_row < 4:
                    continue
                month_col = next(
                    (c for c in df.columns if "month" in str(c).lower() or "survey" in str(c).lower()),
                    df.columns[0],
                )
                df = df.rename(columns={month_col: COL_MONTH})
                if COL_MONTH not in df.columns:
                    df = df.rename(columns={df.columns[0]: COL_MONTH})
                break
            except Exception:
                continue
    else:
        # XLSX: row 0=title, row 1=disclaimer, row 2=headers
        df = pd.read_excel(data_path, header=2)
        df.columns = df.columns.str.strip()
        first_col = df.columns[0]
        if "month" in str(first_col).lower() or "survey" in str(first_col).lower():
            df = df.rename(columns={first_col: COL_MONTH})
        else:
            df = df.rename(columns={df.columns[0]: COL_MONTH})

    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Parse Month to datetime and coerce numeric columns."""
    df = df.copy()

    # Parse Month column to datetime
    df[COL_MONTH] = pd.to_datetime(df[COL_MONTH], errors="coerce")

    # Drop rows with invalid Month
    df = df.dropna(subset=[COL_MONTH]).reset_index(drop=True)

    # Get column names (allow slight variations)
    business_col = COL_BUSINESS if COL_BUSINESS in df.columns else None
    economy_col = COL_ECONOMY if COL_ECONOMY in df.columns else None
    discount_col = COL_BEST_DISCOUNT if COL_BEST_DISCOUNT in df.columns else None
    if business_col is None:
        business_col = next(
            (c for c in df.columns if "business" in str(c).lower() and "restricted" not in str(c).lower()),
            None,
        )
    if economy_col is None:
        economy_col = next(
            (c for c in df.columns if "restricted" in str(c).lower() and "economy" in str(c).lower()),
            None,
        )
    if discount_col is None:
        discount_col = next(
            (c for c in df.columns if "best" in str(c).lower() and "discount" in str(c).lower()),
            None,
        )
    # Fallback: BITRE column order is Month, Business(1), ..., Restricted Economy(5), Best Discount(7)
    if business_col is None and len(df.columns) >= 2:
        business_col = df.columns[1]
    if economy_col is None and len(df.columns) >= 6:
        economy_col = df.columns[5]
    if discount_col is None and len(df.columns) >= 8:
        discount_col = df.columns[7]
    if business_col is None or economy_col is None:
        raise ValueError(
            f"Required columns not found. Available: {df.columns.tolist()}"
        )

    # Coerce to numeric, treating n.a., -, etc. as NaN (Best Discount may have NaNs)
    for col in [business_col, economy_col]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if discount_col:
        df[discount_col] = pd.to_numeric(df[discount_col], errors="coerce")

    # Use standardized names
    df = df.rename(columns={business_col: COL_BUSINESS, economy_col: COL_ECONOMY})
    if discount_col:
        df = df.rename(columns={discount_col: COL_BEST_DISCOUNT})

    # Keep columns; require Business & Economy for leakage logic; Best Discount optional (NaN handled in chart)
    keep_cols = [COL_MONTH, COL_BUSINESS, COL_ECONOMY]
    if COL_BEST_DISCOUNT in df.columns:
        keep_cols.append(COL_BEST_DISCOUNT)
    df = df[keep_cols].dropna(how="all", subset=[COL_BUSINESS, COL_ECONOMY])

    return df.sort_values(COL_MONTH).reset_index(drop=True)


def compute_mom_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate Month-on-Month % change for Business and Economy indices."""
    df = df.copy()
    df["Business_MoM_pct"] = df[COL_BUSINESS].pct_change() * 100
    df["Economy_MoM_pct"] = df[COL_ECONOMY].pct_change() * 100
    return df


def detect_revenue_leakage(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flag months where:
    - Real Restricted Economy dropped by >10%
    - Real Business Class remained stable (between -3% and +3%)
    """
    df = df.copy()
    economy_drop = df["Economy_MoM_pct"] < -10
    business_stable = (df["Business_MoM_pct"] >= -3) & (df["Business_MoM_pct"] <= 3)
    df["REVENUE_LEAKAGE"] = economy_drop & business_stable
    return df


def build_chart(df: pd.DataFrame, output_path: Path) -> None:
    """Create vertical subplot chart: Corporate Yield (top) and Market Competition (bottom)."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Corporate Yield Audit", "Market Competition Audit"),
    )

    # Build hover note for customdata (official notes)
    hover_note = df["official_note"].apply(
        lambda x: f"<br><br>Note: {x}" if pd.notna(x) else ""
    ).tolist()

    # --- Top chart: Business Class + Restricted Economy + Leakage ---
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
    fig.add_trace(
        go.Scatter(
            x=df[COL_MONTH],
            y=df[COL_ECONOMY],
            name=COL_ECONOMY,
            line=dict(color="Coral", width=2),
            mode="lines",
            connectgaps=False,
            customdata=hover_note,
            hovertemplate="Economy: %{y:.2f}%{customdata}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    # Leakage markers (top chart only)
    leakage = df[df["REVENUE_LEAKAGE"]]
    if len(leakage) > 0:
        leakage_notes = leakage["official_note"].apply(
            lambda x: f"<br><br>Note: {x}" if pd.notna(x) else ""
        ).tolist()
        fig.add_trace(
            go.Scatter(
                x=leakage[COL_MONTH],
                y=leakage[COL_ECONOMY],
                name="REVENUE_LEAKAGE",
                mode="markers",
                marker=dict(size=12, color="red", symbol="x", line=dict(width=2)),
                customdata=leakage_notes,
                hovertemplate="REVENUE_LEAKAGE | Economy: %{y:.2f}%{customdata}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    # --- Bottom chart: Restricted Economy + Best Discount ---
    fig.add_trace(
        go.Scatter(
            x=df[COL_MONTH],
            y=df[COL_ECONOMY],
            name=COL_ECONOMY,
            line=dict(color="Coral", width=2),
            mode="lines",
            connectgaps=False,
            customdata=hover_note,
            hovertemplate="Economy: %{y:.2f}%{customdata}<extra></extra>",
            showlegend=False,
        ),
        row=2,
        col=1,
    )
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
        title="Australian Domestic Aviation: Fare Index Trend & Revenue Leakage Audit (1992-2026)",
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
    fig.write_html(str(output_path))
    print(f"Chart saved to: {output_path}")


def print_summary(df: pd.DataFrame) -> None:
    """Print audit findings to the terminal as a professional audit report."""
    leakage = df[df["REVENUE_LEAKAGE"]]
    date_min = df[COL_MONTH].min().strftime("%Y-%m")
    date_max = df[COL_MONTH].max().strftime("%Y-%m")

    print("\n")
    print("=" * 70)
    print("         BITRE AIR FARE AUDIT REPORT")
    print("    Aviation Revenue Integrity Assessment")
    print("=" * 70)
    print()

    print("[AUDIT SCOPE]")
    print("-" * 70)
    print(f"  Period covered:          {date_min} to {date_max}")
    print(f"  Observations analysed:   {len(df)} months")
    indices = f"{COL_BUSINESS}, {COL_ECONOMY}"
    if COL_BEST_DISCOUNT in df.columns:
        indices += f", {COL_BEST_DISCOUNT}"
    print(f"  Indices reviewed:        {indices}")
    print("  Methodology:             Month-on-Month % change analysis with")
    print("                           REVENUE_LEAKAGE flag (Economy drop >10%,")
    print("                           Business stable -3% to +3%)")
    print("-" * 70)
    print()

    print("[KEY FINDINGS]")
    print("-" * 70)
    print(f"  REVENUE_LEAKAGE events:  {int(leakage['REVENUE_LEAKAGE'].sum())}")
    print("-" * 70)

    if len(leakage) > 0:
        for _, row in leakage.iterrows():
            month_str = row[COL_MONTH].strftime("%Y-%m")
            label = "High Priority Anomaly" if month_str == "2011-06" else "REVENUE_LEAKAGE"
            print(f"  {month_str}  |  {label}")
            print(f"       Economy MoM: {row['Economy_MoM_pct']:+.2f}%  |  Business MoM: {row['Business_MoM_pct']:+.2f}%")
            if pd.notna(row.get("official_note")):
                print(f"       Note: {row['official_note']}")
            print()
    else:
        print("  No REVENUE_LEAKAGE events detected in the audit period.")
    print("-" * 70)
    print()

    # Historical context section (months with official notes)
    notes_df = df[df["official_note"].notna()].sort_values(COL_MONTH)
    if len(notes_df) > 0:
        print("[HISTORICAL CONTEXT]")
        print("-" * 70)
        for _, row in notes_df.iterrows():
            print(f"  {row[COL_MONTH].strftime('%Y-%m')}:")
            print(f"       {row['official_note']}")
            print()
        print("-" * 70)
        print()


def main() -> None:
    df_raw = load_data()
    df = clean_data(df_raw)
    df = compute_mom_changes(df)
    df = detect_revenue_leakage(df)
    df = add_historical_notes(df)

    build_chart(df, OUTPUT_HTML)
    print_summary(df)


if __name__ == "__main__":
    main()
