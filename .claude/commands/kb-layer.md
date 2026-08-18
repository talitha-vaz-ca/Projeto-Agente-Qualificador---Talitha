---
description: Converte um kb.md já validado para a camada semântica estruturada kb-layer.md (blocos ```yaml meta``` endereçáveis por id). Derivação pura — não toca o kb.md, não consulta Looker/Metabase/BigQuery, não roda avaliação. Uso `/kb-layer <kb> [--force]`.
argument-hint: <kb> [--force]
---

# Gerar camada semântica da KB (`kb-layer.md`)

Converte `knowledge-bases/<kb>/kb.md` (prosa + queries validadas) em `knowledge-bases/<kb>/kb-layer.md` (grafo de entidades endereçáveis). **Derivação pura**: a fonte é o `kb.md`, nada mais. O `kb.md` **não é modificado** e o pipeline de `/create-kb` / `/run-eval` **não é afetado** — o layer é um artefato novo, regenerável a qualquer momento.

Argumento: `$ARGUMENTS` → primeiro token = `<kb>`; `--force` = regravar `kb-layer.md` existente.

## Passo 1 — Validar

1. `<kb>` deve casar `^[a-z0-9-]+$`. Senão: erro e pare.
2. `knowledge-bases/<kb>/kb.md` deve existir. Senão:
   > `kb.md` não encontrado em `knowledge-bases/<kb>/`. Rode `/create-kb <kb>` primeiro.

   Pare.
3. Se `knowledge-bases/<kb>/kb-layer.md` existe e **não** veio `--force`: informe o caminho, a data de modificação e pare com a sugestão de usar `--force`.

## Passo 2 — Medir a origem

```bash
wc -l "knowledge-bases/<kb>/kb.md"
grep -c '^```sql' "knowledge-bases/<kb>/kb.md"
test -e "knowledge-bases/<kb>/intents.json" && echo has-intents
```

Guarde `KB_LINHAS` e `SQL_ORIGEM`. São o contrato de completude conferido no Passo 4.

## Passo 3 — Disparar o `kb-restructurer`

Uma única instância, via Task tool, `subagent_type: "kb-restructurer"`, com este prompt exato:

```
KB_NAME: <kb>
SOURCE_PATH: knowledge-bases/<kb>/kb.md
TARGET_PATH: knowledge-bases/<kb>/kb-layer.md
INTENTS_PATH: knowledge-bases/<kb>/intents.json   (ou "(none)" se não existir)
OVERWRITE: true
```

Avise o usuário antes: "Convertendo `<kb>` (`KB_LINHAS` linhas, `SQL_ORIGEM` queries) — leva alguns minutos."

Parse a **última linha** da resposta como JSON (tolerante: se vier envolto em ```` ```json ````, extraia). `status != "ok"` → mostre o `reason` e pare.

## Passo 4 — Conferir

1. **Leitura íntegra**: `kb_linhas_lidas` do JSON dentro de ±1 de `KB_LINHAS`. Divergiu → avise que a conversão pode ter sido feita sobre leitura parcial (flag, não abort).
2. **Integridade estrutural** — rode o checker determinístico:
   ```bash
   python scripts/check-kb-layer.py "knowledge-bases/<kb>/kb-layer.md" "knowledge-bases/<kb>/kb.md"
   ```
   Ele valida, de forma determinística: `kind` entre os 7 válidos (`expectation` é reprovado), `id` no formato esperado e único, referências resolvidas (em lista inline **e** em lista YAML em bloco) e **cada query do `kb.md` presente verbatim** no layer — identidade, não contagem. Exit != 0 → mostre a saída inteira.

   **Guarde a linha `blocos por kind:` da saída** — é ela que alimenta o Passo 5.
3. **Correção dirigida (1 rodada, só se o checker reprovou)**: dispare o `kb-restructurer` de novo com o mesmo prompt **mais** a linha:
   ```
   VIOLACOES: <saída literal do checker>
   ```
   Rode o checker outra vez. Se reprovar de novo, **não insista** — entregue o arquivo com o aviso de que ficou com violações pendentes e liste-as.

## Passo 5 — Resumo

As contagens de entidades vêm da linha `blocos por kind:` do **checker** (medidas no arquivo em disco), **nunca** do campo `emitidos` do JSON do agente — esse é auto-reportado e pode divergir do que foi de fato gravado. Mesmo padrão do Invariante #4 do CLAUDE.md: o subagente devolve prova, o orquestrador valida. Do JSON só saem os `avisos`.

Imprima:

```
Camada semântica gerada — knowledge-bases/<kb>/kb-layer.md

  origem      <KB_LINHAS> linhas · <SQL_ORIGEM> queries
  entidades   <n> source · <n> policy · <n> measure · <n> report · <n> pitfall · <n> term · <n> note
  integridade <OK | N violações> · SQL <n>/<SQL_ORIGEM> verbatim

<avisos do JSON, um por linha, se houver>
```

Depois, uma linha de contexto: o `kb.md` segue intacto e canônico; o `kb-layer.md` é derivado e pode ser regerado com `/kb-layer <kb> --force` sempre que o `kb.md` mudar.

> **Consumidor:** o `/agent-brief <kb>` lê o `kb-layer.md` (quando existir) em vez do `kb.md` inteiro — é uma fonte mais barata em tokens porque é estruturada e sem a prosa/narrativa longa do `kb.md`. Isso não afeta o `kb-evaluator`/`run-eval` (que continuam lendo `kb.md`, Invariante #8 do CLAUDE.md) — é um consumidor novo, não uma substituição. Por isso, se o `kb.md` mudar de forma relevante para comportamento de agente (novo indicador liberado/retirado do painel, nova política de sinal), vale regerar com `--force` antes de rodar `/agent-brief`.

## Regras invioláveis

1. **Nunca modifique o `kb.md`.** O comando só escreve em `kb-layer.md`.
2. **Nunca passe `questions.public.json`, `questions.secret.json` ou caminhos de `results/`** ao subagente — ele não tem nada a ver com o benchmark (Invariante #1 do CLAUDE.md).
3. **Nunca chame Looker/Metabase/BigQuery** aqui. Conteúdo novo entra pelo `/create-kb`, não por este comando.
4. **Nunca promova o layer a `kb.md`.** Se um dia a camada estruturada for virar canônica, isso passa pelo champion-vs-candidate do `/create-kb` (Invariante #2) — é decisão de projeto, não deste comando.
