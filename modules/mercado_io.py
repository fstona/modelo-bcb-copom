"""
Dados de mercado para o dashboard Copom.

Câmbio — regra BCB para PTAX de referência:
  - Janela: últimos 10 dias úteis encerrada na sexta-feira da semana
    ANTERIOR à reunião do Copom (regra válida tanto dentro quanto fora
    da semana de Copom, pois a data da reunião é sempre conhecida).
  - Arredondamento: 5 centavos (nearest R$0,05).

Fonte: BCB SGS, série 10813 (taxa de câmbio livre – dólar – venda – diária).
"""

import json
import re
import decimal
import requests
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

CALENDAR_PATH = Path(__file__).parent.parent / "data" / "copom_calendar.json"
SGS_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.10813/dados"
    "?formato=json&dataInicial={start}&dataFinal={end}"
)

MESES_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4,
    "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}

MESES_NUM_PT = {v: k.capitalize() for k, v in MESES_PT.items()}


# ---------------------------------------------------------------------------
# Calendário do Copom
# ---------------------------------------------------------------------------

def load_copom_calendar() -> list:
    """Retorna lista de date das reuniões do Copom, em ordem crescente."""
    with open(CALENDAR_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [date.fromisoformat(d) for d in data["meetings"]]


def find_meeting_date(copom_name: str, calendar: list) -> Optional[date]:
    """
    Mapeia nome da reunião (ex: 'Jun26') para a data no calendário.
    Usa o mês do nome para identificar unicamente a reunião.
    """
    m = re.match(r"([A-Za-z]+)(\d{2,4})", copom_name.strip())
    if not m:
        return None
    mes_str = m.group(1).lower()[:3]
    ano_str = m.group(2)
    if mes_str not in MESES_PT:
        return None
    mes = MESES_PT[mes_str]
    ano = int(ano_str) + (2000 if len(ano_str) == 2 else 0)
    for d in calendar:
        if d.year == ano and d.month == mes:
            return d
    return None


def get_meeting_position(meeting_date: date, calendar: list) -> int:
    """
    Retorna a posição da reunião dentro do trimestre (1 ou 2).
    1ª reunião → usa RPM completo como baseline.
    2ª reunião → usa projeções esparsas da decisão do Copom.
    """
    q = (meeting_date.month - 1) // 3 + 1
    quarter_meetings = sorted(
        d for d in calendar if d.year == meeting_date.year and (d.month - 1) // 3 + 1 == q
    )
    try:
        return quarter_meetings.index(meeting_date) + 1
    except ValueError:
        return 1


def previous_meeting(meeting_date: date, calendar: list) -> Optional[date]:
    """Retorna a reunião imediatamente anterior à data fornecida."""
    earlier = [d for d in calendar if d < meeting_date]
    return earlier[-1] if earlier else None


def meeting_name_from_date(meeting_date: date) -> str:
    """Converte data de reunião para nome padrão tipo 'Abr26'."""
    return f"{MESES_NUM_PT[meeting_date.month]}{str(meeting_date.year)[2:]}"


# ---------------------------------------------------------------------------
# PTAX — BCB SGS série 10813
# ---------------------------------------------------------------------------

def _fetch_sgb(start: date, end: date) -> list:
    """
    Busca taxa de câmbio (dólar, venda, diária) no SGS.
    Retorna lista de dict {date: date, rate: float}.
    """
    url = SGS_URL.format(
        start=start.strftime("%d/%m/%Y"),
        end=end.strftime("%d/%m/%Y"),
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    return [
        {"date": r["data"], "rate": float(r["valor"])}
        for r in rows
    ]


def _ref_date_for_meeting(meeting_date: date) -> date:
    """
    Última sexta-feira da semana anterior à semana do Copom.
    Ex: reunião em 17/jun (quarta) → semana 15-19/jun → sexta anterior = 12/jun.
    """
    monday_of_week = meeting_date - timedelta(days=meeting_date.weekday())
    return monday_of_week - timedelta(days=3)   # sexta-feira anterior


def fetch_ptax_window(ref_date: date, n_days: int = 10) -> list:
    """
    Busca os últimos `n_days` dias úteis de PTAX encerrados em `ref_date`.
    """
    start = ref_date - timedelta(days=n_days * 3)
    rows = _fetch_sgb(start, ref_date)
    return rows[-n_days:]


def round_to_5cents(value: float) -> float:
    """Arredonda para o múltiplo de R$0,05 mais próximo (round half-up)."""
    d = decimal.Decimal(str(round(value, 6)))
    unit = decimal.Decimal("0.05")
    return float((d / unit).quantize(decimal.Decimal("1"),
                                     rounding=decimal.ROUND_HALF_UP) * unit)


# ---------------------------------------------------------------------------
# Interface principal
# ---------------------------------------------------------------------------

def calc_ptax_para_reuniao(meeting_date: date, today: Optional[date] = None) -> dict:
    """
    Calcula a sugestão de PTAX para uma reunião do Copom.

    Regra BCB: janela de 10 dias úteis encerrada na sexta-feira da semana
    anterior à reunião. Se essa sexta ainda não chegou (reunião futura),
    usa os dados disponíveis até hoje — com nota de janela parcial.

    Returns dict com 'avg', 'sugestao', 'rates', 'ref_date', 'partial', 'error'.
    """
    if today is None:
        today = date.today()
    try:
        ref = _ref_date_for_meeting(meeting_date)
        effective_ref = min(ref, today)   # não busca além do disponível
        partial = effective_ref < ref

        rows = fetch_ptax_window(effective_ref, n_days=10)
        if not rows:
            return {"error": "Nenhuma cotação PTAX encontrada no período.", "sugestao": None}
        avg = sum(r["rate"] for r in rows) / len(rows)
        return {
            "ref_date": effective_ref,
            "partial":  partial,
            "rates":    rows,
            "avg":      round(avg, 4),
            "sugestao": round_to_5cents(avg),
            "error":    None,
        }
    except requests.exceptions.Timeout:
        return {"error": "Timeout ao buscar PTAX no SGS/BCB.", "sugestao": None}
    except requests.exceptions.RequestException as e:
        return {"error": f"Erro na API BCB SGS: {e}", "sugestao": None}
    except Exception as e:
        return {"error": f"Erro inesperado: {e}", "sugestao": None}


def ref_friday_for_meeting(meeting_date: date) -> date:
    """Sexta-feira da semana anterior à semana do Copom (pública)."""
    return _ref_date_for_meeting(meeting_date)


def calc_ptax_atual(today: date, calendar: list) -> dict:
    """
    Sugestão de PTAX para a próxima reunião a partir de hoje.
    Se hoje já está além de todas as reuniões, usa a última.
    """
    upcoming = [d for d in calendar if d >= today]
    meeting = upcoming[0] if upcoming else calendar[-1]
    result = calc_ptax_para_reuniao(meeting, today=today)
    result["meeting_used"] = meeting
    return result


# ---------------------------------------------------------------------------
# Selic / Focus — Olinda API (ExpectativasMercadoSelic) + SGS 432
# ---------------------------------------------------------------------------

SELIC_OLINDA_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
    "ExpectativasMercadoSelic"
    "?$filter=Data ge '{start}' and Data le '{end}' and baseCalculo eq 1"
    "&$format=json&$select=Data,Reuniao,Mediana&$top=500&$orderby=Data asc,Reuniao asc"
)

SGS_432_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados"
    "?formato=json&dataInicial={start}&dataFinal={end}"
)


