# ModeloBCB Remoto — Documentação do Projeto

> **Última atualização:** 2026-05-24
> **Status geral:** app funcional com automação completa de Brent Bloomberg, câmbio endógeno no cenário alternativo, gráficos de câmbio e expectativas redesenhados, correção de dados históricos por reunião. Repo GitHub sincronizado.

## Objetivo

Criar uma interface web (Streamlit) para rodar simulações do modelo semi-estrutural do BCB sem depender de Matlab ou Octave. O motor de cálculo usa as matrizes de solução exportadas pelo Dynare, lidas em Python via `scipy.io`.

## Como rodar

```bash
cd /Users/fstona/local/Trabalho/Filipe/Filipe/Modelos/ModeloBCB/remote_model
streamlit run app.py
# acessa em http://localhost:8501
```

Dependências: `pip install streamlit pandas numpy scipy openpyxl plotly requests`

## Estrutura de pastas (atual)

```
remote_model/
├── app.py                        ← entry point Streamlit (fica sempre na raiz)
├── CLAUDE.md                     ← instruções para o Claude Code
├── README.md
├── requirements.txt
├── ModeloBCB_remoto.md           ← este arquivo
│
├── modules/                      ← lógica Python (importada por app.py via sys.path)
│   ├── engine.py                 ← simult_() + loop de convergência
│   ├── calculos.py               ← compounding K-M, tabelas de resultado, export Excel
│   ├── baseline_io.py            ← lê/salva projecoes_copom.xlsx
│   └── mercado_io.py             ← PTAX, Selic/Focus, IPCA/Focus, calendário Copom
│
├── data/                         ← todos os arquivos de dados
│   ├── mAgregado2024q2_base_results.mat   ← solução Dynare (artefato principal)
│   ├── projecoes_copom.xlsx               ← projeções oficiais BCB por reunião
│   ├── copom_calendar.json                ← datas das reuniões 2025-2026
│   ├── copom2025_novo.xlsx                ← outputs de simulação 2025
│   ├── copom2026_novo.xlsx                ← outputs de simulação 2026
│   ├── simula_copom.xlsx                  ← inputs de expectativas/Selic
│   ├── simula_copom2.xlsx
│   └── brent e cambio.xlsx                ← premissas de commodities/câmbio
│
└── exploratory/                  ← scripts Matlab/Dynare (não usados pelo app)
    ├── mAgregado2024q2_base.mod           ← arquivo do modelo Dynare
    ├── mAgregado2024q2_base.log           ← log da última execução Dynare
    ├── mAgregado2024q2_base/              ← output Dynare (checksum, Output/)
    ├── +mAgregado2024q2_base/             ← pacote Octave gerado pelo Dynare
    ├── runModelo24q2_*.m                  ← runners por reunião do Copom
    ├── simul_cambio_e_hiato.m             ← grid search câmbio × hiato
    └── brent/                             ← dados históricos de Brent
```

**Importante para imports:** `app.py` adiciona `modules/` ao `sys.path` na linha 14:
```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "modules"))
```
`mercado_io.py` referencia `copom_calendar.json` como `Path(__file__).parent.parent / "data" / "copom_calendar.json"`.

## Pendências

| # | Item | Prioridade |
|---|---|---|
| 1 | Atualizar `copom_calendar.json` com reuniões de 2027 quando BCB publicar | Baixa |
| 2 | Validar contagem do strip Brent (halfstale vs 6 meses corridos) contra RPM Jun/2026 — ver `exploratory/brent/memoria_brent.md` | Média |
| 3 | Atualizar `Brent_full.xlsx` com dados até 12/jun/2026 (cutoff Jun/2026) | Alta (pré-Jun26) |

## Contexto

O modelo `mAgregado2024q2_base.mod` é resolvido pelo Dynare (ordem 1) e sua solução fica armazenada em `mAgregado2024q2_base_results.mat`. Esse arquivo é o único artefato do Matlab/Dynare — se o modelo mudar, basta rodar Dynare uma vez para atualizá-lo.

### Fluxo original (Matlab)
1. `dynare mAgregado2024q2_base` → popula `M_`, `oo_`, `options_`
2. `simult_()` aplica choques e itera até convergir (Selic / expectativas / câmbio)
3. Output salvo em `copom2026_novo.xlsx`, colunas A–I (desvios trimestrais de 9 variáveis)
4. Excel faz pós-processamento: compounding (K-M), baseline RPM (S-U), projeção final (AA-AC)

### Fluxo novo (Python + Streamlit)
```
mAgregado2024q2_base_results.mat  ←  gerado pelo Dynare (artefato fixo)
         ↓  scipy.io.loadmat
    engine.py   →  simult_() em NumPy + loop de convergência
         ↓
    calculos.py →  compounding K-M, colunas AA-AC
         ↓
    app.py      →  Streamlit: inputs + gráficos + export Excel
```

