"""One-time script: read air_fares.xlsx and save as air_fares_new.csv"""
import pandas as pd
from pathlib import Path

path = Path(__file__).parent
xlsx = path / "air_fares.xlsx"
if not xlsx.exists():
    xlsx = path / "air_fares_0226.xlsx"
df = pd.read_excel(xlsx)
df.to_csv(path / "air_fares_new.csv", index=False, encoding="utf-8")
print(f"Saved {len(df)} rows to air_fares_new.csv")
