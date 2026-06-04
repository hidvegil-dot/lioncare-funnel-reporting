from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from weekly_ai_summary import advisor_summary


def write_weekly_ghl_markdown(*, markdown_path: Path, report: dict[str, Any]) -> None:
    diagnosis = report["diagnosis"]
    metrics = report["metrics"]
    lines = [
        f"# Heti GHL vezetői funnel summary ({report['week_start']} - {report['week_end']})",
        "",
        f"**Fő vezetői válasz:** {diagnosis['ceo_summary']}",
        "",
        f"**Mi működik?** {diagnosis['what_works']}",
        f"**Hol veszítünk pénzt?** {diagnosis['money_loss']}",
        f"**Fő szűk keresztmetszet:** {diagnosis['main_bottleneck']}",
        "",
        "## Következő hét 3 döntése",
        *[f"- {item}" for item in diagnosis["recommended_actions"]],
        "",
        "## Fő KPI-k",
        f"- Új lead: {metrics['new_leads']}",
        f"- Foglalás: {metrics['bookings']}",
        f"- Megjelent: {metrics['showed']}",
        f"- No-show: {metrics['no_show']}",
        f"- Törölt / lemondott: {metrics['cancelled']}",
        f"- Won: {metrics['won']}",
        f"- Lost: {metrics['lost']}",
        f"- Lead → foglalás: {metrics['lead_to_booking_rate']}%",
        f"- Foglalás → megjelent: {metrics['booking_to_show_rate']}%",
        f"- Megjelent → szerződés: {metrics['show_to_close_rate']}%",
        "",
        "## Tanácsadói összefoglalók",
        *[f"- {advisor_summary(row)}" for row in metrics["advisor_rows"]],
        "",
        f"## CRM adatminőség\n{diagnosis['crm_data_quality_note']}",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_weekly_ghl_html(*, html_path: Path, report: dict[str, Any]) -> None:
    diagnosis = report["diagnosis"]
    metrics = report["metrics"]
    html = f"""<!doctype html>
<html lang="hu">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Heti GHL Funnel Riport</title>
  <style>
    body {{ margin: 0; font-family: Inter, Arial, sans-serif; background: #f6f4ef; color: #1f2d3a; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    header, section {{ background: #fff; border: 1px solid #ded7c8; border-radius: 8px; padding: 22px; margin-bottom: 18px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .muted {{ color: #667085; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid #e8e0d2; border-radius: 8px; padding: 14px; background: #fbfaf7; }}
    .label {{ font-size: 12px; text-transform: uppercase; color: #667085; font-weight: 700; }}
    .value {{ font-size: 28px; font-weight: 800; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #ece6db; padding: 10px; text-align: left; font-size: 14px; }}
    th {{ background: #f3ecde; }}
    .decision-list li {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
<main>
  <header>
    <div class="muted">Vizsgált hét: {escape(report['week_start'])} - {escape(report['week_end'])}</div>
    <h1>Heti GHL vezetői funnel riport</h1>
    <p>{escape(diagnosis['ceo_summary'])}</p>
  </header>
  <section>
    <h2>CEO summary</h2>
    <p><strong>Mi működik?</strong> {escape(diagnosis['what_works'])}</p>
    <p><strong>Hol veszítünk pénzt?</strong> {escape(diagnosis['money_loss'])}</p>
    <p><strong>Fő szűk keresztmetszet:</strong> {escape(diagnosis['main_bottleneck'])}</p>
    <ol class="decision-list">{''.join(f'<li>{escape(item)}</li>' for item in diagnosis['recommended_actions'])}</ol>
  </section>
  <section>
    <h2>Heti GHL KPI-k</h2>
    <div class="grid">
      {_metric('Új lead', metrics['new_leads'])}
      {_metric('Foglalás', metrics['bookings'])}
      {_metric('Megjelent', metrics['showed'])}
      {_metric('No-show', metrics['no_show'])}
      {_metric('Törölt / lemondott', metrics['cancelled'])}
      {_metric('Won', metrics['won'])}
      {_metric('Lost', metrics['lost'])}
      {_metric('Lead → foglalás', f"{metrics['lead_to_booking_rate']}%")}
      {_metric('Foglalás → megjelent', f"{metrics['booking_to_show_rate']}%")}
      {_metric('Megjelent → szerződés', f"{metrics['show_to_close_rate']}%")}
    </div>
  </section>
  <section>
    <h2>Tanácsadói teljesítmény</h2>
    <table>
      <thead><tr><th>Tanácsadó</th><th>Lead</th><th>Foglalás</th><th>Megjelent</th><th>No-show</th><th>Törölt</th><th>Won</th><th>Lost</th><th>L→F</th><th>F→M</th><th>M→S</th></tr></thead>
      <tbody>{''.join(_advisor_row(row) for row in metrics['advisor_rows'])}</tbody>
    </table>
  </section>
  <section>
    <h2>Forrás / landing bontás</h2>
    <table><thead><tr><th>Forrás</th><th>Lead</th></tr></thead><tbody>{''.join(_breakdown_row(item, 'source') for item in metrics['source_breakdown'])}</tbody></table>
  </section>
  <section>
    <h2>Heti döntési javaslatok</h2>
    <p><strong>Mit tartsunk?</strong> {escape(diagnosis['keep'])}</p>
    <p><strong>Mit skálázzunk?</strong> {escape(diagnosis['scale'])}</p>
    <p><strong>Mit állítsunk le?</strong> {escape(diagnosis['stop'])}</p>
    <p><strong>Sales folyamat javítása:</strong> {escape(diagnosis['sales_fix'])}</p>
    <p><strong>GHL státuszkezelés:</strong> {escape(diagnosis['crm_fix'])}</p>
    <p><strong>Operatív feladatok:</strong> {escape(diagnosis['ops_tasks'])}</p>
    <p><strong>Adatminőség:</strong> {escape(diagnosis['crm_data_quality_note'])}</p>
  </section>
</main>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")


def _metric(label: str, value: Any) -> str:
    return f'<div class="metric"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def _advisor_row(row: dict[str, Any]) -> str:
    cells = [
        row.get("advisor", ""),
        row.get("new_leads", 0),
        row.get("bookings", 0),
        row.get("showed", 0),
        row.get("no_show", 0),
        row.get("cancelled", 0),
        row.get("won", 0),
        row.get("lost", 0),
        f"{row.get('lead_to_booking_rate', 0)}%",
        f"{row.get('booking_to_show_rate', 0)}%",
        f"{row.get('show_to_close_rate', 0)}%",
    ]
    return "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in cells) + "</tr>"


def _breakdown_row(item: dict[str, Any], label_key: str) -> str:
    return f"<tr><td>{escape(str(item.get(label_key, '')))}</td><td>{escape(str(item.get('count', 0)))}</td></tr>"
