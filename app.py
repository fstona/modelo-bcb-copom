"""
Interface web para simulação do Modelo BCB (Relatório de Inflação 2024/Q2).

Requer apenas:
    pip install streamlit pandas numpy scipy openpyxl plotly

Executar:
    streamlit run app.py
"""

import io
import os
import re
import sys
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "modules"))

from engine import load_model, run_scenario
from calculos import build_results_table, build_comparison_table, export_excel
from baseline_io import load_meeting, list_meetings
from mercado_io import (
    load_copom_calendar, find_meeting_date, get_meeting_position,
    previous_meeting, calc_ptax_para_reuniao, calc_ptax_atual,
    meeting_name_from_date, round_to_5cents, ref_friday_for_meeting,
    fetch_selic_focus_on_date, get_selic_sgs432, build_meeting_label_map,
    selic_label_to_quarter, compute_selic_quarterly_delta,
    fetch_ipca_trimestral, fetch_ipca_12m_suav, fetch_ipca_anual,
    build_expec_curve, compute_expec_delta, model_q_to_focus_q,
)
from brent_io import (
    load_brent_curve, levels_to_eps_brent, quarter_labels as brent_quarter_labels,
    copom_cutoff as brent_copom_cutoff, file_hash as brent_file_hash,
    BRENT_DEFAULT_PATH,
)

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Modelo BCB — Simulação Copom",
    layout="wide",
    initial_sidebar_state="expanded",
)

MAT_FILE      = os.path.join(os.path.dirname(__file__), "data", "mAgregado2024q2_base_results.mat")
BASELINE_FILE = os.path.join(os.path.dirname(__file__), "data", "projecoes_copom.xlsx")
BRENT_FILE    = str(BRENT_DEFAULT_PATH)

OOMEGA_L = 1 - 0.259
N_CAMBIO_BASE = 16


@st.cache_resource
def get_model():
    return load_model(MAT_FILE)


_copom_calendar = load_copom_calendar()


@st.cache_data(ttl=3600)
def get_ptax_reuniao_atual(_cache_v=2):
    """PTAX para a reunião atual — cache 1 hora."""
    from datetime import date as _date
    return calc_ptax_atual(_date.today(), _copom_calendar)


@st.cache_data(ttl=86400)
def get_ptax_reuniao_anterior(prev_date_iso: str, _cache_v=2):
    """PTAX para uma reunião passada — cache 24 horas (dado histórico fixo)."""
    from datetime import date as _date
    return calc_ptax_para_reuniao(_date.fromisoformat(prev_date_iso))


@st.cache_data(ttl=3600)
def _cached_prev_selic_focus(focus_date_iso: str, _v=1):
    """Focus Selic na sexta-feira de referência da reunião anterior (cache 1h)."""
    return fetch_selic_focus_on_date(date.fromisoformat(focus_date_iso))


@st.cache_data(ttl=3600)
def _cached_curr_selic_focus(curr_date_iso: str, _v=1):
    """Focus Selic mais recente até hoje (cache 1h)."""
    return fetch_selic_focus_on_date(date.fromisoformat(curr_date_iso))


@st.cache_data(ttl=86400)
def _cached_realized_selic(meeting_date_iso: str):
    """Selic realizada D+1 após a reunião do Copom via SGS 432 (cache 24h)."""
    return get_selic_sgs432(date.fromisoformat(meeting_date_iso))


@st.cache_data(ttl=3600)
def _cached_prev_ipca_focus(focus_date_iso: str, _v=4):
    """Focus IPCA trimestral + 12m suav + anual na sexta de referência anterior (cache 1h)."""
    d = date.fromisoformat(focus_date_iso)
    _, quarterly = fetch_ipca_trimestral(d)
    _, suav = fetch_ipca_12m_suav(d)
    _, anual = fetch_ipca_anual(d)
    return quarterly, suav, anual


@st.cache_data(ttl=3600)
def _cached_curr_ipca_focus(curr_date_iso: str, _v=4):
    """Focus IPCA trimestral + 12m suav + anual mais recente até hoje (cache 1h)."""
    d = date.fromisoformat(curr_date_iso)
    _, quarterly = fetch_ipca_trimestral(d)
    _, suav = fetch_ipca_12m_suav(d)
    _, anual = fetch_ipca_anual(d)
    return quarterly, suav, anual


@st.cache_data
def _cached_brent_curve(file_hash: str, meeting_date_iso: str, cutoff_iso: str, file_bytes=None):
    """Curva de Brent BCB — cache por hash do arquivo + data da reunião."""
    from datetime import date as _date
    meeting = _date.fromisoformat(meeting_date_iso)
    cutoff = _date.fromisoformat(cutoff_iso)
    import io as _io
    path = _io.BytesIO(file_bytes) if file_bytes is not None else None
    return load_brent_curve(meeting, bloomberg_path=path, cutoff=cutoff)


model = get_model()


# ---------------------------------------------------------------------------
# Parsing do nome da reunião → (ano, trimestre) de início do horizonte
# ---------------------------------------------------------------------------

MESES_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4,
    "mai": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "out": 10, "nov": 11, "dez": 12,
}

def parse_copom_name(name: str):
    m = re.match(r"([A-Za-z]+)(\d{2,4})", name.strip())
    if not m:
        return None
    mes_str = m.group(1).lower()[:3]
    ano_str = m.group(2)
    if mes_str not in MESES_PT:
        return None
    mes = MESES_PT[mes_str]
    ano = int(ano_str) + (2000 if len(ano_str) == 2 else 0)
    return ano, (mes - 1) // 3 + 1


def build_quarters(start_year: int, start_q: int, n: int = 16) -> list:
    quarters, y, q = [], start_year, start_q
    for _ in range(n):
        quarters.append(f"{y}Q{q}")
        q += 1
        if q > 4:
            q, y = 1, y + 1
    return quarters


# ---------------------------------------------------------------------------
# Horizontes do Copom — lógica para 2ª reunião do trimestre
# ---------------------------------------------------------------------------

def get_copom_decision_horizons(start_year: int, start_q: int) -> list:
    """
    Retorna os horizontes (índice no vetor de trimestres, rótulo, código do trimestre)
    divulgados na tabela da decisão do Copom para a 2ª reunião do trimestre.

    Regra derivada das reuniões de 2025:
      - Sempre: Q4 do ano corrente  (índice = 4 - start_q)
      - Se start_q > 2: Q4 do próximo ano  (índice = 8 - start_q)
      - Sempre: horizonte relevante = +6 trimestres  (índice = 6)
      (para Q2 o relevante coincide com Q4/ano+1 — não duplica)
    """
    horizons = []

    # 1. Q4 do ano corrente
    idx_curr = 4 - start_q
    horizons.append({
        "index": idx_curr,
        "quarter": f"{start_year}Q4",
        "label": f"{start_year} (anual)",
    })

    # 2. Q4 do próximo ano — apenas para reuniões de Q3 e Q4
    if start_q > 2:
        idx_next = 8 - start_q
        horizons.append({
            "index": idx_next,
            "quarter": f"{start_year + 1}Q4",
            "label": f"{start_year + 1} (anual)",
        })

    # 3. Horizonte relevante: start_q + 6 trimestres (índice sempre = 6)
    rel_q0   = (start_q - 1) + 6
    rel_year = start_year + rel_q0 // 4
    rel_q    = rel_q0 % 4 + 1

    if not any(h["index"] == 6 for h in horizons):
        label = f"{rel_year} (anual)" if rel_q == 4 else f"{rel_year}Q{rel_q}"
        horizons.append({
            "index": 6,
            "quarter": f"{rel_year}Q{rel_q}",
            "label": label,
        })

    return sorted(horizons, key=lambda h: h["index"])


