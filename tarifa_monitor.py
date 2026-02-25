#!/usr/bin/env python3
"""
Monitora tarifas residenciais (B1 convencional) por distribuidora.

Recursos:
- Busca histórico de tarifas na API aberta da ANEEL.
- Calcula tarifa total (TE + TUSD) e reajustes entre vigências.
- Compara reajuste acumulado e CAGR de 5 anos com IPCA (IBGE SIDRA).
- Gera CSVs por distribuidora e um resumo consolidado.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


ANEEL_BASE = "https://dadosabertos.aneel.gov.br/api/3/action/datastore_search"
ANEEL_RESOURCE_ID = "fcf2906c-7c32-4b9b-a637-054e7a5234f4"
IBGE_BASE = "https://apisidra.ibge.gov.br/values/t/1737/n1/1/p/{start}-{end}/f/n"


@dataclass
class TariffRow:
    sig_agente: str
    ini: dt.date
    fim: dt.date
    te: float
    tusd: float

    @property
    def total(self) -> float:
        return self.te + self.tusd


def fetch_json(url: str, params: Optional[Dict[str, str]] = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "tarifa-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_decimal_br(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def safe_filename(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_") or "distribuidora"


def aneel_filters(sig_agente: str) -> Dict[str, str]:
    # Filtro para série comparável de consumidor residencial B1 convencional.
    return {
        "SigAgente": sig_agente,
        "DscClasse": "Residencial",
        "DscSubGrupo": "B1",
        "DscBaseTarifaria": "Tarifa de Aplicação",
        "DscModalidadeTarifaria": "Convencional",
        "DscSubClasse": "Residencial",
        "DscDetalhe": "Não se aplica",
        "NomPostoTarifario": "Não se aplica",
    }


def fetch_aneel_history(sig_agente: str, limit: int = 100) -> List[TariffRow]:
    filters = aneel_filters(sig_agente)
    offset = 0
    rows: List[TariffRow] = []

    while True:
        payload = fetch_json(
            ANEEL_BASE,
            {
                "resource_id": ANEEL_RESOURCE_ID,
                "filters": json.dumps(filters, ensure_ascii=False),
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        if not payload.get("success"):
            raise RuntimeError(f"ANEEL API retornou erro para {sig_agente}: {payload}")

        result = payload["result"]
        records = result.get("records", [])
        if not records:
            break

        for r in records:
            rows.append(
                TariffRow(
                    sig_agente=r["SigAgente"],
                    ini=parse_date(r["DatInicioVigencia"]),
                    fim=parse_date(r["DatFimVigencia"]),
                    te=parse_decimal_br(r["VlrTE"]),
                    tusd=parse_decimal_br(r["VlrTUSD"]),
                )
            )

        offset += len(records)
        if offset >= result.get("total", 0):
            break

    dedup: Dict[Tuple[str, dt.date], TariffRow] = {}
    for row in rows:
        dedup[(row.sig_agente, row.ini)] = row

    return sorted(dedup.values(), key=lambda x: x.ini)


def pick_base_row(rows: List[TariffRow], target: dt.date) -> Optional[TariffRow]:
    for row in rows:
        if row.ini <= target <= row.fim:
            return row
    prev = [r for r in rows if r.ini <= target]
    if prev:
        return prev[-1]
    return rows[0] if rows else None


def fetch_ipca_indices(start: dt.date, end: dt.date) -> List[Tuple[dt.date, float]]:
    start_ym = f"{start.year:04d}{start.month:02d}"
    end_ym = f"{end.year:04d}{end.month:02d}"
    payload = fetch_json(IBGE_BASE.format(start=start_ym, end=end_ym))

    out: List[Tuple[dt.date, float]] = []
    for row in payload:
        if row.get("D3N") != "IPCA - Número-índice (base: dezembro de 1993 = 100)":
            continue
        month_pt = row["D2N"]
        year = int(month_pt.split()[-1])
        month_name = month_pt.split()[0].lower()
        month_map = {
            "janeiro": 1,
            "fevereiro": 2,
            "março": 3,
            "abril": 4,
            "maio": 5,
            "junho": 6,
            "julho": 7,
            "agosto": 8,
            "setembro": 9,
            "outubro": 10,
            "novembro": 11,
            "dezembro": 12,
        }
        month = month_map[month_name]
        out.append((dt.date(year, month, 1), float(row["V"])))

    return sorted(out, key=lambda x: x[0])


def cagr(start_value: float, end_value: float, years: float) -> float:
    if start_value <= 0 or end_value <= 0 or years <= 0:
        return math.nan
    return (end_value / start_value) ** (1 / years) - 1


def pct_change(start_value: float, end_value: float) -> float:
    if start_value == 0:
        return math.nan
    return (end_value / start_value) - 1


def write_history_csv(path: pathlib.Path, rows: List[TariffRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ascending = sorted(rows, key=lambda x: x.ini)
    pct_by_ini: Dict[dt.date, Optional[float]] = {}
    prev_total: Optional[float] = None
    for row in ascending:
        total = row.total
        if prev_total is None:
            pct_by_ini[row.ini] = None
        else:
            pct_by_ini[row.ini] = round(((total / prev_total) - 1) * 100, 2)
        prev_total = total

    ordered = list(reversed(ascending))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "DatInicioVigencia",
                "DatFimVigencia",
                "TE",
                "TUSD",
                "TarifaTotal",
                "Reajuste_%_vs_anterior",
            ]
        )
        for row in ordered:
            total = row.total
            reaj = pct_by_ini[row.ini]
            w.writerow(
                [
                    row.ini.isoformat(),
                    row.fim.isoformat(),
                    round(row.te, 2),
                    round(row.tusd, 2),
                    round(total, 2),
                    "" if reaj is None else reaj,
                ]
            )


def run(distribuidoras: Iterable[str], output_dir: pathlib.Path) -> None:
    distribuidoras = [d.strip() for d in distribuidoras if d.strip()]
    if not distribuidoras:
        raise ValueError("Lista de distribuidoras está vazia.")

    now = dt.date.today()
    summary_rows: List[dict] = []

    for dist in distribuidoras:
        history = fetch_aneel_history(dist)
        if not history:
            print(f"[AVISO] Sem dados para {dist} com os filtros definidos.", file=sys.stderr)
            continue

        latest = history[-1]
        target = latest.ini - dt.timedelta(days=int(365.25 * 5))
        base = pick_base_row(history, target)
        if base is None:
            print(f"[AVISO] Histórico insuficiente para {dist}.", file=sys.stderr)
            continue

        years_energy = max((latest.ini - base.ini).days / 365.25, 0.0001)
        energy_5y = pct_change(base.total, latest.total)
        energy_cagr = cagr(base.total, latest.total, years_energy)

        ipca_start = dt.date(base.ini.year, base.ini.month, 1)
        ipca_end = dt.date(latest.ini.year, latest.ini.month, 1)
        ipca_series = fetch_ipca_indices(ipca_start, ipca_end)
        if not ipca_series:
            raise RuntimeError("Não foi possível obter série IPCA no intervalo solicitado.")
        ipca_base = ipca_series[0]
        ipca_latest = ipca_series[-1]

        years_ipca = max((ipca_latest[0] - ipca_base[0]).days / 365.25, 0.0001)
        ipca_5y = pct_change(ipca_base[1], ipca_latest[1])
        ipca_cagr = cagr(ipca_base[1], ipca_latest[1], years_ipca)

        dist_slug = safe_filename(dist)
        hist_path = output_dir / "historico" / f"{dist_slug}.csv"
        write_history_csv(hist_path, history)

        summary_rows.append(
            {
                "data_execucao": now.isoformat(),
                "distribuidora": dist,
                "vigencia_base_energia": base.ini.isoformat(),
                "vigencia_final_energia": latest.ini.isoformat(),
                "tarifa_base": round(base.total, 2),
                "tarifa_final": round(latest.total, 2),
                "reajuste_energia_5a_pct": round(energy_5y * 100, 2),
                "cagr_energia_5a_pct_aa": round(energy_cagr * 100, 2),
                "mes_base_ipca": ipca_base[0].isoformat(),
                "mes_final_ipca": ipca_latest[0].isoformat(),
                "ipca_5a_pct": round(ipca_5y * 100, 2),
                "cagr_ipca_5a_pct_aa": round(ipca_cagr * 100, 2),
                "spread_vs_ipca_pp": round((energy_5y - ipca_5y) * 100, 2),
                "arquivo_historico": str(hist_path),
            }
        )

    if not summary_rows:
        raise RuntimeError("Nenhuma distribuidora gerou resultado.")

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "resumo_comparativo.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    snapshot_path = output_dir / "snapshots_mensais.csv"
    append_header = not snapshot_path.exists()
    with snapshot_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        if append_header:
            w.writeheader()
        w.writerows(summary_rows)

    print(f"Resumo: {summary_path}")
    print(f"Snapshots: {snapshot_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor de tarifas residenciais ANEEL vs IPCA.")
    parser.add_argument(
        "--distribuidoras",
        help="Lista separada por vírgula (ex.: CEEE-D,CPFL PAULISTA,ENEL SP).",
    )
    parser.add_argument(
        "--config",
        default="distribuidoras.txt",
        help="Arquivo com uma distribuidora por linha (padrão: distribuidoras.txt).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Diretório de saída para relatórios (padrão: output).",
    )
    return parser.parse_args()


def load_distribuidoras(args: argparse.Namespace) -> List[str]:
    if args.distribuidoras:
        return [x.strip() for x in args.distribuidoras.split(",") if x.strip()]
    cfg = pathlib.Path(args.config)
    if not cfg.exists():
        raise FileNotFoundError(
            f"Arquivo {cfg} não encontrado. Crie com uma distribuidora por linha "
            "ou use --distribuidoras."
        )
    return [line.strip() for line in cfg.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    args = parse_args()
    try:
        distribuidoras = load_distribuidoras(args)
        run(distribuidoras, pathlib.Path(args.output_dir))
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