## Matrizes de solução (extraídas do .mat)

| Campo | Dimensão | Conteúdo |
|---|---|---|
| `oo_.dr.ghx` | 64×40 | Propagação pelos estados |
| `oo_.dr.ghu` | 64×28 | Impacto dos 28 choques |
| `oo_.dr.ys` | 64×1 | Steady state de todas as variáveis |
| `oo_.dr.order_var` | 64×1 | Mapeamento DR → ordenação original |
| `oo_.dr.kstate` | 48×4 | Índices das variáveis de estado |
| `M_.endo_names` | 64 | Nomes das variáveis (incl. auxiliares) |
| `M_.exo_names` | 28 | Nomes dos choques |
| `M_.maximum_lag` | 1 | Número de defasagens |

## Detalhes de implementação

### simult_() — núcleo do motor
Tradução fiel do `simult_.m` do Dynare 4.6.4, order=1, partindo do steady state:
```python
k2_orig  = compute_k2(kstate, max_lag, endo_nbr)   # índices de estado (original ordering)
epsilon  = ghu @ shock_matrix.T                      # 64 × T
y        = np.zeros((64, T + max_lag))               # desvios do SS
for t in range(1, T + max_lag):
    yhat             = y[k2_orig, t-1]
    y[order_var, t]  = ghx @ yhat + epsilon[:, t-1]
y_levels = y + ys[:, None]
y_irf    = y_levels[:, max_lag:] - ys[:, None]      # desvios para T períodos
```

### Loop de convergência
Itera ajustando `eps_i`, `eps_ei`, `eps_e` até `diffSelic`, `diffExpec`, `diffCambio` ≈ 0 (tolerância 1e-6 / 1e-4 / 1e-4). Máximo 10.000 iterações.

### Escalagem dos choques diretos
| Choque | Escalagem |
|---|---|
| `eps_monit` | valor / (1 - oomegaL) |
| `eps_piL` | valor / oomegaL |
| `eps_brent` | %∆ direta |
| `eps_h2008` | pp do hiato |
| `eps_e` | (spot_novo/spot_anterior - 1)*100 |

### Pós-processamento (calculos.py)
- **K-M (compounding)**: `100 * (PRODUCT(col/100 + 1) - 1)` cumulativo por horizonte
- **V (indireto)**: `Livres*(1-peso) + Adm*peso`, peso padrão = 0.25
- **AA-AC (projeção final)**: `V + K`, `Livres_RPM + L`, `Adm_RPM + M`
- **X (diferença vs BCB)**: `IPCA_RPM - round(V, 1)`

### Variáveis de saída (9 colunas A-I)
`it`, `inflt_focus_t4`, `delta_e`, `ht`, `piStar_t`, `brent_t`, `piI_t`, `piL_t`, `piM_t`

## Etapas

| # | Etapa | Arquivo(s) | Status |
|---|---|---|---|
| 1 | Motor Python: `simult_()` + loop de convergência | `modules/engine.py` | ✅ Concluída |
| 2 | Pós-processamento: compounding, AA-AC | `modules/calculos.py` | ✅ Concluída |
| 3 | Interface Streamlit | `app.py` | ✅ Concluída |
| 4 | Validação final vs `copom2026_novo.xlsx → Abr26` | — | ✅ Concluída |
| 5 | Calendário Copom + automação PTAX | `data/copom_calendar.json`, `modules/mercado_io.py` | ✅ Concluída |
| 6 | Persistência projeções BCB + aba Baseline | `modules/baseline_io.py`, `data/projecoes_copom.xlsx` | ✅ Concluída |
| 7 | Automação Selic/Focus (exploração) | — | ✅ Explorado |
| 8 | Integração Selic/Focus no dashboard | `modules/mercado_io.py`, `app.py` | ✅ Concluída |
| 9 | Automação expectativas IPCA (Focus trimestral + 12m suav + anual) | `modules/mercado_io.py`, `app.py` | ✅ Concluída |
| 10 | Reorganização de pastas: `modules/`, `data/`, `exploratory/` | toda a árvore | ✅ Concluída |
| 11 | Sincronizar repositório GitHub com nova estrutura | — | ✅ Concluída |
| 12 | Automação curva de Brent Bloomberg | `modules/brent_io.py`, `app.py`, `data/Brent_full.xlsx` | ✅ Concluída |
| 13 | Correção dados de mercado para reuniões históricas | `app.py` | ✅ Concluída |
| 14 | Câmbio endógeno no cenário alternativo + gráfico de nível | `app.py` | ✅ Concluída |
| 15 | Gráfico de expectativas redesenhado (linha + cauda endógena) | `app.py` | ✅ Concluída |

## Critério de validação

Rodar cenário Abr26 pelo Python e comparar com `copom2026_novo.xlsx → Abr26`:
- Colunas A-I: desvio < 1e-6 em todos os períodos
- Colunas K-M: desvio < 1e-6
- Colunas AA-AC: desvio < 1e-4 (depende do input manual do RPM)