# ---------------------------------------------------------------------------
# Sidebar — Identificação e flags
# ---------------------------------------------------------------------------
st.sidebar.title("Modelo BCB — Copom")
st.sidebar.markdown("---")
st.sidebar.subheader("Identificação")

copom_name   = st.sidebar.text_input("Nome da reunião", value="Jun26",
                                      help="Ex: Jun26, Dez26, Jan27.")
nome_arquivo = st.sidebar.text_input("Arquivo de saída Excel", value="proj_Copom.xlsx")

parsed = parse_copom_name(copom_name)
if parsed:
    start_year, start_q = parsed
    quarters = build_quarters(start_year, start_q)
    st.sidebar.success(f"Horizonte: {quarters[0]} → {quarters[-1]}")
else:
    st.sidebar.warning("Nome não reconhecido — usando 2026Q2 como padrão.")
    start_year, start_q = 2026, 2
    quarters = build_quarters(start_year, start_q)

# Detecção automática de posição no trimestre e reunião anterior via calendário
_meeting_date = find_meeting_date(copom_name, _copom_calendar)
_prev_meeting_date = previous_meeting(_meeting_date, _copom_calendar) if _meeting_date else None
prev_meeting_name = meeting_name_from_date(_prev_meeting_date) if _prev_meeting_date else None

if _meeting_date:
    _pos = get_meeting_position(_meeting_date, _copom_calendar)
    segunda_reuniao = (_pos == 2)
    _pos_label = f"{_pos}ª reunião do trimestre"
    _prev_label = f" | Anterior: **{prev_meeting_name}**" if prev_meeting_name else ""
    st.sidebar.info(f"📅 {_meeting_date.strftime('%d/%m/%Y')} — **{_pos_label}**{_prev_label}")
else:
    segunda_reuniao = False
    st.sidebar.warning("Reunião não encontrada no calendário — assumindo 1ª do trimestre.")

alternativo  = st.sidebar.checkbox("Cenário alternativo", value=False)
dark_mode    = st.sidebar.checkbox("Modo escuro", value=False)
peso_adm     = st.sidebar.number_input("Peso Adm (cálculo indireto)",
                                        value=0.25, min_value=0.0, max_value=1.0, step=0.01)

st.sidebar.markdown("---")
copom_horizons = get_copom_decision_horizons(start_year, start_q)
if segunda_reuniao:
    labels = " | ".join(h["label"] for h in copom_horizons)
    st.sidebar.info(f"Horizontes da decisão:\n{labels}")

# Carrega projeções do Copom anterior — serve como baseline RPM e como linha "Anterior"
# na tabela comparativa. Nome da aba = nome da reunião que publicou as projeções.
curr_proj = None
if prev_meeting_name and os.path.exists(BASELINE_FILE):
    curr_proj = load_meeting(BASELINE_FILE, prev_meeting_name)
    if curr_proj:
        st.sidebar.success(f"Baseline de **{prev_meeting_name}** carregado do arquivo.")
    else:
        st.sidebar.warning(f"**{prev_meeting_name}** não encontrado em projecoes_copom.xlsx.")
elif not prev_meeting_name:
    st.sidebar.warning("Reunião anterior não identificada — adicione ao calendário.")

# "Anterior" na tabela comparativa = mesmas projeções do baseline
prev_proj = curr_proj

# Projeções publicadas pelo BCB para a reunião atual — usadas como coluna de comparação ex-post
bcb_curr_proj = None
if os.path.exists(BASELINE_FILE):
    bcb_curr_proj = load_meeting(BASELINE_FILE, copom_name)
    if bcb_curr_proj:
        st.sidebar.success(f"Projeções BCB de **{copom_name}** disponíveis — coluna de comparação ativada.")

# ---------------------------------------------------------------------------
# Abas principais
# ---------------------------------------------------------------------------
st.title(f"Simulação Copom — {copom_name}")

tab_targets, tab_choques, tab_rpm, tab_resultados = st.tabs(
    ["Targets (Selic / Expec / Câmbio)", "Choques diretos", "Baseline", "Resultados"]
)

