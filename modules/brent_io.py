"""
Curva de Brent do BCB — replicação a partir de dados Bloomberg.

Metodologia (Apêndice Metodológico do RPM/RI):
  - P₀ = média de 10 dias úteis do front-month Bloomberg encerrados no cutoff
  - Cutoff = sexta-feira da semana anterior à semana da reunião do Copom
  - Strip M+1..M+6: média de 10 dias de cada contrato ICE Brent ativo
  - Contratos expirados: halfstale = (preço_stale + front_month) / 2
  - Meses passados no trimestre corrente: média mensal realizada do front-month
  - Além de M+6: crescimento de 2% a.a.

Arquivo Bloomberg esperado: Brent_full.xlsx, aba 'copia'
  - Linha 3 (índice 3): tickers no formato 'COK6 Comdty'
  - Linha 6+ (índice 6+): col 0 = serial Excel de data, demais = PX_LAST

Uso:
    from brent_io import load_brent_curve, levels_to_eps_brent

    levels, q_prev = load_brent_curve(meeting_date, bloomberg_path)
    eps = levels_to_eps_brent(levels, q_prev)
"""

from __future__ import annotations

import calendar
import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

BRENT_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "Brent_full.xlsx"

MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
INVERSE_MONTH_CODE = {v: k for k, v in MONTH_CODE.items()}


# ---------------------------------------------------------------------------
# Funções internas
# ---------------------------------------------------------------------------

def _copom_cutoff(meeting: date) -> date:
    """Sexta-feira da semana anterior à semana da reunião."""
    monday = meeting - timedelta(days=meeting.weekday())
    return monday - timedelta(days=3)


def _ice_brent_expiry(year: int, month: int) -> date:
    """Último dia útil do segundo mês antes do mês de entrega (convenção ICE Brent)."""
    exp_mo = month - 2
    exp_yr = year
    if exp_mo <= 0:
        exp_mo += 12
        exp_yr -= 1
    last = calendar.monthrange(exp_yr, exp_mo)[1]
    d = date(exp_yr, exp_mo, last)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _parse_excel_date(x) -> Optional[pd.Timestamp]:
    if pd.isna(x):
        return None
    if isinstance(x, pd.Timestamp):
        return x
    if hasattr(x, "year"):
        return pd.Timestamp(x)
    try:
        serial = int(float(x))
        if serial > 0:
            return pd.Timestamp(date(1899, 12, 30) + timedelta(days=serial))
    except (ValueError, TypeError):
        pass
    return None


def _read_bloomberg(path) -> Tuple[pd.DataFrame, Dict]:
    """
    Lê Brent_full.xlsx (aba 'copia').

    Retorna:
        df:            DatetimeIndex × tickers ('COK6', ...), preços PX_LAST
        contract_info: {ticker: {'year', 'month', 'expiry'}}
    """
    raw = pd.read_excel(path, sheet_name="copia", header=None)
    ticker_row = raw.iloc[3].tolist()
    data = raw.iloc[6:].reset_index(drop=True)

    contract_info: Dict = {}
    col_map: Dict = {}

    for col_idx, cell in enumerate(ticker_row):
        if col_idx == 0:
            continue
        s = str(cell).strip().split()[0] if pd.notna(cell) else ""
        if (s.startswith("CO") and len(s) == 4
                and s[2] in INVERSE_MONTH_CODE and s[3].isdigit()):
            mc, yd = s[2], s[3]
            yr, mo = 2020 + int(yd), INVERSE_MONTH_CODE[mc]
            contract_info[s] = {
                "year": yr, "month": mo,
                "expiry": _ice_brent_expiry(yr, mo),
            }
            col_map[col_idx] = s

    date_series = data[0].apply(_parse_excel_date)
    valid = date_series.notna()
    dates = pd.DatetimeIndex(date_series[valid])

    df = pd.DataFrame(index=dates)
    for col_idx, ticker in col_map.items():
        df[ticker] = pd.to_numeric(data[col_idx][valid].values, errors="coerce")

    return df, contract_info


def _front_month_series(df: pd.DataFrame, contract_info: Dict) -> pd.Series:
    """Série diária do contrato front-month (rola na expiração de cada contrato)."""
    prices = {}
    for ts in df.index:
        d = ts.to_pydatetime().date()
        active = {tk: info for tk, info in contract_info.items()
                  if info["expiry"] >= d}
        if not active:
            continue
        front = min(active, key=lambda c: (active[c]["year"], active[c]["month"]))
        val = df.loc[ts, front]
        if pd.notna(val) and float(val) > 0:
            prices[ts] = float(val)
    return pd.Series(prices, dtype=float)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def file_hash(path_or_bytes) -> str:
    """MD5 do arquivo Bloomberg — usado como chave de cache no Streamlit."""
    if isinstance(path_or_bytes, (str, Path)):
        data = Path(path_or_bytes).read_bytes()
    else:
        data = path_or_bytes
    return hashlib.md5(data).hexdigest()


def copom_cutoff(meeting: date) -> date:
    """Cutoff padrão BCB para uma reunião (sexta da semana anterior)."""
    return _copom_cutoff(meeting)