## Log de progresso

### [2026-05-24] Reorganização de pastas e criação de `remote_model/`

**Contexto:** o projeto vivia em `ModeloBCB/Modelo24Q2_octave/`, misturado com outros projetos no diretório pai. Foi criada a pasta `remote_model/` para isolar o app Streamlit.

**Mudanças estruturais:**
- Todos os arquivos de `Modelo24Q2_octave/` movidos para `remote_model/`
- Dentro de `remote_model/`, reorganizado em três subpastas:
  - `modules/` — os quatro módulos Python
  - `data/` — arquivos `.mat`, `.xlsx`, `.json`
  - `exploratory/` — scripts Matlab/Dynare, `.mod`, `.log`, pasta `brent/`
- `app.py` permanece na raiz de `remote_model/`

**Alterações de código necessárias:**

`app.py` — duas mudanças:
```python
# Linha ~14: adicionado para resolver imports dos módulos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "modules"))

# Linhas 44-45: paths atualizados para subpasta data/
MAT_FILE      = os.path.join(os.path.dirname(__file__), "data", "mAgregado2024q2_base_results.mat")
BASELINE_FILE = os.path.join(os.path.dirname(__file__), "data", "projecoes_copom.xlsx")
```

`modules/mercado_io.py` — uma mudança:
```python
# Antes (relativo ao próprio arquivo):
CALENDAR_PATH = Path(__file__).parent / "copom_calendar.json"
# Depois (sobe um nível para data/):
CALENDAR_PATH = Path(__file__).parent.parent / "data" / "copom_calendar.json"
```

**Validação:** app iniciado, `/_stcore/health` → `ok`, modelo carregado (64 vars, 28 choques), scenario run convergiu em 8 iterações.

**Próximo passo:** atualizar o repositório GitHub — o repo apontava para `Modelo24Q2_octave/`; agora o conteúdo está em `remote_model/`.

---

### [Sessão 1 — cont. 2] Baseline adaptativo por posição no trimestre

**Regra derivada das reuniões do Copom de 2025:**

| Trimestre da reunião | Horizontes na tabela da decisão |
|---|---|
| Q1 (Jan/Mar) | Q4/ano_atual + **Q3/ano+1** (horizonte relevante) |
| Q2 (Abr/Jun) | Q4/ano_atual + **Q4/ano+1** (relevante = anual do próximo ano) |
| Q3 (Jul/Set) | Q4/ano_atual + Q4/ano+1 + **Q1/ano+2** |
| Q4 (Out/Dez) | Q4/ano_atual + Q4/ano+1 + **Q2/ano+2** |

O horizonte relevante é sempre exatamente **+6 trimestres** a partir do trimestre da reunião.
Intermediário (Q4/ano+1) aparece apenas quando o relevante ultrapassa esse ponto (Q3 e Q4).

**Implementação:**
- `get_copom_decision_horizons(start_year, start_q)` → lista de índices + rótulos
- Índices no vetor de 16 trimestres: Q4/ano=`4-start_q`, Q4/próx=`8-start_q`, relevante=`6` (sempre)
- `build_results_table(..., sparse_rpm=True)` aceita lista de 16 posições com NaN nos períodos sem dado
- Gráfico de resultados muda de linhas (1ª reunião) para barras agrupadas (2ª reunião)
- Sidebar mostra quais horizontes são esperados para a reunião selecionada

### [Sessão 1 — cont.] Refinamentos na interface

**Câmbio PPC simplificado:**
- Cenário base: usuário informa apenas dois valores spot (reunião anterior + Q1 do horizonte)
- App calcula %∆ e monta `target_cambio = [var_q1, 0.0, 0.0, ..., 0.0]` (N_CAMBIO_BASE=7 períodos)
- Desvio 0.0 = PPC (nível de steady state do modelo, diferencial de metas Brasil–EUA = 0,25% a.t.)
- Cenário alternativo: expõe inputs de %∆ para os períodos 2+ individualmente

**Parsing automático do nome da reunião → horizonte:**
- `parse_copom_name("Abr26")` → (2026, Q2); `"Dez26"` → (2026, Q4); `"Mar26"` → (2026, Q1)
- Suporta sufixos: `"Abr26 - alt"`, `"Abr26 - ExpecEndo"` → extrai apenas o prefixo
- Fallback para 2026Q2 se o nome não for reconhecível
- Horizonte de 16 trimestres gerado automaticamente a partir do trimestre inicial

### [Sessão 1] Implementação completa

**Etapa 1 — engine.py** ✅
- Lê `mAgregado2024q2_base_results.mat` via `scipy.io.loadmat`
- Implementa `simult_()` fiel ao Dynare 4.6.4 (order=1, starting from SS)
- Loop de convergência idêntico ao runner .m: ajusta `eps_i`, `eps_ei`, `eps_e`
- Validação vs Excel Abr26: desvio máximo 4.6×10⁻⁵ (câmbio, tolerância 1e-4); outros < 2.2×10⁻⁶
- Converge em 18 iterações para o cenário Abr26