# ============================================================
# ABA 1 — Targets
# ============================================================
with tab_targets:

    st.subheader("Trajetória da Selic (% a.a.) — por reunião do Copom")
    st.caption(
        "Insira a Selic esperada em cada reunião. "
        "O modelo usará o delta trimestral vs Focus da reunião anterior."
    )

    # --- Focus data (needed before n_selic to compute max quarters) ---
    _today = date.today()
    # Para reuniões passadas, usa a sexta de referência daquela reunião como
    # data "atual" nos Focus — evita trazer dados de hoje para uma reunião histórica.
    _curr_ref_date = (
        ref_friday_for_meeting(_meeting_date)
        if _meeting_date and _meeting_date < _today
        else _today
    )
    _prev_focus_date = ref_friday_for_meeting(_prev_meeting_date) if _prev_meeting_date else None
    if _prev_focus_date:
        _prev_focus_date_used, _prev_focus_dict = _cached_prev_selic_focus(_prev_focus_date.isoformat())
    else:
        _prev_focus_date_used, _prev_focus_dict = None, {}
    _curr_focus_date_used, _curr_focus_dict = _cached_curr_selic_focus(_curr_ref_date.isoformat())

    # --- Status bar de dados automáticos ---
    _sb_ref   = _curr_ref_date.strftime("%d/%m/%Y")
    _sb_selic = str(_curr_focus_date_used) if _curr_focus_date_used else "⚠️ indisponível"
    _sb_hist  = "(dado histórico — referência da reunião)" if (_meeting_date and _meeting_date < _today) else "(hoje)"
    st.info(
        f"📡 **Dados automáticos** — Referência: **{_sb_ref}** {_sb_hist} · "
        f"Focus Selic: **{_sb_selic}** · Focus IPCA: mesma data · PTAX: ver câmbio abaixo"
    )

    # --- Build quarter → [labels] mapping (needed before n_selic) ---
    _label_map = build_meeting_label_map(_copom_calendar)

    def _q_str_to_key(qs: str) -> str:
        """'2026Q2' → 'Q2/2026'"""
        _m = re.match(r"(\d{4})Q(\d)", qs)
        return f"Q{_m.group(2)}/{_m.group(1)}" if _m else qs

    def _advance_qkey(qkey: str, n: int) -> str:
        """Advance 'Q2/2026' by n quarters."""
        _m = re.match(r"Q(\d)/(\d{4})", qkey)
        if not _m:
            return qkey
        _q = int(_m.group(1)) - 1 + n
        _y = int(_m.group(2)) + _q // 4
        return f"Q{_q % 4 + 1}/{_y}"

    _start_qkey = _q_str_to_key(quarters[0])

    # Union of labels from Focus + local calendar, grouped by quarter
    _all_labels = set(_curr_focus_dict.keys()) | set(_label_map.keys())
    _quarter_labels: dict = {}
    for _lbl in _all_labels:
        _qk = selic_label_to_quarter(_lbl, _label_map)
        if _qk:
            _quarter_labels.setdefault(_qk, []).append(_lbl)
    for _qk in _quarter_labels:
        _quarter_labels[_qk].sort(
            key=lambda l: _label_map[l]["date"] if l in _label_map else date(9999, 1, 1)
        )

    # Max quarters where delta can be computed (last quarter with prev Focus coverage)
    _max_selic_q = 0
    for _qi_max in range(16):
        _qkey_max = _advance_qkey(_start_qkey, _qi_max)
        _lbls_max = _quarter_labels.get(_qkey_max, [])
        if any(l in (_prev_focus_dict or {}) for l in _lbls_max):
            _max_selic_q = _qi_max + 1
    _max_selic_q = max(_max_selic_q, 1)

    n_selic = st.number_input(
        "Nº de trimestres a ancorar (Selic)", 1, _max_selic_q, min(7, _max_selic_q)
    )

    _fc1, _fc2 = st.columns(2)
    _fc1.caption(f"Focus anterior: {_prev_focus_date_used or '⚠️ indisponível'}")
    _fc2.caption(f"Focus atual: {_curr_focus_date_used or '⚠️ indisponível'}")
    if not _curr_focus_date_used:
        st.warning("Focus atual indisponível — insira os valores manualmente.")

    # --- Meeting-by-meeting inputs ---
    _user_selic: dict = {}

    for _qi in range(int(n_selic)):
        _qkey = _advance_qkey(_start_qkey, _qi)
        _labels = _quarter_labels.get(_qkey, [])

        st.markdown(f"**{_qkey}**")
        if not _labels:
            st.caption("Sem reuniões disponíveis no calendário — delta = 0.")
            continue

        _cols = st.columns(min(len(_labels), 4))
        for _ci, _lbl in enumerate(_labels):
            _info = _label_map.get(_lbl, {})
            _mdate = _info.get("date")
            _is_realized = bool(_mdate and _mdate < _today and _qkey == _start_qkey)

            if _is_realized:
                _val = _cached_realized_selic(_mdate.isoformat())
                _disp = float(_val) if _val is not None else 0.0
                _cols[_ci].number_input(
                    f"{_lbl} ✓ realizada", value=_disp, disabled=True,
                    step=0.25, format="%.2f", key=f"selic_m_{_lbl}",
                )
                _user_selic[_lbl] = _disp
            else:
                _default = float(_curr_focus_dict.get(_lbl, 0.0))
                _val = _cols[_ci].number_input(
                    _lbl, value=_default, step=0.25, format="%.2f",
                    key=f"selic_m_{_lbl}",
                )
                _user_selic[_lbl] = _val
                _prev_val = (_prev_focus_dict or {}).get(_lbl)
                if _prev_val is not None:
                    _cols[_ci].caption(f"anterior: {_prev_val:.2f} | Δ: {_val - _prev_val:+.2f}")
                else:
                    _cols[_ci].caption("sem dado anterior")

    selic_vals = compute_selic_quarterly_delta(
        curr_by_meeting=_user_selic,
        prev_by_meeting=_prev_focus_dict or {},
        label_map=_label_map,
        start_quarter=_start_qkey,
        n_quarters=int(n_selic),
    )
    # Trim trailing zeros — quarters beyond Focus coverage must be endogenous
    while selic_vals and selic_vals[-1] == 0.0:
        selic_vals.pop()

    st.divider()

    st.subheader("Expectativas Focus (desvios em pp)")
    st.caption(
        "Desvio da projeção de inflação vs Focus da reunião anterior. "
        "Pré-preenchido automaticamente com a variação entre os dois Focus."
    )

    # --- Fetch IPCA Focus data ---
    _start_quarter_focus = model_q_to_focus_q(quarters[0])  # e.g. "2T/2026"

    if _prev_focus_date:
        _prev_ipca_quarterly, _prev_ipca_suav, _prev_ipca_anual = _cached_prev_ipca_focus(_prev_focus_date.isoformat())
    else:
        _prev_ipca_quarterly, _prev_ipca_suav, _prev_ipca_anual = {}, None, {}

    _curr_ipca_quarterly, _curr_ipca_suav, _curr_ipca_anual = _cached_curr_ipca_focus(_curr_ref_date.isoformat())

    _prev_expec_curve = build_expec_curve(
        _prev_ipca_quarterly, _prev_ipca_suav, _start_quarter_focus, 16,
        anual=_prev_ipca_anual,
    )

    _curr_expec_curve = build_expec_curve(
        _curr_ipca_quarterly, _curr_ipca_suav, _start_quarter_focus, 16,
        anual=_curr_ipca_anual,
    )

    _auto_expec_delta = compute_expec_delta(_curr_expec_curve, _prev_expec_curve)

    # Default = nº de períodos com dado calculado, limitado a 6; teto sempre 16
    _n_expec_with_data = sum(1 for v in _curr_expec_curve if v is not None)
    _n_expec_default = min(6, max(1, _n_expec_with_data))

    _fe1, _fe2 = st.columns(2)
    _fe1.caption(f"Focus anterior (IPCA): {_prev_focus_date_used or '⚠️ indisponível'}")
    _fe2.caption(f"Focus atual (IPCA): {_curr_focus_date_used or '⚠️ indisponível'}")
    if _n_expec_with_data == 0:
        st.warning("Focus IPCA indisponível — valores zerados; edite manualmente.")

    with st.expander("🔍 Diagnóstico Focus IPCA (expandir para depuração)"):
        st.write(f"**start_quarter:** {_start_quarter_focus}")
        st.write(f"**suav_12m atual:** {_curr_ipca_suav} | **suav_12m anterior:** {_prev_ipca_suav}")
        st.write(f"**Trimestral atual ({len(_curr_ipca_quarterly)} chaves):** {sorted(_curr_ipca_quarterly.keys())}")
        st.write(f"**Anual atual ({len(_curr_ipca_anual)} anos):** {dict(sorted(_curr_ipca_anual.items()))}")
        _expec_tbl = pd.DataFrame({
            "Quarter": quarters[:16],
            "curr_curve": _curr_expec_curve,
            "prev_curve": _prev_expec_curve,
            "auto_delta": _auto_expec_delta,
        })
        st.dataframe(_expec_tbl.style.format({
            "curr_curve": lambda v: f"{v:.4f}" if v is not None else "—",
            "prev_curve": lambda v: f"{v:.4f}" if v is not None else "—",
            "auto_delta": "{:.4f}",
        }), use_container_width=True, height=300)

    n_expec = st.number_input(
        "Nº de períodos a ancorar (Focus)", 1, 16, _n_expec_default,
        key="n_expec_input",
    )

    expec_vals = []
    cols_e = st.columns(4)
    for t in range(int(n_expec)):
        _auto_delta = _auto_expec_delta[t] if t < len(_auto_expec_delta) else 0.0
        _curr_val = _curr_expec_curve[t] if t < len(_curr_expec_curve) else None
        _prev_val = _prev_expec_curve[t] if t < len(_prev_expec_curve) else None
        v = cols_e[t % 4].number_input(
            quarters[t], value=float(_auto_delta), step=0.01,
            format="%.4f", key=f"expec_{t}",
        )
        expec_vals.append(v)
        _cap_parts = []
        if _curr_val is not None:
            _cap_parts.append(f"atual: {_curr_val:.2f}")
        if _prev_val is not None:
            _cap_parts.append(f"anterior: {_prev_val:.2f}")
        if _cap_parts:
            cols_e[t % 4].caption(" | ".join(_cap_parts))

    # Trim trailing zeros
    while expec_vals and expec_vals[-1] == 0.0:
        expec_vals.pop()

    st.divider()

    st.subheader("Câmbio — nível spot (R$/USD)")
    st.info(
        "Cenário base: câmbio ancorado apenas no primeiro período. "
        "A partir do segundo, segue automaticamente a PPC "
        "(diferencial de metas Brasil–EUA = 0,25% a.t.)."
    )

    # --- Sugestão automática de PTAX ---
    def _ptax_caption(result: dict, label: str) -> tuple:
        """Retorna (default_value, caption_str) para um resultado PTAX."""
        if result.get("error") or result.get("sugestao") is None:
            return 5.20, f"⚠️ {label}: {result.get('error', 'indisponível')}"
        rows = result["rates"]
        d0, d1 = rows[0]["date"], rows[-1]["date"]
        parcial = " ⚠️ janela parcial — dados disponíveis até hoje" if result.get("partial") else ""
        cap = (
            f"{label}: média {len(rows)} dias úteis ({d0} → {d1}){parcial} = "
            f"{result['avg']:.4f} → **{result['sugestao']:.2f}**"
        )
        return result["sugestao"], cap

    # Câmbio da reunião atual (cambio_q1)
    ptax_atual = (
        get_ptax_reuniao_anterior(_meeting_date.isoformat())
        if _meeting_date and _meeting_date < _today
        else get_ptax_reuniao_atual()
    )
    _def_q1, _cap_q1 = _ptax_caption(ptax_atual, f"PTAX {quarters[0]}")
    _def_q1 = round_to_5cents(_def_q1)

    # Câmbio da reunião anterior (cambio_base)
    _prev_date = previous_meeting(_meeting_date, _copom_calendar) if _meeting_date else None
    if _prev_date:
        ptax_prev = get_ptax_reuniao_anterior(_prev_date.isoformat())
        _def_base, _cap_base = _ptax_caption(
            ptax_prev, f"PTAX reunião anterior ({_prev_date.strftime('%d/%m/%Y')})"
        )
    else:
        _def_base, _cap_base = 5.20, ""
    _def_base = round_to_5cents(_def_base)

    col_a, col_b, col_c = st.columns(3)
    cambio_base = col_a.number_input("Câmbio da última reunião", value=_def_base,
                                     step=0.05, format="%.2f", key="cambio_base")
    cambio_q1   = col_b.number_input(f"Câmbio {quarters[0]}", value=_def_q1,
                                     step=0.05, format="%.2f", key="cambio_q1")
    cambio_base = round_to_5cents(cambio_base)
    cambio_q1   = round_to_5cents(cambio_q1)
    var_cambio  = (cambio_q1 / cambio_base - 1) * 100
    col_c.metric("Variação implícita (%∆)", f"{var_cambio:.2f}%")
    if _cap_base:
        st.caption(_cap_base)
    if _cap_q1:
        st.caption(_cap_q1)

    target_cambio = [var_cambio] + [0.0] * (N_CAMBIO_BASE - 1)

    if alternativo:
        st.markdown("**Cenário alternativo — câmbio (períodos 2+)**")
        n_cambio_alt = int(st.number_input(
            "Nº de períodos explícitos além do Q1  (0 = câmbio endógeno)",
            0, N_CAMBIO_BASE - 1, 0, key="n_cambio_alt",
        ))
        if n_cambio_alt == 0:
            st.caption("Câmbio endógeno a partir do 2º período — evolui via UIP sem ancoragem.")
            target_cambio = [var_cambio]
        else:
            cols_c2 = st.columns(4)
            for t in range(1, 1 + n_cambio_alt):
                v = cols_c2[(t - 1) % 4].number_input(
                    f"%∆ {quarters[t]}", value=0.0, step=0.01,
                    format="%.4f", key=f"cambio_alt_{t}",
                )
                target_cambio[t] = v
            target_cambio = target_cambio[:1 + n_cambio_alt]

