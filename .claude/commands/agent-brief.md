---
description: Gera knowledge-bases/<kb>/agent-brief.md — o contrato de comportamento para um agente conversacional ao vivo construído sobre a KB (regras invioláveis, whitelist de indicadores, política de "fora do escopo", convenções de leitura, tom de resposta). Derivação pura — não toca kb.md/kb-layer.md, não consulta Looker/Metabase/BigQuery, não roda avaliação. Uso `/agent-brief <kb> [--force]`.
argument-hint: <kb> [--force]
---

# Gerar contrato de comportamento (`agent-brief.md`)

Converte a KB de `<kb>` num contrato de comportamento para um agente conversacional que vai responder, ao vivo, perguntas de negócio sobre o painel — não sobre como escrever a SQL certa (isso é papel do `kb-evaluator`/`kb.md`), mas sobre o que responder, o que recusar, e como. **Derivação pura**: a fonte é `kb-layer.md` (preferencial) ou `kb.md` (fallback); nenhum dos dois é modificado, e o pipeline de `/create-kb`/`/run-eval` não é afetado.

Argumento: `$ARGUMENTS` → primeiro token = `<kb>`; `--force` = regravar `agent-brief.md` existente.

## Passo 1 — Validar

1. `<kb>` deve casar `^[a-z0-9-]+$`. Senão: erro e pare.
2. `knowledge-bases/<kb>/kb.md` deve existir. Senão:
   > `kb.md` não encontrado em `knowledge-bases/<kb>/`. Rode `/create-kb <kb>` primeiro.

   Pare.
3. Se `knowledge-bases/<kb>/agent-brief.md` existe e **não** veio `--force`: informe o caminho, a data de modificação e pare com a sugestão de usar `--force`.

## Passo 2 — Escolher a fonte

1. `test -e knowledge-bases/<kb>/kb-layer.md` via Bash.
2. **Se existir**: `SOURCE_PATH = knowledge-bases/<kb>/kb-layer.md`, `SOURCE_KIND = layer`. Siga ao Passo 3.
3. **Se não existir**: `AskUserQuestion` — header `"Fonte"` — pergunta `"Esta KB não tem kb-layer.md. Como seguir?"`:
   - `"Gerar kb-layer.md primeiro (recomendado — mais barato em tokens)"` → pare e informe: `"Rode /kb-layer <kb> e depois /agent-brief <kb> de novo."` (não encadeie a chamada automaticamente — o usuário decide quando).
   - `"Seguir direto no kb.md (mais caro, sem gerar o layer)"` → `SOURCE_PATH = knowledge-bases/<kb>/kb.md`, `SOURCE_KIND = kb_md`. Siga ao Passo 3.

## Passo 3 — Coletar audiência (única pergunta de negócio)

A KB não sabe para quem o agente responde — é a única coisa que só o usuário tem. `AskUserQuestion` — header `"Audiência"` — pergunta `"Quem é o público deste agente? (equipe, papel, o que eles decidem com esse número)"`:
- Texto livre (Other). Se o usuário pular/deixar vazio, `AUDIENCE = "(none)"` — o subagent marca a seção 1 como não preenchida em vez de inventar um público.

## Passo 4 — Medir a origem

```bash
wc -l "<SOURCE_PATH>"
```

Guarde `SOURCE_LINHAS` — é o contrato de completude conferido no Passo 6.

## Passo 5 — Disparar o `agent-brief-writer`

Uma única instância, via Agent tool, `subagent_type: "agent-brief-writer"`, com este prompt exato:

```
KB_NAME: <kb>
SOURCE_PATH: <SOURCE_PATH>
SOURCE_KIND: <layer|kb_md>
TARGET_PATH: knowledge-bases/<kb>/agent-brief.md
AUDIENCE: <texto coletado no Passo 3, ou "(none)">
OVERWRITE: true
```

Avise o usuário antes: `"Gerando contrato de comportamento para <kb> a partir de <SOURCE_PATH> (<SOURCE_LINHAS> linhas)."`

Parse a **última linha** da resposta como JSON (tolerante a wrapper ```` ```json ````). `status != "ok"` → mostre `reason` e pare.

## Passo 6 — Conferir

**Leitura íntegra**: `linhas_lidas` do JSON dentro de ±1 de `SOURCE_LINHAS`. Divergiu → avise que a geração pode ter sido feita sobre leitura parcial (flag, não abort).

Não há checker determinístico dedicado (diferente do `/kb-layer`) — este artefato é texto de comportamento, não estrutura endereçável por id. A prova de completude é a leitura íntegra da fonte + a contagem de seções do próprio JSON.

## Passo 7 — Resumo

```
Contrato de comportamento gerado — knowledge-bases/<kb>/agent-brief.md

  fonte        <SOURCE_KIND> · <SOURCE_LINHAS> linhas
  audiência    <preenchida | não informada>
  seções       <n> fora do escopo · <n> whitelist · <n> armadilhas · <n> divergências

<avisos do JSON, um por linha, se houver>
```

Depois, uma linha de contexto: `agent-brief.md` é derivado e pode ser regerado com `/agent-brief <kb> --force` sempre que `kb.md`/`kb-layer.md` mudar de forma relevante para comportamento (indicador liberado/retirado do painel, nova convenção de sinal).

## Regras invioláveis

1. **Nunca modifique `kb.md` ou `kb-layer.md`.** O comando só escreve em `agent-brief.md`.
2. **Nunca passe `questions.public.json`, `questions.secret.json` ou caminhos de `results/`** ao subagente — comportamento de agente não tem relação com o benchmark (Invariante #1 do CLAUDE.md).
3. **Nunca chame Looker/Metabase/BigQuery** aqui. Conteúdo novo entra pelo `/create-kb`, não por este comando.
4. **Não existe promoção.** `agent-brief.md` não tem um "canônico" a substituir — é sempre o artefato mais recente gerado. Não há champion-vs-candidate aqui.
5. **A escolha de fonte (Passo 2) é do usuário, não automática.** Não gere `kb-layer.md` "de passagem" sem perguntar — o usuário pode preferir não pagar esse custo agora.