def fetch_selic_focus_on_date(focus_date: date) -> tuple:
    """
    Busca expectativas Selic do Focus na data mais recente disponível até focus_date.

    Returns (date_used: str | None, {Reuniao: Mediana}).
    Reuniao no formato 'R3/2026'. Retorna ({}, None) em caso de erro.
    """
    try:
        start = focus_date - timedelta(days=30)
        url = SELIC_OLINDA_URL.format(
            start=start.strftime("%Y-%m-%d"),
            end=focus_date.strftime("%Y-%m-%d"),
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return None, {}
        max_date = max(row["Data"] for row in items)
        result = {
            row["Reuniao"]: float(row["Mediana"])
            for row in items
            if row["Data"] == max_date and row["Mediana"] is not None
        }
        return max_date, result
    except Exception:
        return None, {}


def get_selic_sgs432(meeting_date: date) -> Optional[float]:
    """
    Busca a taxa Selic (SGS 432) no primeiro dia útil após a reunião do Copom.
    """
    try:
        d1 = meeting_date + timedelta(days=1)
        end = meeting_date + timedelta(days=7)
        url = SGS_432_URL.format(
            start=d1.strftime("%d/%m/%Y"),
            end=end.strftime("%d/%m/%Y"),
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
        return float(rows[0]["valor"]) if rows else None
    except Exception:
        return None


def build_meeting_label_map(calendar: list) -> dict:
    """
    Constrói mapeamento de rótulo Focus ('R3/2026') para metadados de reunião.

    Agrupa por ano, atribui n=1,2,... em ordem crescente.
    Mapeamento n→trimestre: R1+R2→Q1, R3+R4→Q2, R5+R6→Q3, R7+R8→Q4.

    Returns {label: {"date": date, "quarter": "Q2/2026", "n": int}}.
    """
    from itertools import groupby as _groupby
    label_map = {}
    for year, it in _groupby(sorted(calendar), key=lambda d: d.year):
        for n, meeting_date in enumerate(list(it), start=1):
            q = (n - 1) // 2 + 1
            label = f"R{n}/{year}"
            label_map[label] = {"date": meeting_date, "quarter": f"Q{q}/{year}", "n": n}
    return label_map


def selic_label_to_quarter(label: str, label_map: Optional[dict] = None) -> Optional[str]:
    """Converte rótulo Focus 'R3/2026' → 'Q2/2026'. Usa label_map se disponível."""
    if label_map and label in label_map:
        return label_map[label]["quarter"]
    m = re.match(r"R(\d+)/(\d{4})", label)
    if not m:
        return None
    n, year = int(m.group(1)), int(m.group(2))
    return f"Q{(n - 1) // 2 + 1}/{year}"


# ---------------------------------------------------------------------------
# Expectativas Focus — IPCA trimestral + 12m suavizado (Olinda)
# ---------------------------------------------------------------------------

IPCA_ANUAL_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
    "ExpectativasMercadoInflacaoAnuais"
    "?$filter=Indicador eq 'IPCA' and Data ge '{start}' and Data le '{end}'"
    "&$format=json&$select=Data,DataReferencia,Mediana&$top=200&$orderby=Data asc"
)

IPCA_TRIM_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
    "ExpectativasMercadoTrimestrais"
    "?$filter=Indicador eq 'IPCA' and baseCalculo eq 0"
    " and Data ge '{start}' and Data le '{end}'"
    "&$format=json&$select=Data,DataReferencia,Mediana&$top=500&$orderby=Data asc"
)