# ============================================================
# ABA 2 — Choques diretos
# ============================================================
with tab_choques:
    st.subheader("Choques diretos")

    st.markdown("**IPCA Monitorados (`eps_monit`)**")
    st.caption(
        "Use quando o BCB revisou a trajetória de preços administrados entre reuniões "
        "(energia, combustíveis, tarifas). Fonte: RPM/ata do Copom. "
        "Inserir em **pp de IPCA acumulado no trimestre** — o app aplica o fator "
        f"1/(1−ωL) = **{1/(1-OOMEGA_L):.3f}** internamente para converter para o choque do modelo."
    )
    n_monit = st.number_input("Nº de períodos (eps_monit)", 0, 16, 1, key="n_monit")
    monit_vals = []
    cols_m = st.columns(4)
    for t in range(n_monit):
        v = cols_m[t % 4].number_input(quarters[t], value=0.0, step=0.01,
                                        format="%.4f", key=f"monit_{t}")
        monit_vals.append(v / (1 - OOMEGA_L))

    st.divider()
    st.markdown("**IPCA Livres (`eps_piL`)**")
    st.caption(
        "Pressão adicional de preços livres não capturada pela revisão de expectativas Focus. "
        "Raramente necessário: o choque de expectativas (`eps_ei`) já cobre a maior parte da "
        "revisão de livres. Use apenas quando houver surpresa de curto prazo isolada "
        "(ex.: choque de alimentos). "
        f"Inserir em **pp de IPCA livres** — fator interno: 1/ωL = **{1/OOMEGA_L:.3f}**."
    )
    n_pil = st.number_input("Nº de períodos (eps_piL)", 0, 16, 1, key="n_pil")
    pil_vals = []
    cols_p = st.columns(4)
    for t in range(n_pil):
        v = cols_p[t % 4].number_input(quarters[t], value=0.0, step=0.01,
                                        format="%.4f", key=f"pil_{t}")
        pil_vals.append(v / OOMEGA_L)

    st.divider()
    st.markdown("**Brent** — nível por trimestre (USD)")

    # --- Uploader Bloomberg ---
    _brent_upload = st.file_uploader(
        "Atualizar Brent_full.xlsx (Bloomberg) — opcional; se vazio usa o arquivo do repo",
        type=["xlsx"], key="brent_uploader",
    )
    _brent_bytes = _brent_upload.read() if _brent_upload is not None else None
    _brent_hash  = brent_file_hash(_brent_bytes) if _brent_bytes else brent_file_hash(BRENT_FILE)

    # --- Calcula curva BCB atual ---
    _brent_meeting_date = find_meeting_date(copom_name, _copom_calendar) or date.today()
    _brent_cutoff       = brent_copom_cutoff(_brent_meeting_date)
    _brent_levels_auto, _brent_q_prev = _cached_brent_curve(
        _brent_hash, _brent_meeting_date.isoformat(), _brent_cutoff.isoformat(),
        file_bytes=_brent_bytes,
    )
    _brent_eps_auto = levels_to_eps_brent(_brent_levels_auto, _brent_q_prev)
    _brent_labels   = brent_quarter_labels(_brent_meeting_date, len(_brent_levels_auto))

    # --- Calcula curva BCB da reunião anterior ---
    _brent_prev_levels, _brent_prev_labels = [], []
    if _prev_meeting_date:
        try:
            _brent_prev_cutoff = brent_copom_cutoff(_prev_meeting_date)
            _brent_prev_levels_raw, _brent_prev_q_prev = _cached_brent_curve(
                _brent_hash, _prev_meeting_date.isoformat(),
                _brent_prev_cutoff.isoformat(), file_bytes=_brent_bytes,
            )
            _brent_prev_labels = brent_quarter_labels(_prev_meeting_date,
                                                       len(_brent_prev_levels_raw))
            _brent_prev_levels = _brent_prev_levels_raw
        except Exception:
            _brent_prev_levels, _brent_prev_labels = [], []

    # Mapa trimestre → nível da reunião anterior (para comparar o mesmo trimestre entre reuniões)
    _brent_prev_level_map = dict(zip(_brent_prev_labels, _brent_prev_levels)) if _brent_prev_levels else {}

    # Nº de períodos padrão = até onde há variação real (strip de 6 meses = ~2 trimestres além do corrente)
    _brent_n_default = min(3, len(_brent_levels_auto))
    n_brent = st.number_input("Nº de trimestres", 0, len(_brent_levels_auto),
                               _brent_n_default, key="n_brent")

    st.caption(f"Cutoff BCB: {_brent_cutoff.strftime('%d/%m/%Y')}")

    # --- Inputs de nível USD (pré-preenchidos, editáveis) ---
    # eps_brent = primeira diferença das revisões entre reuniões (captura nível + inclinação da curva)
    brent_input_levels = []
    brent_revisions = []
    cols_b = st.columns(4)
    for t in range(int(n_brent)):
        _auto_lvl    = _brent_levels_auto[t] if t < len(_brent_levels_auto) else 0.0
        _lbl         = _brent_labels[t] if t < len(_brent_labels) else quarters[t]
        _prev_same_q = _brent_prev_level_map.get(_lbl)
        v = cols_b[t % 4].number_input(
            _lbl, value=float(_auto_lvl), step=0.5, format="%.2f", key=f"brent_{t}"
        )
        if _prev_same_q:
            _rev_pct = (v / _prev_same_q - 1) * 100
            cols_b[t % 4].caption(
                f"{prev_meeting_name or 'Anterior'}: {_prev_same_q:.2f} USD | Δ: {_rev_pct:+.2f}%"
            )
            brent_revisions.append(round(_rev_pct, 4))
        else:
            cols_b[t % 4].caption("Sem dado anterior")
            brent_revisions.append(0.0)
        brent_input_levels.append(v)

    # eps_brent = primeira diferença das revisões entre reuniões:
    #   período 0: revisão direta
    #   período t: revisão[t] − revisão[t−1]  (desconta a velocidade de queda já aplicada)
    brent_vals = [
        round(brent_revisions[t] - (brent_revisions[t - 1] if t > 0 else 0.0), 4)
        for t in range(len(brent_revisions))
    ]

    # Remove trailing zeros
    while brent_vals and abs(brent_vals[-1]) < 1e-6:
        brent_vals.pop()

    st.divider()
    st.markdown("**Hiato do produto (`eps_h2008`)**")
    st.caption(
        "Choque persistente sobre o hiato do produto. Use em cenários de recessão ou "
        "superaquecimento não capturados pelo canal de Selic/câmbio — por exemplo, "
        "revisão de crescimento do PIB potencial. Inserir em **pp do hiato**."
    )
    n_hiato = st.number_input("Nº de períodos (eps_h2008)", 0, 16, 0, key="n_hiato")
    hiato_vals = []
    cols_h = st.columns(4)
    for t in range(n_hiato):
        v = cols_h[t % 4].number_input(quarters[t], value=0.0, step=0.05,
                                        format="%.4f", key=f"hiato_{t}")
        hiato_vals.append(v)