def load_brent_curve(
    meeting: date,
    bloomberg_path=None,
    cutoff: Optional[date] = None,
) -> Tuple[List[float], float]:
    """
    Constrói a curva de Brent BCB e retorna níveis trimestrais em USD.

    Parâmetros:
        meeting:        data da reunião do Copom
        bloomberg_path: caminho ou BytesIO do Brent_full.xlsx; se None, usa
                        data/Brent_full.xlsx do repo
        cutoff:         data de referência (padrão = sexta semana anterior)

    Retorna:
        levels:  lista de floats — nível USD por trimestre, começando no
                 trimestre corrente (Q do meeting) e indo ~12 trimestres à frente
        q_prev:  nível USD do trimestre imediatamente anterior ao corrente
                 (usado para calcular o primeiro eps_brent)
    """
    if bloomberg_path is None:
        bloomberg_path = BRENT_DEFAULT_PATH
    if cutoff is None:
        cutoff = _copom_cutoff(meeting)

    df, contract_info = _read_bloomberg(bloomberg_path)
    front_series = _front_month_series(df, contract_info)
    monthly_hist = front_series.resample("MS").mean()

    # Janela de 10 dias úteis encerrados no cutoff
    avail = df.index[df.index <= pd.Timestamp(cutoff)]
    window = avail[-10:]

    # P₀
    p0 = float(front_series[front_series.index <= pd.Timestamp(cutoff)].tail(10).mean())

    # Front-month fixo no cutoff (referência do halfstale)
    active_at_cutoff = {tk: info for tk, info in contract_info.items()
                        if info["expiry"] >= cutoff}
    front_tk = min(active_at_cutoff,
                   key=lambda c: (active_at_cutoff[c]["year"],
                                  active_at_cutoff[c]["month"]))
    front_price = float(df.loc[window, front_tk].dropna().mean())

    # Strip M+1..M+6 ativos
    strip: Dict[date, float] = {}
    active_count = 0
    for step in range(1, 13):
        total = meeting.month + step
        yr = meeting.year + (total - 1) // 12
        mo = (total - 1) % 12 + 1
        mdate = date(yr, mo, 1)

        ticker = next((tk for tk, info in contract_info.items()
                       if info["year"] == yr and info["month"] == mo), None)
        if ticker is None:
            break

        vals = df.loc[window, ticker].dropna()
        if vals.empty:
            break

        if contract_info[ticker]["expiry"] >= cutoff:
            strip[mdate] = float(vals.mean())
            active_count += 1
            if active_count >= 6:
                break
        else:
            strip[mdate] = (float(vals.mean()) + front_price) / 2

    anchor_date = max(strip)
    anchor_price = strip[anchor_date]

    # Série mensal: um trimestre antes do corrente até +12 trimestres à frente
    meeting_month = date(meeting.year, meeting.month, 1)
    q_start_mo = ((meeting.month - 1) // 3) * 3 + 1
    q_start = date(meeting.year, q_start_mo, 1)

    # Um trimestre antes para calcular q_prev
    prev_q_start = date(
        q_start.year if q_start.month > 3 else q_start.year - 1,
        (q_start.month - 4) % 12 + 1 if q_start.month > 3 else q_start.month + 9,
        1,
    )

    prices: Dict[date, float] = {}
    for ts in pd.date_range(prev_q_start, periods=52, freq="MS"):
        m = ts.date()
        if m < meeting_month:
            val = monthly_hist.get(ts, np.nan)
            prices[m] = float(val) if not pd.isna(val) else np.nan
        elif m == meeting_month:
            prices[m] = p0
        elif m in strip:
            prices[m] = strip[m]
        else:
            months_ahead = ((m.year - anchor_date.year) * 12
                            + (m.month - anchor_date.month))
            prices[m] = anchor_price * (1.02) ** (months_ahead / 12)

    # Agrega trimestralmente
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in prices])
    series = pd.Series(list(prices.values()), index=idx)
    quarterly = (series.resample("QS-JAN").mean().round(2))

    # Separa q_prev do restante
    q_prev_ts = pd.Timestamp(prev_q_start)
    q_curr_ts = pd.Timestamp(q_start)

    q_prev = float(quarterly.get(q_prev_ts, np.nan))
    levels = [
        float(v) for ts, v in quarterly.items()
        if ts >= q_curr_ts and not pd.isna(v)
    ]

    return levels, q_prev


def levels_to_eps_brent(levels: List[float], q_prev: float) -> List[float]:
    """
    Converte lista de níveis trimestrais de Brent (USD) em eps_brent (%∆).

    eps_brent[0] = (levels[0] / q_prev - 1) * 100
    eps_brent[t] = (levels[t] / levels[t-1] - 1) * 100

    Zeros do final são removidos (tail endógena no modelo).
    """
    all_levels = [q_prev] + levels
    eps = [
        round((all_levels[i] / all_levels[i - 1] - 1) * 100, 4)
        for i in range(1, len(all_levels))
    ]
    # Remove trailing zeros
    while eps and abs(eps[-1]) < 1e-6:
        eps.pop()
    return eps


def quarter_labels(meeting: date, n: int) -> List[str]:
    """Rótulos de trimestre a partir do trimestre da reunião: ['2026Q2', ...]."""
    q_start_mo = ((meeting.month - 1) // 3) * 3 + 1
    labels = []
    yr, mo = meeting.year, q_start_mo
    for _ in range(n):
        labels.append(f"{yr}Q{(mo - 1) // 3 + 1}")
        mo += 3
        if mo > 12:
            mo -= 12
            yr += 1
    return labels