**Etapa 2 — calculos.py** ✅
- Descoberta importante: compounding K-M não é acumulado desde o início — é janela crescente de 1 a 4 trimestres (t=1..4) e depois janela deslizante de 4 trimestres (t≥5)
  - Fórmulas: K2=G2, K3=G2:G3, K4=G2:G4, K5=G2:G5, K6=G3:G6, K7=G4:G7...
- Projeção final (AA): usa baseline RPM indireto (V = Livres×(1-peso) + Adm×peso) como base, não o headline do BCB
- Validação K-M: desvio máximo 4.32×10⁻⁶
- Validação AA-AC: desvio máximo 3.75×10⁻⁶ (nos 10 períodos com dados RPM)

**Etapa 3 — app.py** ✅
- 4 abas: Targets / Choques diretos / Baseline RPM / Resultados
- Escalagem automática de eps_monit (÷(1-ωL)) e eps_piL (÷ωL) na UI
- Câmbio: usuário insere nível spot, app calcula %∆ automaticamente
- Exportação Excel em BytesIO (sem dependência de arquivo local)
- Rodando em http://localhost:8501

**Etapa 4 — Validação** ✅
- Cenário Abr26 reproduzido com desvios < 5×10⁻⁵ vs Matlab original

### [Sessão 2] Automação de inputs de mercado

#### Câmbio (PTAX)

**Regra BCB:** média dos últimos 10 dias úteis encerrada na sexta-feira da semana *anterior* à semana do Copom. Arredondamento para o múltiplo de R$0,05 mais próximo (round half-up).

**Implementação (`mercado_io.py`):**
- `load_copom_calendar()` → lê `copom_calendar.json`
- `find_meeting_date(name, cal)` → "Jun26" → `date(2026,6,17)` 
- `get_meeting_position(date, cal)` → 1 ou 2 (posição no trimestre, auto-detectada)
- `previous_meeting(date, cal)` → reunião imediatamente anterior
- `meeting_name_from_date(date)` → `date(2026,4,29)` → "Abr26"
- `_ref_date_for_meeting(date)` → sexta-feira da semana anterior
- `fetch_ptax_window(ref_date, n=10)` → SGS série 10813
- `round_to_5cents(value)` → arredonda para múltiplo de 0,05 (via `Decimal / 0.05 → round → * 0.05`)
- `calc_ptax_para_reuniao(date)` → resultado com avg, sugestao, partial flag
- `calc_ptax_atual(today, cal)` → PTAX para a próxima reunião

**Bug corrigido:** implementação inicial de `round_to_5cents` usava `Decimal.quantize("0.05")` que apenas arredonda casas decimais sem respeitar múltiplos de 0,05. Corrigido para `(d / 0.05).quantize(1) * 0.05`.

#### Calendário do Copom (`copom_calendar.json`)

Reuniões de 2025 e 2026 salvas em ISO. Atualizar quando BCB publicar 2027.

```json
{ "meetings": ["2025-01-29", ..., "2026-12-09"] }
```

#### Projeções oficiais BCB (`baseline_io.py` + `projecoes_copom.xlsx`)

**Convenção de nomenclatura das abas:** o nome da aba = reunião que *publicou* as projeções (não a que vai usá-las).

| Aba | Conteúdo | Tipo |
|---|---|---|
| `Jan26` | Copom jan/26 (sparse: Q4/26, Q3/27 HR, Q4/27) | 2ª reunião Q1 |
| `Mar26` | RPM mar/26 (dense: 10 trimestres a partir de Q2/26) | 1ª reunião Q2 |
| `Abr26` | Copom abr/26 (sparse: Q4/26, Q4/27 HR, Q1/28) | 2ª reunião Q2 |

**Lógica do dashboard:** ao simular reunião X, carrega a aba da reunião *anterior* (auto-derivada do calendário) como baseline RPM. A aba Baseline é somente leitura — o usuário edita o Excel diretamente.

**Funções (`baseline_io.py`):** `list_meetings`, `load_meeting`, `save_bcb_projections`.

#### Melhorias no dashboard (`app.py`)

- **Remoção do radio 1ª/2ª reunião:** auto-detectado pelo calendário via `get_meeting_position`
- **Remoção do input "Reunião anterior":** auto-derivado via `previous_meeting` + `meeting_name_from_date`
- **Câmbio com step=0.05** e arredondamento forçado no value= e no retorno do widget
- **RPM baseline** carregado da aba da reunião *anterior* (não da atual)
- **Linha "Anterior"** na tabela comparativa usa as mesmas projeções do RPM baseline

#### Selic/Focus (exploração concluída, integração pendente)

