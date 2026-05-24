"""
Motor de simulação do modelo BCB — tradução fiel do simult_() do Dynare 4.6.4.

Lê as matrizes de solução de mAgregado2024q2_base_results.mat e reproduz
a lógica dos runners runModelo24q2_*.m sem depender de Matlab ou Octave.
"""

import numpy as np
import scipy.io
from typing import Optional


# ---------------------------------------------------------------------------
# Carregamento das matrizes de solução
# ---------------------------------------------------------------------------

def load_model(mat_path: str) -> dict:
    """Carrega e organiza todas as matrizes necessárias do .mat do Dynare."""
    mat = scipy.io.loadmat(mat_path, squeeze_me=True)

    oo = mat["oo_"]
    dr = oo["dr"].item()
    M  = mat["M_"]

    ghx       = dr["ghx"].item().astype(float)
    ghu       = dr["ghu"].item().astype(float)
    ys        = dr["ys"].item().astype(float)
    order_var = dr["order_var"].item().astype(int)   # 1-indexed
    kstate    = dr["kstate"].item().astype(int)

    endo_names = list(M["endo_names"].item())
    exo_names  = list(M["exo_names"].item())
    max_lag    = int(M["maximum_lag"].item())
    endo_nbr   = int(M["endo_nbr"].item())
    irf_periods = 16   # igual a options_.irf no .mod

    # Replicar exatamente: k2 = dr.kstate(kstate(:,2)<=max_lag+1, [1 2])
    # kstate é 1-indexed em Matlab; Python usa 0-indexed
    mask      = kstate[:, 1] <= max_lag + 1          # coluna 2 do Matlab
    k2_rows   = kstate[mask][:, [0, 1]]              # colunas 1 e 2 do Matlab
    k2        = k2_rows[:, 0] + (max_lag + 1 - k2_rows[:, 1]) * endo_nbr  # 1-indexed

    # Índices 0-indexed para indexação NumPy
    k2_0       = k2 - 1                   # índices em order_var
    order_var_0 = order_var - 1           # linhas de y_ para todas as variáveis
    state_rows  = order_var_0[k2_0]       # linhas de y_ para variáveis de estado

    return {
        "ghx": ghx,
        "ghu": ghu,
        "ys": ys,
        "order_var_0": order_var_0,
        "state_rows": state_rows,
        "endo_names": endo_names,
        "exo_names": exo_names,
        "max_lag": max_lag,
        "endo_nbr": endo_nbr,
        "irf_periods": irf_periods,
    }


# ---------------------------------------------------------------------------
# simult_() — idêntico ao Dynare, order=1, partindo do steady state
# ---------------------------------------------------------------------------

def simult(model: dict, shock_matrix: np.ndarray) -> np.ndarray:
    """
    Simula o modelo a partir do steady state.

    Parameters
    ----------
    model : dict
        Saída de load_model().
    shock_matrix : np.ndarray
        Matriz T × nexo de choques (mesma convenção dos runners .m).

    Returns
    -------
    y_irf : np.ndarray
        Matriz endo_nbr × T de desvios do steady state.
    """
    ghx        = model["ghx"]
    ghu        = model["ghu"]
    ys         = model["ys"]
    order_var_0 = model["order_var_0"]
    state_rows  = model["state_rows"]
    max_lag     = model["max_lag"]
    endo_nbr    = model["endo_nbr"]

    T = shock_matrix.shape[0]

    # Dynare subtrai ys antes do loop e soma de volta no final
    # Partindo do steady state: y_[:, 0] = ys - ys = 0
    y_ = np.zeros((endo_nbr, T + max_lag))

    epsilon = ghu @ shock_matrix.T   # endo_nbr × T

    for t in range(1, T + max_lag):
        yhat = y_[state_rows, t - 1]
        y_[order_var_0, t] = ghx @ yhat + epsilon[:, t - 1]

    # Adiciona steady state (bsxfun(@plus, y_, dr.ys) do Dynare)
    y_levels = y_ + ys[:, None]

    # Desvios do steady state para os T períodos de simulação
    y_irf = y_levels[:, max_lag:] - ys[:, None]

    return y_irf


# ---------------------------------------------------------------------------
# Loop de convergência — replica os runners .m
# ---------------------------------------------------------------------------

