# Como rodar no Octave

## Pré-requisitos

### 1. Octave
Instalar via Homebrew (macOS):
```bash
brew install octave
```

### 2. Dynare compatível com Octave
Baixar o instalador em **dynare.org → Downloads** e escolher a versão para Octave (mesma versão que você usa no MATLAB, se aplicável). No macOS com Homebrew:
```bash
brew install dynare
```
Ou baixar o `.pkg` do site e instalar manualmente.

### 3. Pacote `io` do Octave (necessário para escrever Excel)
Dentro do Octave, executar **uma única vez**:
```octave
pkg install -forge io
```

---

## Configurar o path do Dynare

O Octave precisa saber onde está o Dynare. Adicionar ao arquivo de inicialização `~/.octaverc`:
```octave
addpath('/usr/local/lib/dynare/matlab')   % ajustar para o caminho real da instalação
```

Para descobrir o caminho correto após instalar:
```bash
find /usr/local -name "dynare.m" 2>/dev/null
```

---

## Rodar

No terminal, abrir o Octave na pasta do projeto e executar:
```bash
cd /caminho/para/Modelo24Q2_octave
octave --no-gui runModelo24q2_abr26_oct.m
```

Ou interativamente dentro do Octave:
```octave
cd /caminho/para/Modelo24Q2_octave
run runModelo24q2_abr26_oct.m
```

---

## Saída

Os resultados são gravados em `copom2026_novo.xlsx`, aba `Abr26`, com 9 variáveis:
`it`, `expectativa`, `delta_e`, `ht`, `ICbr`, `Brent`, `IPCA`, `Livres`, `Administrados`.

---

## Problemas comuns

| Erro | Causa provável | Solução |
|---|---|---|
| `dynare: command not found` | Dynare não está no path | Verificar `addpath` no `.octaverc` |
| `xlswrite: undefined` | Pacote `io` não instalado | `pkg install -forge io` |
| `error: pkg load io` | Pacote instalado mas não carregado | O script já faz `pkg load io`; verificar se a instalação foi bem-sucedida com `pkg list` |
| Erro de compilação do `.mod` | Versão do Dynare incompatível | Usar Dynare 5.x ou superior |