**API:** `ExpectativasMercadoSelic` (Olinda) — não `ExpectativaMercadoSelic` (inválido).
**Campos:** `Data`, `Reuniao` (formato `RN/YYYY`), `Mediana`, `baseCalculo`.
**Filtro:** `baseCalculo eq 1` (últimas 30 dias — padrão BCB).

**Regra de conversão por reunião → trimestral:**
- R1+R2 → Q1, R3+R4 → Q2, R5+R6 → Q3, R7+R8 → Q4
- Para anos fora do calendário (2027+): fallback `(N-1)//2 + 1`

**Regra do trimestre misto (2ª reunião do trimestre):**
- R1 do trimestre já realizada → busca SGS série 432 no D+1 após a reunião
- R2 ainda no Focus → usa mediana do Focus
- Média das duas compõe a curva do trimestre
- Exemplo Jun/26: R3/2026 (abr 29) realizada (SGS 432 em 30/abr) + Focus R4/2026 (jun 17) → Q2/2026

**Cálculo do delta (input para o modelo):**
- `curva_prev` = Focus da sexta anterior à reunião anterior (ex: 13/mar para Mar/26)
- `curva_curr` = Focus da sexta anterior à reunião atual (ex: 24/abr para Abr/26)
- `delta[Q]` = curva_curr[Q] − curva_prev[Q] → choque de Selic por trimestre

**Resultado teste Abr/26 (delta Mar→Abr):**

| Trimestre | Mar/26 | Abr/26 | Delta |
|---|---|---|---|
| Q2/2026 | 14.00 | 14.375 | +0.375 |
| Q3/2026 | 13.00 | 13.625 | +0.625 |
| Q4/2026 | 12.375 | 13.125 | +0.750 |
| Q1/2027 | 12.00 | 12.625 | +0.625 |
| Q2/2027 | 11.625 | 11.9375 | +0.313 |

*Nota: Q1/2026 para Mar/26 = avg(SGS432[29/jan]=15.00, Focus R2=14.75) = 14.875. Por 24/abr ambas R1 e R2 já realizadas, Q1 não aparece na curva futura de Abr/26.*

### [Sessão 3] Integração Selic/Focus no dashboard

#### Novas funções em `mercado_io.py`

| Função | Descrição |
|---|---|
| `fetch_selic_focus_on_date(focus_date)` | Olinda `ExpectativasMercadoSelic`, retorna `(date_used, {Reuniao: Mediana})` com `baseCalculo eq 1` |
| `get_selic_sgs432(meeting_date)` | SGS série 432, busca D+1 após a reunião (taxa realizada) |
| `build_meeting_label_map(calendar)` | Mapeia `"R3/2026"` → `{date, quarter, n}` a partir do calendário local |
| `selic_label_to_quarter(label, label_map)` | `"R3/2026"` → `"Q2/2026"` com fallback `(N-1)//2+1` para anos fora do calendário |
| `compute_selic_quarterly_delta(curr, prev, label_map, start_q, n_q)` | Delta trimestral: `avg(curr) - avg(prev)` por reuniões em comum; reuniões sem cobertura no prev → delta 0 |

#### Interface da Selic no dashboard (`app.py`)

**Aba Targets — seção Selic:**
- Inputs por reunião do Copom, agrupados por trimestre (header `**Q2/2026**` etc.)
- Reuniões realizadas (trimestre misto): campo desabilitado com valor do SGS 432 D+1
- Reuniões futuras: campo editável pré-preenchido com mediana do Focus atual; caption mostra `anterior: X.XX | Δ: ±Y.YY`
- `n_selic` (nº de trimestres a ancorar): padrão **7**, máximo dinâmico = último trimestre com cobertura do Focus anterior — impedindo que o usuário solicite deltas onde não há dado de comparação
- Internamente: `selic_vals = compute_selic_quarterly_delta(...)` com zeros trailing removidos — trimestres além da cobertura rodam endogenamente pela regra de Taylor

**Três funções cached:**
```python
@st.cache_data(ttl=3600)  _cached_prev_selic_focus(focus_date_iso)   # Focus da sexta anterior à reunião anterior
@st.cache_data(ttl=3600)  _cached_curr_selic_focus(curr_date_iso)    # Focus mais recente (hoje)
@st.cache_data(ttl=86400) _cached_realized_selic(meeting_date_iso)   # SGS 432 D+1 (dado histórico)
```

#### Correção de convergência

**Bug:** `selic_vals` com zeros trailing forçava `it[t] = 0` para trimestres sem cobertura, lutando contra a regra de Taylor e causando muitas iterações.  
**Fix:** após `compute_selic_quarterly_delta`, remove zeros do final da lista antes de passar ao engine. O modelo roda endogenamente a partir do último trimestre ancorado.

#### Gráficos de Selic nos Resultados

