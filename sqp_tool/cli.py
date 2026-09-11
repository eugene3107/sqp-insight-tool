"""CLI: `sqp analyse <files> --out report.xlsx`, `sqp dashboard`."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from .metrics import Thresholds
from .pipeline import analyse
from .report import write_report

app = typer.Typer(add_completion=False, help="Amazon SQP insight tool")


@app.command(name="analyse")
def analyse_cmd(
    files: list[Path] = typer.Argument(..., help="SQP exports (.xlsx / .csv)"),
    out: Path = typer.Option(Path("sqp_report.xlsx"), "--out", "-o"),
    brand: list[str] = typer.Option(None, "--brand", help="Own-brand token(s); inferred if omitted"),
    competitor: list[str] = typer.Option(None, "--competitor", help="Competitor token(s); inferred if omitted"),
    min_impr: int = typer.Option(20, help="Own impressions needed to trust own CTR"),
    min_clicks: int = typer.Option(10, help="Own clicks needed to trust own CVR"),
    price_band: float = typer.Option(0.15, help="±band treated as price parity"),
    top_n: int = typer.Option(25, help="Rows per entity in Actions / Visibility tabs"),
) -> None:
    """Analyse one or more SQP exports and write an enriched Excel report."""
    t = Thresholds(min_impr=min_impr, min_clicks=min_clicks, price_band=price_band)
    d, ctx = analyse(files, t, brand or None, competitor or None)
    write_report(d, ctx, out, top_n)
    typer.echo(f"{len(d):,} query rows across {d.entity_id.nunique()} entities -> {out}")
    typer.echo(f"own-brand tokens: {ctx['own_brand_tokens']}")
    typer.echo(f"competitor tokens: {ctx['competitor_tokens']}")
    typer.echo(d.quadrant.value_counts().to_string())



@app.command()
def dashboard(port: int = 8501) -> None:
    """Launch the Streamlit dashboard."""
    script = Path(__file__).with_name("dashboard.py")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(script), "--server.port", str(port)], check=False)


if __name__ == "__main__":
    app()
