# Aviation Revenue Integrity Auditor

A Python-based tool designed to detect yield divergence and revenue leakage in the Australian domestic aviation market using BITRE (Bureau of Infrastructure, Transport and Regional Economics) air fare index data.

---

## Objective

The Aviation Revenue Integrity Auditor identifies anomalies where the **Real Restricted Economy** fare index drops sharply while the **Real Business Class** index remains stable. Such divergence can indicate revenue leakage, structural market shifts, or methodological changes—all of which are critical for revenue management and audit purposes.

---

## Methodology

The tool applies **Month-on-Month (MoM) % change** analysis to three BITRE fare indices:

- **Real Business Class** — Full-fare business travel
- **Real Restricted Economy** — Economy fares with advance-purchase restrictions
- **Real Best Discount** — Lowest available discount fares

**MoM % Change** = `(Current Month − Previous Month) / Previous Month × 100`

REVENUE_LEAKAGE anomalies are flagged when:

| Condition | Threshold |
|-----------|-----------|
| Real Restricted Economy | MoM drop > 10% |
| Real Business Class | MoM change between -3% and +3% (stable) |

Months meeting both criteria are labeled **REVENUE_LEAKAGE**. The June 2011 event is further designated a **High Priority Anomaly** due to its magnitude and market significance.

---

## Validation

Findings were cross-referenced and validated against official BITRE methodology notes, including the June 2011 fare structure shift and 2020 COVID-19 impacts. The tool incorporates a historical context dictionary for key milestones such as Virgin/Jetstar Flexi fare changes, Jetstar refund rule redefinitions, and methodology updates.

---

## Visualization

The output chart uses a **dual-layer subplot** layout with linked X-axes (zoom on one pane syncs the other):

- **Top pane — Corporate Yield Audit**: Business Class vs Restricted Economy, with REVENUE_LEAKAGE markers highlighted in red where divergence is detected
- **Bottom pane — Market Competition Audit**: Restricted Economy vs Best Discount, tracking the low-cost segment

Both panes display official notes on hover for key dates (e.g., June 2011 Flexi fare shift, COVID-19 impacts). The chart tracks the evolution of the Australian domestic market from 1992 onward, including the post-2001 restructuring after the Ansett collapse and the subsequent entry of low-cost carriers (Jetstar, Tiger, Virgin Blue), which reshaped fare structures across all segments.

---

## Features

- **Data cleaning**: Handles BITRE Excel/CSV formats, parses dates, and manages header variations
- **Audit logic**: MoM % change calculation with configurable thresholds
- **Anomaly detection**: Automated REVENUE_LEAKAGE and High Priority Anomaly flags
- **Visualization**: Interactive dual-layer subplot chart (`trend_analysis.html`) with linked axes, leakage markers, and hover notes
- **Reporting**: Professional terminal report with [AUDIT SCOPE], [KEY FINDINGS], and [HISTORICAL CONTEXT]

---

## Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd BITRE_Airline_Audit
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Download the latest Domestic Air Fares time series (XLSX) from the [official BITRE Aviation Statistics page](https://www.bitre.gov.au/statistics/aviation/air_fares). Save it in the project directory as `air_fares.xlsx` (the script will automatically detect files starting with `air_fares`).

4. Run the audit:
   ```bash
   python audit_engine.py
   ```

5. Open `trend_analysis.html` in a browser to view the interactive chart.

---

## Output

- **Terminal**: Audit report with scope, key findings, and historical context
- **Chart**: `trend_analysis.html` — interactive dual-layer subplot (Corporate Yield Audit / Market Competition Audit) with Business, Restricted Economy, and Best Discount indices; leakage markers; and hover notes for key events

---

## Dependencies

- **pandas** — Data loading and analysis
- **plotly** — Interactive visualizations
- **openpyxl** — Excel file support
