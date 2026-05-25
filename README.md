# ModeloBCB — Interface Web para o Modelo Semi-Estrutural do BCB

Interface Streamlit para rodar simulações do modelo semi-estrutural de pequeno porte do Banco Central do Brasil (Relatório de Inflação 2024/Q2) **sem depender de Matlab ou Octave**. O motor de cálculo usa as matrizes de solução exportadas pelo Dynare, lidas em Python via `scipy.io`.

## Como rodar

```bash
# Instalar dependências
pip install -r requirements.txt

# Iniciar o app
streamlit run app.py
# Acessa em http://localhost:8501
```

## Estrutura de pastas

```
remote_model/
├── app.py                        ← entry point Streamlit
├── requirements.txt
│
├── modules/                      ← lógica Python (importada por app.py)
│   ├── engine.py                 ← simult_() + loop de convergência (tradução NumPy do Dynare)
│   ├── calculos.py               ← compounding K-M, tabelas de resultado, export Excel
│   ├── baseline_io.py            ← lê/salva projecoes_copom.xlsx (projeções oficiais BCB)
│   ├── mercado_io.py             ← PTAX (SGS), Selic/Focus (Olinda), IPCA/Focus, calendário Copom
│   └── brent_io.py               ← curva de Brent BCB a partir de exportação Bloomberg
│
└── data/
    ├── mAgregado2024q2_base_results.mat   ← solução Dynare (artefato principal — não editar)
    ├── projecoes_copom.xlsx               ← projeções oficiais BCB por reunião (uma aba por reunião)
    ├── Brent_full.xlsx                    ← exportação Bloomberg com contratos ICE Brent
    └── copom_calendar.json                ← datas das reuniões 2025–2026
```

> Scripts Matlab/Dynare de referência e o diário técnico do projeto são mantidos apenas localmente.

## Interface (app.py)

O dashboard tem 4 abas:

| Aba | Conteúdo |
|---|---|
| **Targets** | Trajetória da Selic (por reunião do Copom), expectativas IPCA (12m Focus) e câmbio (PTAX) |
| **Choques diretos** | Choques avulsos: preços administrados, livres, Brent, hiato, câmbio extra |
| **Baseline RPM** | Projeções oficiais do BCB carregadas do `projecoes_copom.xlsx` (somente leitura) |
| **Resultados** | Tabela e gráficos: Selic, expectativas, câmbio, IPCA, livres, administrados |

Inputs de mercado (Selic/Focus, IPCA/Focus, PTAX) são pré-preenchidos automaticamente via APIs do BCB.

## Arquitetura do motor

O arquivo `.mat` contém a solução de primeira ordem do modelo Dynare:

| Matriz | Dimensão | Conteúdo |
|---|---|---|
| `ghx` | 64×40 | Propagação pelos estados |
| `ghu` | 64×28 | Impacto dos 28 choques |
| `ys` | 64×1 | Steady state |

`engine.py` implementa `simult_()` em NumPy e um loop de convergência que ajusta os choques `eps_i` (Selic), `eps_ei` (expectativas) e `eps_e` (câmbio) até as trajetórias alvo convergirem (tolerância 1e-6 / 1e-4 / 1e-4, máximo 10.000 iterações).

## Dados de mercado (automação)

| Dado | Fonte | Módulo |
|---|---|---|
| PTAX (câmbio) | SGS série 10813 | `mercado_io.fetch_ptax_window` |
| Selic realizada | SGS série 432 | `mercado_io.get_selic_sgs432` |
| Selic Focus | Olinda `ExpectativasMercadoSelic` | `mercado_io.fetch_selic_focus_on_date` |
| IPCA 12m suavizado | Olinda `ExpectativasMercadoInflacao12Meses` | `mercado_io.fetch_ipca_12m` |
| IPCA trimestral | Olinda `ExpectativasMercadoTrimestrais` | `mercado_io.fetch_ipca_trimestral` |
| IPCA anual | Olinda `ExpectativasMercadoInflacaoAnuais` | `mercado_io.fetch_ipca_anual` |

**Regra PTAX:** média dos últimos 10 dias úteis encerrada na sexta-feira da semana anterior à do Copom, arredondada para o múltiplo de R$0,05 mais próximo.

## Curva de Brent (automação Bloomberg)

A aba **Choques diretos** inclui automação da curva de Brent do BCB:

- **Arquivo padrão:** `data/Brent_full.xlsx` — exportação Bloomberg com contratos ICE Brent (aba `copia`). Já está no repositório e é carregado automaticamente.
- **Atualização:** para usar dados mais recentes, faça upload do novo `Brent_full.xlsx` via o widget na aba, ou substitua o arquivo em `data/` e faça push.
- **Metodologia replicada:** P₀ (média 10 dias úteis no cutoff), strip M+1..M+6 com halfstale para contratos expirados, crescimento de 2% a.a. após M+6.
- **Cutoff:** sexta-feira da semana anterior à semana da reunião do Copom (derivado automaticamente do nome da reunião).
- O usuário vê e edita **níveis em USD** por trimestre; o `eps_brent` (%∆) é calculado internamente.

## Atualização do modelo

Se o arquivo `.mod` for alterado e o Dynare reprocessado, basta substituir `data/mAgregado2024q2_base_results.mat` pela nova solução. O app Python não precisa de outras mudanças.

## Calendário Copom

`data/copom_calendar.json` contém as datas das reuniões de 2025 e 2026. Atualizar com reuniões de 2027 quando o BCB publicar.

## Referências

- Relatório de Inflação BCB 2024/Q2 — especificação do modelo semi-estrutural
- [Dynare Reference Manual](https://www.dynare.org/manual/)
- API BCB Olinda: `https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/`
- SGS BCB: `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados`