**Gráfico 1 — desvio (barras):**
- Barras azuis: `df["it"]` — trajetória completa do modelo (16 trimestres), inclui tail endógena
- Marcadores diamante vermelhos: delta target do Focus (apenas nos trimestres ancorados)

**Gráfico 2 — nível % a.a.:**
- Horizonte: limitado ao último trimestre com cobertura do Focus anterior (não vai além)
- Eixo x: mesmo formato do gráfico de barras (`2026Q2`, `2026Q3`, ...)
- Linha azul — Cenário atual: `_curr_q_selic[t]` para t < n_selic (input do usuário); `prev_focus_avg[t] + df["it"][t]` para t ≥ n_selic (resposta endógena em nível)
- Linha laranja tracejada — Focus anterior: cobertura total até o horizonte (não limitada ao n_selic do usuário)
- Linha pontilhada vertical: marca fronteira entre anchored e endógena

**Fix técnico:** `add_vline` do Plotly quebra com eixo categórico em versões antigas — substituído por `add_shape(type="line", yref="paper")`.

### [Sessão 4] Automação de expectativas IPCA no dashboard

#### Endpoints e filtros corretos (validados contra `simula_copom.xlsx`)

| Dado | Endpoint | Filtro |
|---|---|---|
| 12m suavizado (t=0) | `ExpectativasMercadoInflacao12Meses` | `Indicador eq 'IPCA' and Suavizada eq 'S' and baseCalculo eq 0` |
| Trimestral (t≥1) | `ExpectativasMercadoTrimestrais` | `Indicador eq 'IPCA' and baseCalculo eq 0` |
| Anual (âncoras) | `ExpectativasMercadoInflacaoAnuais` | `Indicador eq 'IPCA'` |

**Erros encontrados e corrigidos:**
- `baseCalculo eq 1` devolvia valores diferentes dos da planilha; o correto é `baseCalculo eq 0` para ambos os endpoints IPCA
- Endpoint `ExpectativasMercadoInflacaoTrimestral` não existe — o correto é `ExpectativasMercadoTrimestrais`
- `DataReferencia` retorna no formato `"4/2026"` (sem "T") → normalizado para `"4T/2026"` no `fetch_ipca_trimestral`

#### Lógica da curva de expectativas (`build_expec_curve`)

```
curve[0]   = suav_12m  (12m smoothed Focus, baseCalculo=0)
curve[t≥1] = 100*(PROD(Q_{t+1}..Q_{t+4})/100+1) - 1
             onde Q_k = Focus trimestral do trimestre advance(start, t+k), k=1..4
```

**Exemplo (start=2T/2026, t=1 → 2026Q3):**
- Compound de 4T/2026, 1T/2027, 2T/2027, 3T/2027
- Abr/24: [0.8734, 1.2917, 0.8725, 0.6464] → 3.73
- Mai/8: [0.8623, 1.3054, 0.8825, 0.6519] → 3.75 → **delta = +0.02**

**Fallback para horizontes além da cobertura trimestral:**
- Âncoras anuais (Q4 de cada ano-calendário) inseridas nas posições correspondentes
- Interpolação linear entre pontos conhecidos (suav_12m, compounds, âncoras anuais)

#### Interface no dashboard (`app.py`)

**Aba Targets — seção Expectativas:**
- Pré-preenchida com `compute_expec_delta(curr_curve, prev_curve)` por trimestre
- Captions mostram `atual: X.XX | anterior: Y.YY` para cada período
- `n_expec`: padrão `min(6, períodos_com_dado)`, teto fixo 16 (editável manualmente)
- Zeros trailing removidos antes de passar ao engine (tail endógena)
- Aviso amarelo se dados Focus IPCA indisponíveis

**Três funções cached** (versão `_v=4` para invalidar cache ao mudar assinatura):
```python
@st.cache_data(ttl=3600)  _cached_prev_ipca_focus(focus_date_iso, _v=4)  # trimestral + suav + anual
@st.cache_data(ttl=3600)  _cached_curr_ipca_focus(curr_date_iso,  _v=4)  # idem, data de hoje
```

**Expander de diagnóstico** na aba Targets mostra chaves do dict trimestral, dados anuais e a tabela completa de `curr_curve / prev_curve / auto_delta`.

#### Gráfico de expectativas nos Resultados

- Barras agrupadas: **anterior** (laranja, offsetgroup=0) à esquerda, **atual** (azul, offsetgroup=1) à direita
- Eixo X limitado ao último trimestre com dado não-nulo em qualquer das duas curvas
- Título: "Expectativas de inflação 12m — comparativo entre reuniões"

### [2026-05-24] Sessão 5 — Reorganização do repo GitHub + automação Brent Bloomberg

#### Reorganização de pastas e sincronização com GitHub

**Contexto:** o repositório GitHub (`fstona/modelo-bcb-copom`) apontava para a estrutura antiga (`Modelo24Q2_octave/`, tudo na raiz). Os arquivos já haviam sido movidos para `remote_model/` com subpastas `modules/`, `data/`, `exploratory/`. Nesta sessão o `.git` foi migrado para `remote_model/` e o repo foi sincronizado.