def build_shock_matrix(
    model: dict,
    target_selic: list[float],
    target_expec: list[float],
    target_cambio: list[float],
    direct_shocks: Optional[dict] = None,
    max_iter: int = 10_000,
    tol_selic: float = 1e-6,
    tol_expec: float = 1e-4,
    tol_cambio: float = 1e-4,
) -> tuple[np.ndarray, int]:
    """
    Constrói a shock_matrix que faz as variáveis atingirem os targets.

    Parameters
    ----------
    target_selic : list[float]
        Desvios desejados de `it` (variação da Selic em pp).
    target_expec : list[float]
        Desvios desejados de `inflt_focus_t4` (expectativas Focus).
    target_cambio : list[float]
        Desvios desejados de `delta_e` (%∆ da taxa de câmbio).
    direct_shocks : dict, optional
        Choques diretos: {'eps_brent': [v1, v2, ...], 'eps_monit': [v1], ...}
        Cada choque é um vetor de até irf_periods valores (posições 1-indexed).
    max_iter : int
        Número máximo de iterações (padrão 10.000 como nos .m originais).

    Returns
    -------
    shock_matrix : np.ndarray  (irf_periods × nexo)
    n_iter : int               número de iterações até convergência
    """
    irf     = model["irf_periods"]
    nexo    = model["ghu"].shape[1]
    exo_names = model["exo_names"]
    endo_names = model["endo_names"]

    n_selic  = len(target_selic)
    n_expec  = len(target_expec)
    n_cambio = len(target_cambio)

    target_selic  = np.array(target_selic,  dtype=float)
    target_expec  = np.array(target_expec,  dtype=float)
    target_cambio = np.array(target_cambio, dtype=float)

    idx = {name: i for i, name in enumerate(exo_names)}
    var = {name: i for i, name in enumerate(endo_names)}

    shock_matrix = np.zeros((irf, nexo))

    # Choques diretos definidos pelo usuário
    if direct_shocks:
        for shock_name, values in direct_shocks.items():
            if shock_name not in idx:
                raise ValueError(f"Choque '{shock_name}' não encontrado no modelo.")
            col = idx[shock_name]
            for t, v in enumerate(values):
                if t < irf:
                    shock_matrix[t, col] = v

    i_it    = var["it"]
    i_expec = var["inflt_focus_t4"]
    i_cambio = var["delta_e"]

    i_eps_i   = idx["eps_i"]
    i_eps_ei  = idx["eps_ei"]
    i_eps_e   = idx["eps_e"]

    y_irf = simult(model, shock_matrix)

    diff_selic  = y_irf[i_it,     :n_selic]  - target_selic
    diff_expec  = y_irf[i_expec,  :n_expec]  - target_expec
    diff_cambio = y_irf[i_cambio, :n_cambio] - target_cambio

    diff_selic  = np.round(diff_selic,  6)
    diff_expec  = np.round(diff_expec,  4)
    diff_cambio = np.round(diff_cambio, 4)

    n_iter = 0
    while n_iter < max_iter:
        converged = (
            np.all(diff_selic  == 0) and
            np.all(diff_expec  == 0) and
            np.all(diff_cambio == 0)
        )
        if converged:
            break

        n_iter += 1

        shock_matrix[:n_selic,  i_eps_i]  -= diff_selic
        shock_matrix[:n_expec,  i_eps_ei] -= diff_expec
        shock_matrix[:n_cambio, i_eps_e]  -= diff_cambio

        y_irf = simult(model, shock_matrix)

        diff_selic  = np.round(y_irf[i_it,     :n_selic]  - target_selic,  6)
        diff_expec  = np.round(y_irf[i_expec,  :n_expec]  - target_expec,  4)
        diff_cambio = np.round(y_irf[i_cambio, :n_cambio] - target_cambio, 4)

    return shock_matrix, n_iter


# ---------------------------------------------------------------------------
# Extração do resultado — colunas A-I dos runners originais
# ---------------------------------------------------------------------------

OUTPUT_VARS = ["it", "inflt_focus_t4", "delta_e", "ht", "piStar_t",
               "brent_t", "piI_t", "piL_t", "piM_t"]
OUTPUT_COLS = ["it", "expectativa", "delta_e", "ht", "ICbr",
               "Brent", "IPCA", "Livres", "Administrados"]


def extract_output(model: dict, y_irf: np.ndarray) -> np.ndarray:
    """
    Extrai as 9 séries de saída (equivalente às colunas A-I do Excel).

    Returns
    -------
    result : np.ndarray
        Matriz irf_periods × 9 (mesma estrutura do AA original nos .m).
    """
    var = {name: i for i, name in enumerate(model["endo_names"])}
    rows = [var[v] for v in OUTPUT_VARS]
    return y_irf[rows, :].T   # (irf_periods × 9)


# ---------------------------------------------------------------------------
# Interface principal: simular um cenário completo
# ---------------------------------------------------------------------------

def run_scenario(
    model: dict,
    target_selic: list[float],
    target_expec: list[float],
    target_cambio_pct: list[float],
    direct_shocks: Optional[dict] = None,
) -> dict:
    """
    Roda um cenário completo e retorna os resultados.

    Parameters
    ----------
    target_cambio_pct : list[float]
        %∆ desejada do câmbio por período (ex.: [(5.00/5.20-1)*100, 0, 0, ...]).

    Returns
    -------
    dict com:
        'output'        : np.ndarray (irf_periods × 9) — colunas A-I
        'shock_matrix'  : np.ndarray (irf_periods × nexo)
        'n_iter'        : int
        'col_names'     : list[str]
    """
    shock_matrix, n_iter = build_shock_matrix(
        model=model,
        target_selic=target_selic,
        target_expec=target_expec,
        target_cambio=target_cambio_pct,
        direct_shocks=direct_shocks,
    )

    y_irf  = simult(model, shock_matrix)
    output = extract_output(model, y_irf)

    return {
        "output": output,
        "shock_matrix": shock_matrix,
        "n_iter": n_iter,
        "col_names": OUTPUT_COLS,
    }
