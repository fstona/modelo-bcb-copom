# CLAUDE.md

Instruções para o Claude Code neste projeto.

## O que é este projeto

Interface Streamlit para rodar simulações do modelo semi-estrutural do BCB (Relatório de Inflação 2024/Q2) sem Matlab/Octave. O motor Python lê as matrizes de solução do Dynare (`data/mAgregado2024q2_base_results.mat` via `scipy.io`) e reimplementa `simult_()` em NumPy.

## Como rodar

```bash
streamlit run app.py
# http://localhost:8501
```

## Estrutura de arquivos

```
app.py                     ← entry point; adiciona modules/ ao sys.path (linha ~14)
requirements.txt
modules/
  engine.py                ← simult_() + loop de convergência (núcleo do motor)
  calculos.py              ← compounding K-M, projeção final AA-AC, export Excel
  baseline_io.py           ← lê/salva data/projecoes_copom.xlsx
  mercado_io.py            ← PTAX (SGS 10813), Selic/Focus (Olinda), IPCA/Focus, calendário
data/
  mAgregado2024q2_base_results.mat   ← solução Dynare — NÃO EDITAR
  projecoes_copom.xlsx               ← projeções oficiais BCB (uma aba por reunião)
  copom_calendar.json                ← datas das reuniões 2025–2026
  copom2025_novo.xlsx / copom2026_novo.xlsx  ← outputs de simulação
exploratory/               ← scripts Matlab/Dynare originais (referência; não usados pelo app)
```

## Arquitetura do motor (`engine.py`)

Solução linear de primeira ordem:
```python
k2_orig  = compute_k2(kstate, max_lag, endo_nbr)
epsilon  = ghu @ shock_matrix.T          # 64 × T
y        = np.zeros((64, T + max_lag))
for t in range(1, T + max_lag):
    yhat            = y[k2_orig, t-1]
    y[order_var, t] = ghx @ yhat + epsilon[:, t-1]
y_irf = (y + ys[:, None])[:, max_lag:] - ys[:, None]
```

Loop de convergência: ajusta `eps_i`, `eps_ei`, `eps_e` até `diffSelic`, `diffExpec`, `diffCambio` ≈ 0 (tol 1e-6 / 1e-4 / 1e-4, máx 10.000 iterações).

## Escalagem dos choques diretos

| Choque | Escalagem obrigatória |
|---|---|
| `eps_monit` (administrados) | valor ÷ (1 − ωL), onde ωL = 0.741 |
| `eps_piL` (livres) | valor ÷ ωL |
| `eps_e` (câmbio) | (spot_novo / spot_anterior − 1) × 100 |
| `eps_brent` | %∆ direta |
| `eps_h2008` | pp do hiato |

A UI do `app.py` já aplica essas escalagens automaticamente — não dobrar no engine.

## Matrizes do `.mat`

| Campo | Dimensão | Descrição |
|---|---|---|
| `oo_.dr.ghx` | 64×40 | Propagação pelos estados |
| `oo_.dr.ghu` | 64×28 | Impacto dos 28 choques |
| `oo_.dr.ys` | 64×1 | Steady state |
| `oo_.dr.order_var` | 64×1 | Mapeamento DR → ordenação original |
| `oo_.dr.kstate` | 48×4 | Índices das variáveis de estado |
| `M_.endo_names` | 64 | Nomes das 64 variáveis (39 orig + 25 aux) |
| `M_.exo_names` | 28 | Nomes dos 28 choques |

## Pós-processamento (`calculos.py`)

- **K-M (compounding)**: janela crescente de 1..4 trimestres, depois deslizante de 4 (K2=G2, K5=G2:G5, K6=G3:G6, ...)
- **V (indireto)**: `Livres×(1−peso) + Adm×peso`, peso padrão = 0.25
- **AA-AC (projeção final)**: `V + K`, `Livres_RPM + L`, `Adm_RPM + M`
- Baseline RPM lido de `projecoes_copom.xlsx`, aba da reunião *anterior* à simulada

## APIs de mercado (`mercado_io.py`)