IPCA_12M_SUAV_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
    "ExpectativasMercadoInflacao12Meses"
    "?$filter=Indicador eq 'IPCA' and Suavizada eq 'S' and baseCalculo eq 0"
    " and Data ge '{start}' and Data le '{end}'"
    "&$format=json&$select=Data,Mediana&$top=100&$orderby=Data asc"
)


def quarter_from_date(d: date) -> str:
    """date(2026,6,17) → '2T/2026'  (formato Focus/Excel)."""
    return f"{(d.month - 1) // 3 + 1}T/{d.year}"


def model_q_to_focus_q(model_q: str) -> str:
    """'2026Q2' → '2T/2026'."""
    m = re.match(r"(\d{4})Q(\d)", model_q)
    return f"{m.group(2)}T/{m.group(1)}" if m else model_q


def _advance_focus_q(label: str, n: int) -> str:
    """Advance '2T/2026' por n trimestres."""
    m = re.match(r"(\d)T/(\d{4})", label)
    if not m:
        return label
    q = int(m.group(1)) - 1 + n
    y = int(m.group(2)) + q // 4
    return f"{q % 4 + 1}T/{y}"


def fetch_ipca_trimestral(focus_date: date) -> tuple:
    """
    Busca projeções trimestrais de IPCA do Focus (Olinda).
    Returns (date_used: str | None, {quarter_label: mediana}).
    quarter_label no formato '1T/2026'.
    """
    try:
        start = focus_date - timedelta(days=30)
        url = IPCA_TRIM_URL.format(
            start=start.strftime("%Y-%m-%d"),
            end=focus_date.strftime("%Y-%m-%d"),
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return None, {}
        max_date = max(r["Data"] for r in items)
        result = {}
        for r in items:
            if r["Data"] == max_date and r["Mediana"] is not None:
                dr = r["DataReferencia"].strip()
                # API retorna "4/2026" → normaliza para "4T/2026"
                if "/" in dr and "T" not in dr:
                    q, y = dr.split("/", 1)
                    dr = f"{q}T/{y}"
                result[dr] = float(r["Mediana"])
        return max_date, result
    except Exception:
        return None, {}


def fetch_ipca_12m_suav(focus_date: date) -> tuple:
    """
    Busca expectativa de IPCA acumulado 12m suavizado do Focus (Olinda).
    Returns (date_used: str | None, mediana: float | None).
    """
    try:
        start = focus_date - timedelta(days=30)
        url = IPCA_12M_SUAV_URL.format(
            start=start.strftime("%Y-%m-%d"),
            end=focus_date.strftime("%Y-%m-%d"),
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return None, None
        max_date = max(r["Data"] for r in items)
        vals = [float(r["Mediana"]) for r in items
                if r["Data"] == max_date and r["Mediana"] is not None]
        return max_date, (sum(vals) / len(vals) if vals else None)
    except Exception:
        return None, None


def fetch_ipca_anual(focus_date: date) -> tuple:
    """
    Busca expectativas anuais de IPCA do Focus (Olinda) — por ano-calendário.
    Returns (date_used: str | None, {'2026': 5.0, '2027': 4.0, ...}).
    """
    try:
        start = focus_date - timedelta(days=30)
        url = IPCA_ANUAL_URL.format(
            start=start.strftime("%Y-%m-%d"),
            end=focus_date.strftime("%Y-%m-%d"),
        )
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return None, {}
        max_date = max(r["Data"] for r in items)
        result = {}
        for r in items:
            if r["Data"] == max_date and r["Mediana"] is not None:
                # DataReferencia may be "2026" or "2026-12-31" — extract year
                dr = str(r["DataReferencia"])
                year_str = dr[:4]
                result[year_str] = float(r["Mediana"])
        return max_date, result
    except Exception:
        return None, {}


def build_expec_curve(
    quarterly: dict,
    suav_12m: Optional[float],
    start_quarter: str,
    n_periods: int,
    anual: Optional[dict] = None,
) -> list:
    """
    Constrói a curva de expectativas de inflação acumulada em 4 trimestres.

    Replica a lógica da aba 'expec' do simula_copom.xlsx:
      - curve[0]: suav_12m (infl. 12m suavizada — trimestre corrente)
      - curve[t] (t≥1): 100*(PROD(Q_{t+1}..Q_{t+4})/100+1) - 1
        onde Q_k = quarterly[start_quarter + k trimestres]
      - Para t onde falta dado trimestral: interpolação linear entre o último
        ponto composto e os âncoras anuais Focus (Q4 de cada ano-calendário).

    Parameters
    ----------
    quarterly : dict {quarter_label: mediana}  ex: {'1T/2026': 1.35, ...}
    suav_12m  : float  (expectativa 12m suavizada)
    start_quarter : str  no formato '2T/2026' (trimestre da reunião)
    n_periods : int  número de períodos do modelo a preencher
    anual : dict {year_str: mediana}  ex: {'2026': 5.0, '2027': 4.0}

    Returns
    -------
    list de n_periods floats (None onde não há cobertura).
    """
    curve: list = [None] * n_periods

    # Passo 1: preenche com compound trimestral onde possível
    if suav_12m is not None:
        curve[0] = suav_12m
    for t in range(1, n_periods):
        # 12m forward compound a partir de t:
        # t=1 (2026Q3) → advance(start,2..5) = 2026Q4, 2027Q1, 2027Q2, 2027Q3
        qs = [_advance_focus_q(start_quarter, t + j) for j in range(1, 5)]
        vals = [quarterly.get(q) for q in qs]
        if all(v is not None for v in vals):
            prod = 1.0
            for v in vals:
                prod *= 1.0 + v / 100.0
            curve[t] = round((prod - 1.0) * 100.0, 4)

    # Passo 2: interpola usando âncoras anuais para posições ainda None
    if anual:
        # Mapeia ano-calendário → índice do Q4 correspondente no horizonte
        annual_anchors: dict = {}
        for year_str, val in anual.items():
            target = f"4T/{year_str}"
            for t in range(n_periods):
                if _advance_focus_q(start_quarter, t) == target:
                    annual_anchors[t] = val
                    break

        # Preenche âncoras anuais nas posições ainda None
        for t_anc, v_anc in annual_anchors.items():
            if curve[t_anc] is None:
                curve[t_anc] = v_anc

        # Interpola linearmente entre pontos conhecidos consecutivos
        known_idx = [t for t in range(n_periods) if curve[t] is not None]
        for i in range(len(known_idx) - 1):
            t0, t1 = known_idx[i], known_idx[i + 1]
            if t1 - t0 <= 1:
                continue
            v0, v1 = curve[t0], curve[t1]
            for t in range(t0 + 1, t1):
                curve[t] = round(v0 + (v1 - v0) * (t - t0) / (t1 - t0), 4)

    return curve


def compute_expec_delta(curr_curve: list, prev_curve: list) -> list:
    """
    Delta entre duas curvas de expectativas (curr - prev).
    Onde prev é None (sem cobertura) → delta = 0.
    """
    return [
        round(c - p, 4) if (c is not None and p is not None) else 0.0
        for c, p in zip(curr_curve, prev_curve)
    ]


def compute_selic_quarterly_delta(
    curr_by_meeting: dict,
    prev_by_meeting: dict,
    label_map: Optional[dict],
    start_quarter: str,
    n_quarters: int,
) -> list:
    """
    Calcula o delta trimestral da Selic entre o Focus atual e o anterior.

    Para cada trimestre, faz a média das reuniões em comum entre curr e prev
    e retorna (avg_curr - avg_prev). Reuniões sem dado no prev → delta = 0.

    Returns lista de floats com comprimento n_quarters.
    """
    m = re.match(r"Q(\d)/(\d{4})", start_quarter)
    if not m:
        return [0.0] * n_quarters
    q0, y0 = int(m.group(1)) - 1, int(m.group(2))  # 0-indexed quarter

    quarter_to_labels: dict = {}
    for label in curr_by_meeting:
        qk = selic_label_to_quarter(label, label_map)
        if qk:
            quarter_to_labels.setdefault(qk, []).append(label)

    result = []
    for i in range(n_quarters):
        total_q = q0 + i
        y = y0 + total_q // 4
        q = total_q % 4 + 1
        qkey = f"Q{q}/{y}"

        common = [
            l for l in quarter_to_labels.get(qkey, [])
            if l in prev_by_meeting
        ]
        if not common:
            result.append(0.0)
        else:
            curr_avg = sum(curr_by_meeting[l] for l in common) / len(common)
            prev_avg = sum(prev_by_meeting[l] for l in common) / len(common)
            result.append(round(curr_avg - prev_avg, 4))

    return result