**Mudanças no repo:**
- `.git` movido de `Modelo24Q2_octave/` para `remote_model/` (raiz do projeto)
- Git reconheceu automaticamente as renomeações: `baseline_io.py → modules/baseline_io.py`, `engine.py → modules/engine.py`, etc.
- `.gitignore` atualizado: `exploratory/` excluído; `data/*.xlsx` excluído exceto `projecoes_copom.xlsx` e `Brent_full.xlsx`
- `README.md` reescrito para refletir o app Streamlit (não mais Octave)
- `CLAUDE.md` criado do zero para o projeto Python

#### Módulo `modules/brent_io.py` — curva de Brent BCB

Replica a metodologia do Apêndice Metodológico do RPM/RI a partir de dados Bloomberg (`Brent_full.xlsx`, aba `copia`).

**API:**
```python
levels, q_prev = load_brent_curve(meeting_date, bloomberg_path=None, cutoff=None)
```
Retorna níveis trimestrais em USD + nível do trimestre anterior.

**Metodologia implementada:**
1. Cutoff = sexta da semana anterior à reunião
2. P₀ = média de 10 dias úteis do front-month encerrados no cutoff
3. Strip M+1..M+6: média de 10 dias de cada contrato ICE Brent ativo
4. Contratos expirados: `halfstale = (stale_10d + front_month_10d) / 2`
5. Meses já realizados no trimestre corrente: média mensal histórica do front-month
6. Além de M+6: crescimento de 2% a.a.

**Precisão:** RMSE = 0.09 USD (RPM Dez/2025), 0.32 USD (RPM Mar/2026).

**`Brent_full.xlsx`** adicionado a `data/` e rastreado no GitHub (necessário para Streamlit Cloud). Formato: aba `copia`, linha 3 = tickers (`COK6 Comdty`), linha 6+ = PX_LAST por data (serial Excel).

#### Integração no `app.py` — aba Choques diretos (seção Brent)

**UI:** inputs de nível USD por trimestre (editáveis), pré-preenchidos com a curva BCB calculada automaticamente.
- File uploader opcional para atualizar o Bloomberg sem push ao repo
- Caption por período: `{reunião anterior}: {nível USD} | Δ: ±X.XX%`
- Padrão: 3 trimestres exibidos

**Lógica do eps_brent (evolução em 3 iterações):**

| Iteração | Fórmula | Problema |
|---|---|---|
| 1 | `(level[t] / level[t-1] - 1)` — %∆ consecutiva dentro da curva | Caption comparava trimestres diferentes (Q2 vs Q1 dentro da mesma curva) |
| 2 | `(curr[t] / prev[t] - 1)` — revisão direta entre reuniões | Curva com backwardation: Q3 recebia choque positivo mesmo quando Brent caiu de Q2 a Q3 |
| 3 | Primeira diferença das revisões: `rev[t] - rev[t-1]` | ✅ Final |

**Fórmula final:**
```
rev[t] = (curr_level[t] / prev_level[t] - 1) × 100   ← mesma lógica da Selic
eps_brent[0] = rev[0]
eps_brent[t] = rev[t] - rev[t-1]   para t > 0
```

**Por que funciona:** captura simultaneamente a revisão de nível entre reuniões (quanto Brent foi revisado para cima/baixo no trimestre t) e a velocidade de queda da curva de futuros (backwardation). Se a curva cai de Q2 para Q3 mas ambos os trimestres foram revisados para cima, eps[Q3] = revisão menor − revisão maior → negativo → modelo vê a queda.

**Verificação no `.mod`:** `brent_t = eps_brent` (ρ=0, sem persistência). O choque não se acumula entre trimestres — cada período é independente. A persistência na inflação vem via Phillips curve lagged (`piL_t(-1)`), não via Brent.

#### Gráficos de Brent na aba Resultados

- **Nível (USD/barril):** linha verde (atual) + linha cinza tracejada (reunião anterior) — mesmo padrão da Selic
- **eps_brent (%∆):** barras cinza `#555555` — mesmo padrão dos outros gráficos de desvio
- Horizonte limitado ao mesmo número de trimestres do gráfico de Selic nível

### [2026-05-24] Sessão 6 — Correções e melhorias de interface

#### Fix: dados de mercado para reuniões históricas

**Problema:** ao simular uma reunião passada (ex: Abr26), os dados "atuais" (Focus Selic, Focus IPCA, PTAX) eram sempre buscados com `date.today()` como referência, trazendo dados de mai/2026 em vez de abr/2026.

