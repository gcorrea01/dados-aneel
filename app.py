#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import pathlib
from typing import Dict, List, Tuple

import altair as alt
import pandas as pd
import streamlit as st

from tarifa_monitor import (
    TariffRow,
    cagr,
    fetch_aneel_history,
    fetch_ipca_indices,
    pct_change,
)


st.set_page_config(page_title="Monitor Tarifas ANEEL x IPCA", layout="wide")


def load_distribuidoras(path: pathlib.Path) -> List[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pick_base_row(rows: List[TariffRow], years: int) -> TariffRow:
    latest = rows[-1]
    target = latest.ini - dt.timedelta(days=int(365.25 * years))
    for row in rows:
        if row.ini <= target <= row.fim:
            return row
    previous = [r for r in rows if r.ini <= target]
    if previous:
        return previous[-1]
    return rows[0]


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_analysis(sig_agente: str, years: int) -> Dict:
    history = fetch_aneel_history(sig_agente)
    if not history:
        return {"history": []}

    latest = history[-1]
    base = pick_base_row(history, years)
    energy_years = max((latest.ini - base.ini).days / 365.25, 0.0001)

    energy_acc = pct_change(base.total, latest.total)
    energy_cagr = cagr(base.total, latest.total, energy_years)

    ipca_start = dt.date(base.ini.year, base.ini.month, 1)
    ipca_end = dt.date(latest.ini.year, latest.ini.month, 1)
    ipca = fetch_ipca_indices(ipca_start, ipca_end)
    ipca_base = ipca[0]
    ipca_latest = ipca[-1]
    ipca_years = max((ipca_latest[0] - ipca_base[0]).days / 365.25, 0.0001)
    ipca_acc = pct_change(ipca_base[1], ipca_latest[1])
    ipca_cagr = cagr(ipca_base[1], ipca_latest[1], ipca_years)

    return {
        "history": history,
        "latest": latest,
        "base": base,
        "energy_acc": energy_acc,
        "energy_cagr": energy_cagr,
        "ipca": ipca,
        "ipca_acc": ipca_acc,
        "ipca_cagr": ipca_cagr,
    }


def build_chart_series(
    history: List[TariffRow],
    ipca: List[Tuple[dt.date, float]],
    base: TariffRow,
) -> List[Dict]:
    energy_rows = [r for r in history if r.ini >= base.ini]
    if not energy_rows:
        energy_rows = [history[-1]]
    energy_base = energy_rows[0].total

    points: List[Dict] = []
    for row in energy_rows:
        points.append(
            {
                "data": row.ini.isoformat(),
                "serie": "Energia",
                "variacao_pct": round((row.total / energy_base - 1) * 100, 2),
            }
        )

    ipca_base = ipca[0][1]
    # Ponto de partida comum com a mesma data da base de energia.
    points.append(
        {
            "data": base.ini.isoformat(),
            "serie": "IPCA",
            "variacao_pct": 0.0,
        }
    )
    for date_ref, value in ipca:
        points.append(
            {
                "data": date_ref.isoformat(),
                "serie": "IPCA",
                "variacao_pct": round((value / ipca_base - 1) * 100, 2),
            }
        )
    return points


st.title("Monitor de Tarifas Residenciais: ANEEL x IPCA")
st.caption(
    "Filtro: B1 Residencial | Tarifa de Aplicação | Convencional | "
    "SubClasse Residencial | DscDetalhe/NomPosto = Não se aplica"
)

cfg = pathlib.Path("distribuidoras.txt")
distribuidoras = load_distribuidoras(cfg)
if not distribuidoras:
    st.error("Arquivo distribuidoras.txt vazio ou inexistente.")
    st.stop()

col_left, col_right = st.columns([3, 1])
with col_left:
    selected = st.selectbox("Concessionária", options=distribuidoras, index=0)
with col_right:
    janela_anos = st.selectbox("Janela de análise", options=[5, 10], index=0)

if st.button("Atualizar agora (buscar dados mais recentes)"):
    st.cache_data.clear()
    st.rerun()

with st.spinner("Consultando ANEEL e IBGE..."):
    data = load_analysis(selected, janela_anos)

if not data["history"]:
    st.warning("Sem dados para essa concessionária com os filtros atuais.")
    st.stop()

latest: TariffRow = data["latest"]
base: TariffRow = data["base"]

mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("Tarifa atual (TE+TUSD)", f"R$ {latest.total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
mc2.metric("Início vigência atual", latest.ini.isoformat())
mc3.metric(f"Reajuste {janela_anos} anos", f"{data['energy_acc'] * 100:.2f}%")
mc4.metric(f"CAGR energia ({janela_anos} anos)", f"{data['energy_cagr'] * 100:.2f}% a.a.")

mc5, mc6, mc7 = st.columns(3)
mc5.metric(f"IPCA acumulado ({janela_anos} anos)", f"{data['ipca_acc'] * 100:.2f}%")
mc6.metric(f"CAGR IPCA ({janela_anos} anos)", f"{data['ipca_cagr'] * 100:.2f}% a.a.")
mc7.metric("Spread vs IPCA", f"{(data['energy_acc'] - data['ipca_acc']) * 100:.2f} p.p.")

st.caption(
    f"Base energia: {base.ini.isoformat()} | Atual energia: {latest.ini.isoformat()} | "
    f"Base IPCA: {data['ipca'][0][0].isoformat()} | Atual IPCA: {data['ipca'][-1][0].isoformat()}"
)

points = build_chart_series(data["history"], data["ipca"], base)
st.subheader("Reajuste acumulado: Energia x IPCA")
if not points:
    st.warning("Sem pontos suficientes para o gráfico no período selecionado.")
else:
    chart_df = pd.DataFrame(points)
    chart_df["data"] = pd.to_datetime(chart_df["data"], errors="coerce")
    chart_df = chart_df.dropna(subset=["data", "serie", "variacao_pct"])

    if chart_df.empty:
        st.warning("Sem dados válidos para renderizar o gráfico.")
    else:
        chart = (
            alt.Chart(chart_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("data:T", title="Data"),
                y=alt.Y("variacao_pct:Q", title="Variação acumulada (%)"),
                color=alt.Color("serie:N", title=""),
                tooltip=[
                    alt.Tooltip("data:T", title="Data"),
                    alt.Tooltip("serie:N", title="Série"),
                    alt.Tooltip("variacao_pct:Q", title="Variação (%)", format=".2f"),
                ],
            )
        )
        st.altair_chart(chart, use_container_width=True)

st.subheader("Histórico de tarifas")
hist_rows = sorted(data["history"], key=lambda x: x.ini, reverse=True)
table = []
asc_rows = sorted(data["history"], key=lambda x: x.ini)
pct_by_ini: Dict[dt.date, str] = {}
prev_total = None
for row in asc_rows:
    if prev_total is None or prev_total <= 0:
        pct_by_ini[row.ini] = ""
    else:
        pct_by_ini[row.ini] = f"{((row.total / prev_total) - 1) * 100:.2f}%"
    prev_total = row.total

for row in hist_rows:
    table.append(
        {
            "Início Vigência": row.ini.isoformat(),
            "Fim Vigência": row.fim.isoformat(),
            "TE": round(row.te, 2),
            "TUSD": round(row.tusd, 2),
            "Tarifa Total": round(row.total, 2),
            "Reajuste vs anterior": pct_by_ini[row.ini],
        }
    )

st.dataframe(table, use_container_width=True, hide_index=True)