# ============================================================
# ABA 3 — Baseline (RPM ou decisão do Copom)
# ============================================================
with tab_rpm:

    def _to_series(raw, n=16):
        """Converte lista do arquivo (pode ter None) para lista com np.nan."""
        out = list(raw) + [np.nan] * n
        return [np.nan if v is None else float(v) for v in out[:n]]

    st.caption(
        "Aba somente leitura. Atualize as projeções diretamente em **projecoes_copom.xlsx** "
        "e recarregue o nome da reunião na barra lateral."
    )

    # ── Baseline (projeções da reunião anterior, usadas como RPM) ────────────
    if curr_proj:
        st.subheader(f"Baseline — Projeções de {prev_meeting_name} (referência para {copom_name})")
        _curr_raw = {
            "Período": (curr_proj.get("quarters") or quarters)[:16],
            "IPCA":    _to_series(curr_proj.get("IPCA",   [])),
            "Livres":  _to_series(curr_proj.get("Livres", [])),
            "Adm":     _to_series(curr_proj.get("Adm",    [])),
        }
        _curr_df = pd.DataFrame(_curr_raw).iloc[:16]
        _curr_df_show = _curr_df.dropna(subset=["IPCA", "Livres", "Adm"], how="all")
        st.dataframe(
            _curr_df_show.style.format(
                {"IPCA": "{:.4f}", "Livres": "{:.4f}", "Adm": "{:.4f}"}, na_rep="—"
            ),
            use_container_width=True, height=min(60 + len(_curr_df_show) * 35, 460),
        )

        # Extrai vetores para a simulação
        if not segunda_reuniao:
            # 1ª reunião: série contígua (apenas linhas com dado)
            rpm_ipca   = [v for v in _curr_raw["IPCA"]   if not np.isnan(v)]
            rpm_livres = [v for v in _curr_raw["Livres"] if not np.isnan(v)]
            rpm_adm    = [v for v in _curr_raw["Adm"]    if not np.isnan(v)]
        else:
            # 2ª reunião: sparse (NaN nas posições sem dado)
            rpm_ipca   = _curr_raw["IPCA"]
            rpm_livres = _curr_raw["Livres"]
            rpm_adm    = _curr_raw["Adm"]
    else:
        _missing = prev_meeting_name or copom_name
        st.warning(
            f"Nenhuma projeção encontrada para **{_missing}** em `projecoes_copom.xlsx`. "
            "Adicione uma aba com esse nome no arquivo e recarregue."
        )
        rpm_ipca = rpm_livres = rpm_adm = []

    prev_extra = None