| Dado | Endpoint / Série |
|---|---|
| PTAX | SGS 10813 |
| Selic realizada | SGS 432, D+1 após reunião |
| Selic Focus | Olinda `ExpectativasMercadoSelic`, `baseCalculo eq 1` |
| IPCA 12m suav | Olinda `ExpectativasMercadoInflacao12Meses`, `Suavizada eq 'S' and baseCalculo eq 0` |
| IPCA trimestral | Olinda `ExpectativasMercadoTrimestrais`, `baseCalculo eq 0` |
| IPCA anual | Olinda `ExpectativasMercadoInflacaoAnuais` |

**Atenção:** `baseCalculo eq 0` para IPCA (não `eq 1`). `DataReferencia` trimestral vem como `"4/2026"` → normalizar para `"4T/2026"`.

**Regra PTAX:** média dos 10 dias úteis encerrada na sexta-feira da semana *anterior* à do Copom, arredondada para múltiplo de R$0,05 (`(d / 0.05).quantize(1) * 0.05`).

## Convenção do `projecoes_copom.xlsx`

Nome da aba = reunião que *publicou* as projeções (não a que as usará). Ao simular reunião X, o app carrega a aba da reunião *anterior* como baseline RPM.

## Módulo Brent (`modules/brent_io.py`)

Replica a curva de Brent do BCB (Apêndice Metodológico do RPM/RI).

**Arquivo de entrada:** `data/Brent_full.xlsx` (exportação Bloomberg)
- Aba `copia`
- Linha 3 (índice 3): tickers no formato `'COK6 Comdty'`
- Linha 6+ (índice 6+): coluna 0 = serial Excel de data, demais = PX_LAST
- Cobertura: contratos ICE Brent ativos + histórico desde ~set/2025

**API pública:**
```python
levels, q_prev = load_brent_curve(meeting_date, bloomberg_path=None, cutoff=None)
eps = levels_to_eps_brent(levels, q_prev)
```
- `levels`: lista de floats — nível USD/barril por trimestre, a partir do trimestre da reunião
- `q_prev`: nível do trimestre anterior (necessário para o primeiro eps_brent)
- `eps_brent[t] = (levels[t] / levels[t-1] - 1) * 100`; zeros trailing removidos

**Metodologia BCB:**
1. Cutoff = sexta-feira da semana anterior à semana da reunião
2. P₀ = média de 10 dias úteis do front-month encerrados no cutoff
3. Strip M+1..M+6: média de 10 dias de cada contrato ICE Brent ativo
4. Contratos expirados antes do cutoff: halfstale = (preço_stale + front_month) / 2
5. Meses já realizados no trimestre corrente: média mensal histórica do front-month
6. Além de M+6: crescimento de 2% a.a.

**Convenção de expiração ICE Brent:** último dia útil do segundo mês antes do mês de entrega (ex: COK6 = mai/26, expira em 31/mar/2026).

**Precisão validada:** RMSE = 0.09 USD (RPM Dez/2025), 0.32 USD (RPM Mar/2026).

**No app:** uploader na aba "Choques diretos". Se vazio, usa `data/Brent_full.xlsx` do repo.
O usuário vê e edita **níveis em USD** por trimestre; o app calcula `eps_brent` internamente.
`data/Brent_full.xlsx` está no GitHub (arquivo Bloomberg — repo privado).

## Variáveis de saída (9 colunas A-I)

`it`, `inflt_focus_t4`, `delta_e`, `ht`, `piStar_t`, `brent_t`, `piI_t`, `piL_t`, `piM_t`

## Steady state

- Inflação trimestral: `pi_meta_ss/4 = 0.75%`
- Taxa neutra real: `rss = 4%` a.a.
- Selic: `it = rt_barTaylor + piMeta_t`
- Câmbio (PPC): depreciação de 0.25% a.t. (diferencial de metas BR–EUA)

## Pendências

1. Sincronizar repositório GitHub com a nova estrutura de pastas (`remote_model/` como raiz do repo)
2. Atualizar `copom_calendar.json` com reuniões de 2027 quando BCB publicar

## Diário técnico

Detalhes completos de implementação, bugs corrigidos e log de progresso estão em `ModeloBCB_remoto.md`.