**Fix em `app.py`:** definição de `_curr_ref_date` logo após `_today = date.today()`:
```python
_curr_ref_date = (
    ref_friday_for_meeting(_meeting_date)
    if _meeting_date and _meeting_date < _today
    else _today
)
```
Os três fetches passaram a usar `_curr_ref_date`:
- `_cached_curr_selic_focus(_curr_ref_date.isoformat())`
- `_cached_curr_ipca_focus(_curr_ref_date.isoformat())`
- PTAX: `get_ptax_reuniao_anterior(_meeting_date.isoformat())` se reunião passada

**Validação Abr26 (curr_ref = 24/abr/2026):**
- Focus Selic: data_used = 2026-04-24, 16 reuniões ✓
- PTAX: janela 10/abr→24/abr (10 dias úteis), sugestão R$5,00 ✓

#### Remoção do choque de meta de inflação (eps_meta)

A seção "Meta de inflação" foi removida da aba Choques diretos. O choque `eps_meta` não é utilizado nas simulações do BCB e gerava confusão na interface.

#### Câmbio endógeno no cenário alternativo

**Contexto:** verificação do `.mod` confirmou que a UIP é:
```
delta_e = e_ppct - ddelta*(it_dif - it_dif(-1)) + sigma_e*eps_e
```
`delta_e` não tem persistência AR própria — a pressão sobre o câmbio nos períodos 2+ vem do diferencial de juros (Selic via UIP). O Matlab original usava `targetVecCambio = [varCambio1 0 0 0 0 0 0]` (PPC forçada via eps_e), com a opção `%endógeno` comentada.

**Mudanças em `app.py`:**
- `N_CAMBIO_BASE`: 7 → 16 (PPC forçada em todos os 16 trimestres no modo padrão — resolve desvio em 2028Q1)
- Cenário alternativo: novo campo `n_cambio_alt` (0 a 15 períodos além do Q1)
  - **0** (padrão): `target_cambio = [var_cambio]` → câmbio endógeno via UIP a partir do 2º trimestre
  - **k > 0**: inputs explícitos para k períodos adicionais; períodos além de k+1 endógenos

#### Gráfico de câmbio nível (R$/USD) na aba Resultados

Inserido após o gráfico de Selic nível, com o mesmo padrão visual:
- **Linha verde:** câmbio simulado reconstruído a partir de `cambio_q1` acumulando `delta_e_irf + 0,25%/trimestre` (PPC de SS)
- **Linha cinza tracejada:** PPC de referência pura (0,25% a.t. a partir de `cambio_q1`)
- **Linha vertical pontilhada:** aparece no cenário alternativo com k>0, marcando onde o câmbio passa a ser endógeno

No modo padrão a linha simulada deve coincidir com a PPC. No modo endógeno (n_cambio_alt=0) o câmbio reflete a pressão da Selic via UIP sem ancoragem explícita.

#### Gráfico de expectativas redesenhado

**Antes:** barras agrupadas (prev vs curr Focus), horizonte limitado à cobertura do Focus.

**Depois:** linha (padrão Selic nível), mantendo o horizonte da cobertura do Focus:
- **Linha verde sólida:** Focus ancorado para `i < n_expec`; `prev_expec[i] + df["expectativa"].iloc[i]` (cauda endógena) para `i ≥ n_expec`
- **Linha cinza tracejada:** curva da reunião anterior (referência)
- **Linha vertical pontilhada:** marca o boundary ancorado/endógeno quando `n_expec < n_expec_plot`

O horizonte permanece limitado à cobertura do Focus (~4 trimestres) — a extensão para _horizon distorcia por falta de dados além de 4T.

#### Ponto de atenção no Brent (anotado em `exploratory/brent/memoria_brent.md`)

Constatado que o strip de Brent pode se estender além de M+6 quando há contratos halfstale no início (não contam para `active_count`). Para a reunião de Jun/2026 com cutoff 12/jun, CON6 (jul/26, expirado 29/mai) é halfstale → strip vai até jan/2027 (7 meses). Isso faz com que Q1/2027 ainda apareça "seguindo a curva" em vez de aplicar o crescimento de 2% a.a. imediatamente.

**Ambiguidade metodológica:** BCB diz "seis meses" — se significa 6 meses corridos (jul–dez), o fix é contar halfstale no `active_count`. Validação pendente contra RPM Jun/2026.

### [Início] Análise e planejamento
- Lido `mAgregado2024q2_base.mod`: 39 variáveis originais + 25 auxiliares = 64, 28 choques
- Lido `runModelo24q2_abr26.m` e `runModelo24q2_abr26_oct.m`: lógica completa dos runners
- Inspecionado `copom2026_novo.xlsx → Abr26`: mapeamento completo das colunas A-AC
- Lido código-fonte `/Applications/Dynare/4.6.4/matlab/simult_.m`: tradução Python planejada
- Inspecionado `mAgregado2024q2_base_results.mat`: todas as matrizes disponíveis via scipy.io
- **Descoberta chave**: o `.mat` já contém tudo — não precisa de script de extração separado