# ============================================================
# ABA 4 — Resultados
# ============================================================
with tab_resultados:
    st.subheader("Simulação")

    if st.button("▶  Rodar simulação", type="primary", use_container_width=True):

        direct_shocks = {}
        if monit_vals: direct_shocks["eps_monit"] = monit_vals
        if pil_vals:   direct_shocks["eps_piL"]   = pil_vals
        if brent_vals: direct_shocks["eps_brent"] = brent_vals
        if hiato_vals: direct_shocks["eps_h2008"] = hiato_vals


        with st.spinner("Calculando..."):
            result = run_scenario(
                model=model,
                target_selic=selic_vals,
                target_expec=expec_vals,
                target_cambio_pct=target_cambio,
                direct_shocks=direct_shocks or None,
            )

        df = build_results_table(
            sim_output=result["output"],
            rpm_ipca=rpm_ipca,
            rpm_livres=rpm_livres,
            rpm_adm=rpm_adm,
            quarters=quarters,
            peso_adm=peso_adm,
            sparse_rpm=segunda_reuniao,
        )

        st.success(f"Convergiu em {result['n_iter']} iterações.")
        st.session_state.update({
            "df": df, "result": result, "quarters": quarters,
            "segunda_reuniao": segunda_reuniao,
            "copom_horizons": copom_horizons,
            "curr_name": copom_name,
            "prev_proj": prev_proj,
            "prev_name": prev_meeting_name or "Anterior",
            "prev_extra": prev_extra,
            "bcb_curr_proj": bcb_curr_proj,
            "selic_delta": selic_vals,
            "expec_curr_curve": _curr_expec_curve,
            "expec_prev_curve": _prev_expec_curve,
            "brent_levels": brent_input_levels,
            "brent_q_prev": _brent_q_prev,
            "brent_labels": _brent_labels,
            "brent_eps": brent_vals,
            "brent_prev_levels": _brent_prev_levels,
            "brent_prev_labels": _brent_prev_labels,
            "cambio_q1_stored": cambio_q1,
            "n_cambio_targets_stored": len(target_cambio),
            "alternativo_stored": alternativo,
            "n_expec_stored": int(n_expec),
            "dark_mode_stored": dark_mode,
        })

    if "df" not in st.session_state:
        st.info("Configure os parâmetros nas abas anteriores e clique em **Rodar simulação**.")
        st.stop()

    df              = st.session_state["df"]
    result          = st.session_state["result"]
    quarters        = st.session_state["quarters"]
    segunda_reuniao = st.session_state["segunda_reuniao"]
    copom_horizons  = st.session_state["copom_horizons"]
    curr_name_ss    = st.session_state.get("curr_name", copom_name)
    prev_proj_ss    = st.session_state.get("prev_proj", None)
    prev_name_ss    = st.session_state.get("prev_name", "Anterior")
    prev_extra_ss   = st.session_state.get("prev_extra", None)
    bcb_curr_proj_ss = st.session_state.get("bcb_curr_proj", None)

    # --- Tabela comparativa (sempre exibida) ---
    comp_table, hr_col = build_comparison_table(
        df=df,
        copom_horizons=copom_horizons,
        quarters=quarters,
        prev_name=prev_name_ss,
        curr_name=curr_name_ss,
        prev_proj=prev_proj_ss,
        prev_extra=prev_extra_ss,
        bcb_proj=bcb_curr_proj_ss,
        bcb_name=f"BCB pub. ({curr_name_ss})",
    )

    st.markdown("#### Projeções acumuladas — comparativo entre reuniões")
    if hr_col:
        st.caption(f"HR = Horizonte Relevante | coluna **{hr_col}**")
    if bcb_curr_proj_ss:
        st.caption(f"Linha **BCB pub. ({curr_name_ss})** = projeção publicada pelo BCB para esta reunião.")

    _dark_mode_ss = st.session_state.get("dark_mode_stored", False)
    _hr_style = (
        "font-weight: bold; background-color: #2D6A4F; color: #FFFFFF"
        if _dark_mode_ss else
        "font-weight: bold; background-color: #D4EFE2; color: #1a1a1a"
    )

    def _style_comp(df_s):
        styles = pd.DataFrame("", index=df_s.index, columns=df_s.columns)
        for col in df_s.columns:
            if col == hr_col:
                styles[col] = _hr_style
        for idx in df_s.index:
            if isinstance(idx, tuple) and "BCB pub." in str(idx[1]):
                styles.loc[idx] = styles.loc[idx].apply(
                    lambda v: v + "; background-color: #007A3D; color: #FFFFFF"
                )
        return styles

    st.dataframe(
        comp_table.style.format("{:.1f}", na_rep="—").apply(_style_comp, axis=None),
        use_container_width=True,
    )

    st.divider()

    # --- Selic (sempre exibida) ---
    st.markdown("#### Selic — trajetória simulada (pp vs steady state)")
    _selic_delta_ss = st.session_state.get("selic_delta", [])
    _selic_target = (_selic_delta_ss + [None] * len(quarters))[:len(quarters)]
    fig_selic = go.Figure()
    fig_selic.add_trace(go.Bar(
        x=quarters, y=df["it"].tolist(),
        name="Selic simulada", marker_color="#00A859",
    ))
    fig_selic.add_trace(go.Scatter(
        x=quarters, y=_selic_target,
        name="Δ target (Focus)", mode="markers",
        marker=dict(color="#9CA3AF" if dark_mode else "#4B5563", size=8, symbol="diamond"),
        connectgaps=False,
    ))
    fig_selic.update_layout(yaxis_title="pp vs steady state", height=300,
                            legend=dict(orientation="h", y=1.02, yanchor="bottom"))
    st.plotly_chart(fig_selic, use_container_width=True)

    # --- Selic nível % a.a. por trimestre (anchored + endógena) ---
    st.markdown("#### Selic — nível por trimestre (% a.a.)")

    # Prev Focus quarterly averages for all 16 quarters, using same quarters[] labels
    _prev_q_full = []
    for _qs16 in quarters:
        _qkey16 = _q_str_to_key(_qs16)          # "2026Q2" → "Q2/2026"
        _lbls16 = _quarter_labels.get(_qkey16, [])
        _pv16 = [_prev_focus_dict[l] for l in _lbls16 if l in (_prev_focus_dict or {})]
        _prev_q_full.append(sum(_pv16) / len(_pv16) if _pv16 else None)

    # Limit horizon to last quarter with prev Focus coverage
    _last_prev_idx = next(
        (i for i in range(len(_prev_q_full) - 1, -1, -1) if _prev_q_full[i] is not None), -1
    )
    _horizon = _last_prev_idx + 1
    _qs_plot = quarters[:_horizon]

    # Build series up to horizon
    _n_anch = int(n_selic)
    _curr_selic_plot, _prev_selic_plot = [], []
    for i in range(_horizon):
        _qkey_i = _q_str_to_key(quarters[i])
        _labels_i = _quarter_labels.get(_qkey_i, [])
        # Current scenario
        if i < _n_anch:
            _cv = [_user_selic[l] for l in _labels_i if l in _user_selic]
            _curr_selic_plot.append(sum(_cv) / len(_cv) if _cv else None)
        else:
            _pbase = _prev_q_full[i]
            _delta = float(df["it"].iloc[i]) if i < len(df) else None
            _curr_selic_plot.append(
                round(_pbase + _delta, 2) if (_pbase is not None and _delta is not None) else None
            )
        # Prev Focus
        _prev_selic_plot.append(_prev_q_full[i])

    fig_selic_lvl = go.Figure()
    fig_selic_lvl.add_trace(go.Scatter(
        x=_qs_plot, y=_curr_selic_plot,
        name="Cenário atual", mode="lines+markers",
        line=dict(color="#00A859", width=2),
    ))
    fig_selic_lvl.add_trace(go.Scatter(
        x=_qs_plot, y=_prev_selic_plot,
        name=f"Focus {prev_name_ss}", mode="lines+markers",
        line=dict(color="#9CA3AF", dash="dash", width=2),
        marker=dict(symbol="circle-open"),
        connectgaps=False,
    ))
    if 0 < _n_anch < _horizon:
        fig_selic_lvl.add_shape(
            type="line",
            x0=quarters[_n_anch - 1], x1=quarters[_n_anch - 1],
            y0=0, y1=1, yref="paper",
            line=dict(dash="dot", color="#9CA3AF", width=1),
        )
    fig_selic_lvl.update_layout(
        yaxis_title="% a.a.", height=320,
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
    )
    st.plotly_chart(fig_selic_lvl, use_container_width=True)

    # --- Câmbio nível ---
    st.divider()
    _cambio_q1_ss          = st.session_state.get("cambio_q1_stored", None)
    _n_cambio_targets_ss   = st.session_state.get("n_cambio_targets_stored", N_CAMBIO_BASE)
    _alternativo_ss        = st.session_state.get("alternativo_stored", False)

    if _cambio_q1_ss is not None:
        _E_PPC = 0.25  # depreciação trimestral de steady state (% a.t.)
        _cambio_lvl = [float(_cambio_q1_ss)]
        for _t in range(1, _horizon):
            _irf_t = float(df["delta_e"].iloc[_t]) if _t < len(df) else 0.0
            _cambio_lvl.append(round(_cambio_lvl[-1] * (1 + (_E_PPC + _irf_t) / 100), 4))

        _ppc_ref = [round(float(_cambio_q1_ss) * (1 + _E_PPC / 100) ** _t, 4)
                    for _t in range(_horizon)]

        st.markdown("#### Câmbio — nível simulado (R$/USD)")
        fig_cambio = go.Figure()
        fig_cambio.add_trace(go.Scatter(
            x=quarters[:_horizon], y=_cambio_lvl,
            name="Câmbio simulado", mode="lines+markers",
            line=dict(color="#00A859", width=2),
        ))
        fig_cambio.add_trace(go.Scatter(
            x=quarters[:_horizon], y=_ppc_ref,
            name="PPC (0,25% a.t.)", mode="lines",
            line=dict(color="#9CA3AF", dash="dash", width=1.5),
        ))
        if _alternativo_ss and 0 < _n_cambio_targets_ss < _horizon:
            fig_cambio.add_shape(
                type="line",
                x0=quarters[_n_cambio_targets_ss - 1], x1=quarters[_n_cambio_targets_ss - 1],
                y0=0, y1=1, yref="paper",
                line=dict(dash="dot", color="#9CA3AF", width=1),
            )
        fig_cambio.update_layout(
            yaxis_title="R$/USD", height=300,
            legend=dict(orientation="h", y=1.02, yanchor="bottom"),
        )
        st.plotly_chart(fig_cambio, use_container_width=True)

    # --- Brent ---
    st.divider()
    _brent_levels_ss = st.session_state.get("brent_levels", [])
    _brent_q_prev_ss = st.session_state.get("brent_q_prev", None)
    _brent_labels_ss = st.session_state.get("brent_labels", [])
    _brent_eps_ss    = st.session_state.get("brent_eps", [])

    if _brent_levels_ss:
        # Limita ao mesmo horizonte da Selic (último trimestre com cobertura do Focus anterior)
        _all_brent_labels = (
            _brent_labels_ss[:len(_brent_levels_ss)]
            if _brent_labels_ss
            else quarters[:len(_brent_levels_ss)]
        )
        _brent_horizon = min(_horizon, len(_all_brent_labels))
        _brent_plot_labels = _all_brent_labels[:_brent_horizon]
        _brent_levels_plot = _brent_levels_ss[:_brent_horizon]

        _brent_prev_levels_ss = st.session_state.get("brent_prev_levels", [])
        _brent_prev_labels_ss = st.session_state.get("brent_prev_labels", [])

        st.markdown("#### Brent — nível (USD/barril)")
        fig_brent_lvl = go.Figure()
        fig_brent_lvl.add_trace(go.Scatter(
            x=_brent_plot_labels, y=_brent_levels_plot,
            name=f"Brent {curr_name_ss}", mode="lines+markers",
            line=dict(color="#00A859", width=2),
        ))
        if _brent_prev_levels_ss:
            _prev_all_labels = (
                _brent_prev_labels_ss[:len(_brent_prev_levels_ss)]
                if _brent_prev_labels_ss
                else quarters[:len(_brent_prev_levels_ss)]
            )
            _prev_horizon = min(_horizon, len(_prev_all_labels))
            fig_brent_lvl.add_trace(go.Scatter(
                x=_prev_all_labels[:_prev_horizon],
                y=_brent_prev_levels_ss[:_prev_horizon],
                name=f"Brent {prev_name_ss}", mode="lines+markers",
                line=dict(color="#9CA3AF", dash="dash", width=2),
                marker=dict(symbol="circle-open"),
            ))
        fig_brent_lvl.update_layout(
            yaxis_title="USD/barril", height=300,
            legend=dict(orientation="h", y=1.02, yanchor="bottom"),
        )
        st.plotly_chart(fig_brent_lvl, use_container_width=True)

        st.markdown("#### Brent — eps_brent (%∆ trimestral)")
        _eps_plot = (_brent_eps_ss + [None] * _brent_horizon)[:_brent_horizon]
        fig_brent_eps = go.Figure()
        fig_brent_eps.add_trace(go.Bar(
            x=_brent_plot_labels, y=_eps_plot,
            name="eps_brent", marker_color="#9CA3AF",
        ))
        fig_brent_eps.update_layout(
            yaxis_title="%∆ trimestral", height=280,
            legend=dict(orientation="h", y=1.02, yanchor="bottom"),
        )
        st.plotly_chart(fig_brent_eps, use_container_width=True)

    # --- Expectativas de inflação — comparativo entre reuniões ---
    st.divider()
    st.markdown("#### Expectativas de inflação 12m — comparativo entre reuniões")

    _expec_curr_ss = st.session_state.get("expec_curr_curve", [])
    _expec_prev_ss = st.session_state.get("expec_prev_curve", [])
    _n_anch_expec  = st.session_state.get("n_expec_stored", 0)

    # Limita ao último índice com dado em qualquer uma das duas curvas
    _expec_last = max(
        (i for i, v in enumerate(_expec_curr_ss) if v is not None), default=-1
    )
    _expec_last = max(
        _expec_last,
        max((i for i, v in enumerate(_expec_prev_ss) if v is not None), default=-1),
    )
    _n_expec_plot = _expec_last + 1
    _eq = quarters[:_n_expec_plot]

    # Constrói série atual: Focus quando ancorada, prev+delta do modelo quando endógena
    _expec_curr_plot, _expec_prev_plot = [], []
    for _i in range(_n_expec_plot):
        _pv = (_expec_prev_ss[_i]
               if _i < len(_expec_prev_ss) and _expec_prev_ss[_i] is not None
               else None)
        _expec_prev_plot.append(_pv)
        if _i < _n_anch_expec:
            _cv = (_expec_curr_ss[_i]
                   if _i < len(_expec_curr_ss) and _expec_curr_ss[_i] is not None
                   else None)
            _expec_curr_plot.append(_cv)
        else:
            _dv = float(df["expectativa"].iloc[_i]) if _i < len(df) else 0.0
            _expec_curr_plot.append(round(_pv + _dv, 4) if _pv is not None else None)

    fig_expec = go.Figure()
    fig_expec.add_trace(go.Scatter(
        x=_eq, y=_expec_curr_plot,
        name=curr_name_ss, mode="lines+markers",
        line=dict(color="#00A859", width=2),
        connectgaps=False,
    ))
    fig_expec.add_trace(go.Scatter(
        x=_eq, y=_expec_prev_plot,
        name=prev_name_ss, mode="lines+markers",
        line=dict(color="#9CA3AF", dash="dash", width=2),
        marker=dict(symbol="circle-open"),
        connectgaps=False,
    ))
    if 0 < _n_anch_expec < _n_expec_plot:
        fig_expec.add_shape(
            type="line",
            x0=quarters[_n_anch_expec - 1], x1=quarters[_n_anch_expec - 1],
            y0=0, y1=1, yref="paper",
            line=dict(dash="dot", color="#9CA3AF", width=1),
        )
    fig_expec.update_layout(
        yaxis_title="% acum. 12m (Focus)",
        height=320,
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
    )
    st.plotly_chart(fig_expec, use_container_width=True)

    # --- Gráfico de trajetória — apenas para 1ª reunião ---
    if not segunda_reuniao:
        df_proj = df.dropna(subset=["RPM_IPCA"])
        if not df_proj.empty:
            st.divider()
            st.markdown("#### Trajetória projetada vs Baseline RPM")
            CORES = {"IPCA": "#00A859", "Livres": "#33BB77", "Adm": "#E05252"}
            fig_traj = go.Figure()
            q_plot = df_proj["Período"].tolist()
            for var, cor in CORES.items():
                fig_traj.add_trace(go.Scatter(
                    x=q_plot, y=df_proj[f"RPM_{var}"].tolist(),
                    name=f"{var} — Baseline RPM",
                    line=dict(color=cor, dash="dash", width=1.5),
                    mode="lines+markers", marker=dict(symbol="circle-open"),
                ))
                fig_traj.add_trace(go.Scatter(
                    x=q_plot, y=df_proj[f"Proj_{var}"].tolist(),
                    name=f"{var} — Projeção atualizada",
                    line=dict(color=cor, dash="solid", width=2),
                    mode="lines+markers",
                ))
            fig_traj.update_layout(
                yaxis_title="% (acum. 4 trimestres)",
                height=450,
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_traj, use_container_width=True)

    # --- Desvios brutos e projeções detalhadas em expansor ---
    with st.expander("Desvios trimestrais e projeções detalhadas"):
        raw_cols = ["Período", "it", "expectativa", "delta_e", "ht",
                    "ICbr", "Brent", "IPCA", "Livres", "Adm"]
        st.markdown("**Desvios do steady state (colunas A–I)**")
        st.dataframe(
            df[raw_cols].style.format({c: "{:.4f}" for c in raw_cols[1:]}),
            use_container_width=True, height=320,
        )
        df_proj_exp = df.dropna(subset=["RPM_IPCA"])
        if not df_proj_exp.empty:
            proj_cols = ["Período", "RPM_IPCA", "IPCA_cum", "Proj_IPCA",
                         "RPM_Livres", "Livres_cum", "Proj_Livres",
                         "RPM_Adm", "Adm_cum", "Proj_Adm",
                         "Indireto", "Dif_vs_RPM"]
            st.markdown("**Projeções atualizadas (baseline + choque acumulado)**")
            _rename_map = {"RPM_IPCA": "Copom_IPCA", "RPM_Livres": "Copom_Livres", "RPM_Adm": "Copom_Adm"}
            _proj_display = df_proj_exp[proj_cols].rename(columns=_rename_map)
            _display_cols = [_rename_map.get(c, c) for c in proj_cols]
            st.dataframe(
                _proj_display.style.format({c: "{:.4f}" for c in _display_cols[1:]}),
                use_container_width=True, height=320,
            )

    st.divider()

    # --- Exportação ---
    st.markdown("#### Exportar para Excel")

    def _gerar_excel():
        buf = io.BytesIO()
        export_excel(df, buf, copom_name)
        buf.seek(0)
        return buf.read()

    st.download_button(
        label="⬇  Baixar Excel",
        data=_gerar_excel(),
        file_name=nome_arquivo,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
