# CLAUDE.md

Contexto para o Claude trabalhar neste repositório sem regredir invariantes. Complementa o [README.md](README.md) — não duplica o passo-a-passo de uso.

## O que é

`kb-manager` é um plugin do Claude Code que constrói, versiona e avalia **Knowledge Bases sobre dados** (data dictionaries + perguntas-benchmark). Ciclo coberto por três slash commands: `/create-kb` (build/update), `/run-eval` (avaliação; `--quick` = check diário binário vs última run verde) e `/eval-report` (relatório histórico — leitura pura, gera HTML para gestores).

Stack: slash commands + subagents Claude (`kb-builder`, `question-creator`, `kb-prober`, `kb-evaluator`) + 3 MCPs Python locais (`bq_local`, `looker_local`, `metabase_local`) que falam com BigQuery, Looker e Metabase. Perguntas vivem em **duas faces** (pública/secreta) e o gabarito é executado por um ator isolado — a tool MCP `execute_gabarito` (do `bq_local`) — ver Invariante #1 e #7.

## Invariantes — NÃO QUEBRAR

Estes são os "porquês" do design. Mudar qualquer um exige discussão explícita com o usuário antes.

### 1. Isolamento do gabarito — duas faces + `question-creator` NUNCA lê `kb.md`

Duas blindagens contra o mesmo risco (o benchmark "casar" com a KB e a avaliação medir cópia em vez de qualidade):

**(a) `question-creator` NUNCA lê `kb.md`.** O agente de perguntas chama os MCPs Looker/Metabase **diretamente**, não derivado da KB. Motivo: se perguntas saíssem do `kb.md`, KB e benchmark evoluiriam juntos ("alvo móvel") e seria impossível medir melhoria entre versões. Se for tentado refatorar para "reaproveitar contexto", **pare e confirme**.

**(b) Perguntas vivem em DUAS FACES; o orquestrador lê só a pública.** O `questions.json` único foi dividido (separação **física**, não instrucional) em:
- **`questions.public.json`** — array de `{ id, pergunta }`. **Única** face que o orquestrador (`/run-eval`, `/create-kb`) lê para montar o prompt do avaliador — `kb-evaluator` (A/B do `/create-kb` e certificação `/run-eval`) ou `kb-prober` (loop de afinação do `/create-kb`).
- **`questions.secret.json`** — `{ id, gabarito_sql, resposta_esperada_unidade, esperava_encontrar, tolerancia_relativa, _* }`. Lida **exclusivamente** pela tool MCP `execute_gabarito` (do `bq_local`, server-side); **nunca** por quem monta o prompt do avaliador.

Motivo do design: enquanto o gabarito estivesse ao alcance de quem prepara o prompt, o vazamento era possível (e aconteceu — o orquestrador codificou a fórmula da resposta no prompt do avaliador, com colunas inexistentes no `kb.md`). A separação em faces + a ordem do fluxo (avaliadores **antes** de `execute_gabarito`) tornam isso fisicamente impossível: no momento da montagem do prompt, o orquestrador nunca viu a `gabarito_sql`.

Aplicação prática:
- `question-creator.md` não recebe e não deve ler `KB_PATH`; grava as **duas faces** (gabarito só na secreta).
- O orquestrador **nunca** abre `questions.secret.json`. A `gabarito_sql`/`valor_gabarito` chegam só como **retorno de `execute_gabarito`**, e só depois que os avaliadores responderam.
- **A KB chega ao `kb-evaluator` por uma cópia isolada.** O orquestrador faz `cp` do `kb.md` para um diretório de scratch e passa ao avaliador **só o caminho da cópia** (`KB_FILE`) — **nunca** `KB_DIR`, o slug, ou qualquer caminho sob `knowledge-bases/`. **O diretório de scratch tem nome opaco e caminho nativo do SO — gere com `python tempfile.mkdtemp()`, não `mktemp -d` (no Git Bash devolve caminho POSIX `/tmp/...` que a tool `Read` não abre): o slug não pode aparecer embutido no `KB_FILE` (nome do dir ou do arquivo), senão vaza pelo caminho** — foi um furo real corrigido (o scratch chegou a se chamar `eval-<slug>`). O avaliador lê a cópia via `Read` (ganhou essa tool). Como ele não recebe onde a KB "mora", **não consegue localizar** `questions.secret.json` (que fica só no `KB_DIR`). É **isolamento na prática** (o avaliador não sabe o caminho do segredo), não "por construção" — foi a escolha deliberada vs. um MCP dedicado, por ser mais leve e não tocar o frágil `setup-mcp.sh`. Antes espelhava o `golden-runner` (subagente que lia a face secreta via `Read`); hoje a face secreta é lida **server-side** pela tool MCP `execute_gabarito`, então o isolamento **do gabarito** virou **por construção** (o orquestrador não tem como abrir o arquivo), enquanto o **da KB** para o avaliador segue **na prática**. Ver Invariante #8. Se for tentado passar `KB_DIR`/slug ao avaliador, ou embutir o conteúdo da KB no prompt de novo: **pare e confirme**.
- Em `--regenerate-questions`, perguntas vêm das fontes novamente, não do `kb.md` atual.
- Default em update de KB existente: **manter as faces intactas** (alvo fixo).
- Se for tentado voltar a um `questions.json` único, ou pôr `gabarito_sql` na face pública, ou ler a secreta no orquestrador: **pare e confirme**.

### 2. Champion-vs-candidate é o caminho de update

Em `/create-kb` com `kb.md` existente, o builder escreve em `kb-candidate.md` (nunca sobrescreve direto). Avalia ambos com as MESMAS perguntas (mesma face pública; mesmo `valor_gabarito` de `execute_gabarito`, computado uma vez), mostra diff e pergunta se promove. Se for tentado "atualizar in-place", **pare e confirme**.

### 3. Claude principal é o ÚNICO ponto de interação com o usuário

Subagents (`kb-builder`, `question-creator`, `kb-evaluator`) **não fazem AskUserQuestion** (e a tool `execute_gabarito` tampouco interage — devolve só dados). O orquestrador coleta tudo upfront e passa via prompt estruturado. Não adicione `AskUserQuestion` na definição de subagent.

### 4. `kb-evaluator` e `execute_gabarito` retornam prova de execução

Saída obrigatória do `kb-evaluator` inclui `sql_executado`, `bytes_processed`, `job_id` (prova de execução de SQL) e `kb_linhas_lidas` + `kb_ultima_linha` (prova de que leu a KB **inteira e até o EOF** — ver Invariante #8). Se algum dos três primeiros vier `null` quando `encontrada: true`, o subagente alucinou — `/run-eval` valida via `execucao_ok` e reprova. O gabarito (`execute_gabarito`) é validado do mesmo jeito: `gabarito_ok` só vale com `gabarito_job_id`/`gabarito_bytes` reais. A **validação de prova** continua servindo à auditoria; e como o gabarito agora roda em **código** (não num LLM), o determinismo é garantido pela própria execução (código não regenera a SQL nem alucina) — não relaxe nenhuma delas.

### 5. BigQuery é read-only

MCP `bq_local` usa `execute_sql_readonly`. Nunca trocar por `execute_sql` (que permite escrita). Se precisar de escrita, é caso novo — discutir antes.

### 6. Observabilidade é camada não-invasiva por cima do núcleo

Snapshots são `{ meta, results }` (o array por-pergunta vai em `results`, **inalterado**); `results/_index.json` é append-only e **derivado** (reconstruível varrendo snapshots; falha de escrita nunca aborta); `/eval-report` é **leitura pura** (sem agentes, sem BigQuery). Hashes (incluindo `kb_prompt_hash`/`kb_integra` do Invariante #8) e índice nunca abortam uma run. O **carimbo de `meta`/hashes é feito pelos orquestradores** — não mova lógica de observabilidade para dentro dos subagents nem torne a escrita do índice fatal.

> Nota: a entrega da KB por cópia isolada (Invariante #1/#8) **tocou** o `kb-evaluator` (ganhou `Read` e passou a buscar a KB) — mas isso é mudança de **canal de entrada**, não de observabilidade. O carimbo de `meta`/hashes continua no orquestrador; o avaliador só **devolve um campo de prova** (`kb_linhas_lidas`), no mesmo padrão "subagente retorna prova, orquestrador valida" do Invariante #4.

### 7. Gabarito dinâmico — a verdade é a `gabarito_sql` executada na run, por um ator isolado

Cada pergunta carrega uma `gabarito_sql` (query canônica, na **face secreta**). A "resposta certa" **não** é um número estático — é o resultado de rodar essa SQL **na própria run**, contra o BigQuery ao vivo. Motivo: o banco sofre **atualização retroativa**, então um valor congelado fica errado sem a KB ter piorado. Regras que sustentam o design (mudar exige discussão):

- A `gabarito_sql` é executada **verbatim pela tool MCP `execute_gabarito`** (`/run-eval` Passo 5, `/create-kb` Passo 6d), que reusa `execute_sql_readonly` — **nunca regenerada, corrigida ou otimizada** em runtime. `execute_gabarito` é o **único** ator que lê a face secreta (server-side, dentro do `bq_local`); o orquestrador **não** executa o gabarito e nunca o lê do disco. Determinismo é garantido pela execução em **código** + a **validação de prova** (`gabarito_job_id`/`gabarito_bytes`, Invariante #4), não por "quem executou". Falha de execução vira `status = "erro_gabarito"`, **não** reprovação do candidato.
  > Mudança de design (autorizada): (1) antes o orquestrador executava o gabarito inline; (2) depois foi movido para o subagente `golden-runner` (isolamento físico); (3) **agora** foi movido para a tool MCP `execute_gabarito` (do `bq_local`), que lê a face secreta server-side e roda a SQL por código. Motivo do passo (3): o passo era puramente mecânico e o subagente custava ~1,1M tokens/run — a tool zera esse custo, torna a leitura do segredo **por construção** e o determinismo garantido por código. O orquestrador que monta o prompt do avaliador nunca segura a `gabarito_sql`. Reverter para execução no orquestrador reabre o vetor de vazamento do Invariante #1.
- A `gabarito_sql` **NUNCA** entra no prompt do `kb-evaluator`. O candidato chega ao número só pela KB — senão a avaliação vira cópia. (Reforça o Invariante #1.)
- A ordem do fluxo é parte do invariante: **avaliadores primeiro** (a partir da face pública), **`execute_gabarito` depois**. O gabarito só entra no contexto do orquestrador (como retorno de `execute_gabarito`, para gravar no snapshot) **após** os prompts dos avaliadores já terem sido enviados.
- `valor_gabarito` é gravado no snapshot (auditável via `gabarito_job_id`) mas **varia entre runs** por design. A comparação longitudinal é por **`status` por pergunta**, não por valor absoluto — por isso o status fica estável apesar do drift de dados.
- O `question-creator` gera `gabarito_sql` (extraída da SQL real das fontes — `query.sql` do Looker/Metabase nativo — e **validada no BigQuery read-only**, Passo 5.5; gabarito que não valida é descartado) e a grava **só na face secreta**. Para isso usa `mcp__bq_local__execute_sql_readonly` (read-only — não fere o Invariante #5) só para validação; continua **sem ler `kb.md`** (Invariante #1 intacto).

### 8. Verificação de KB íntegra — cópia por `cp` + `kb_linhas_lidas` + marcador de EOF

O avaliador (`kb-evaluator`) precisa usar o `kb.md` **inteiro** (já foi violado uma vez por um recorte de KB por pergunta). Mecanismo de entrega (ver Invariante #1): o orquestrador **não reescreve a KB no prompt** — faz `cp` do `kb.md` para uma cópia de scratch e passa só o caminho; o avaliador a lê via `Read`. Assim o orquestrador nunca manipula o conteúdo da KB (não há como truncá-la/resumi-la ao montar o prompt). Três checagens, gravadas no `meta`:

- `kb_hash` — sha256(16) do `kb.md` **em disco**.
- `kb_prompt_hash` — sha256(16) da **cópia de scratch** que o avaliador leu (`KB_FILE`). Como veio de `cp`, bate com `kb_hash` por construção; hashear a cópia é honesto (é o que foi lido).
- `kb_linhas_lidas` — campo de **prova** devolvido por cada avaliador (Invariante #4): quantas linhas ele de fato leu. O orquestrador compara com `KB_LINHAS` (`wc -l` do `kb.md` da run, tolerância ±1 por slop de convenção `wc -l`/`Read`). Sinal **grosso** de tamanho lido — não prova EOF sozinho.
- `kb_ultima_linha` — campo de **prova** de leitura **até o fim**: a última linha não-vazia da KB (`strip`, ≤120 chars) que cada avaliador reportou. O orquestrador compara com `KB_ULTIMA_LINHA` (mesmo marcador computado do `kb.md` da run). Prova forte de EOF: um avaliador que só leu o começo não acerta a última linha. O esperado **nunca** entra no prompt do avaliador, e vem da KB (conteúdo público), **nunca** da face secreta → não afeta o isolamento do gabarito. Gravado em `meta.kb_ultima_linha_esperada`.
- `kb_integra = (kb_prompt_hash == kb_hash) E (todos os avaliadores com kb_linhas_lidas dentro de ±1 de KB_LINHAS) E (todos com kb_ultima_linha == KB_ULTIMA_LINHA)`.

Se `kb_integra == false`, a run é marcada **suspeita** — causas: cópia corrompida/errada (hash difere), leitura parcial (linhas divergem) **ou** leitura que não chegou ao fim (última linha diverge). O `/run-eval` sinaliza na saída. Fallback: sub-checagem que não computa vira `"unknown"` (não força `false`); se nenhuma for conclusiva, `kb_integra = null`. **Nunca aborta por isso** — é flag, não gate. Em `/create-kb` há um trio por lado (champion e candidate, cada um: hash da sua cópia + `kb_linhas_lidas` vs `KB_LINHAS` + `kb_ultima_linha` vs `KB_ULTIMA_LINHA` daquele lado).

> **Nível de garantia (honesto):** o isolamento do segredo aqui é **na prática** (o avaliador tem `Read`, mas não recebe `KB_DIR`/slug, então não localiza `questions.secret.json`), não "por construção". A alternativa "por construção" (MCP `get_kb` sem filesystem) foi descartada por tocar o frágil `setup-mcp.sh`. Se algum dia o isolamento por construção virar requisito, o caminho é o MCP — **discuta antes**.
 
## Layout (o que é versionado vs gerado)

**Versionado:**
- `.claude/commands/*.md` — slash commands (project-local)
- `.claude/agents/*.md` — subagents (project-local)
- `.claude-plugin/plugin.json` — manifesto
- `.claude-plugin/mcps/<name>/{server.py,requirements.txt}` — **source-of-truth dos MCPs**
- `knowledge-bases/<kb>/{kb.md,questions.public.json,questions.secret.json}` — KBs (conteúdo curado; perguntas em duas faces, ver Invariante #1)
- `scripts/sync-repos.sh`, `setup-mcp.sh`, `.env.example`

**Gerado (gitignored — ver [.gitignore](.gitignore)):**
- `mcp-bq/`, `mcp-looker/`, `mcp-metabase/` — instâncias instaladas (venvs) pelo `setup-mcp.sh`. **Editar aqui é inútil** — `setup-mcp.sh` sobrescreve a partir de `.claude-plugin/mcps/`. Sempre edite o source-of-truth.
- `repos/` — clones LookML + Dataform via `sync-repos.sh`
- `knowledge-bases/*/results/` — snapshots de avaliação (`{ meta, results }`) + `_index.json` (índice append-only, derivado)
- `knowledge-bases/*/reports/` — HTML gerado por `/eval-report` (derivado, regenerável)
- `.env` — secrets

**Backups (no disco, sem rotação):**
- `kb.md.bak.<ts>` ao promover candidate
- `questions.public.json.bak.<ts>` e `questions.secret.json.bak.<ts>` ao `--regenerate-questions` (carimbo `<ts>` compartilhado entre as duas faces)
- `questions.json.bak.<ts>` legado — backup do `questions.json` único na migração para faces (não regenerado)
- `kb-candidate.md` é efêmero — em execução interrompida fica órfão; `/create-kb` no Passo 1a trata.

## Convenções

- **Slug de KB**: `[a-z0-9-]+` (minúsculas, dígitos, hífens). Sem espaços, acentos, underscores. Validado no Passo 1 de `/create-kb`.
- **Idioma**: tudo em português (commands, agents, README, mensagens). Manter consistência.
- **Snapshots `results/`** (formato `{ meta, results }`; `meta` carrega `kb`, `run_id`, `kb_hash`, `kb_prompt_hash`, `kb_integra`, `kb_ultima_linha_esperada`, `questions_hash`, `mode`, agregados `aprovados`/`reprovados`/`erros_gabarito`/`total`/`confianca_media` e `bytes_total`):
  - `<ts>.json` = canônico (`mode` `full`/`quick`; de `/run-eval` ou pós-promoção)
  - `<ts>.champion.json` / `<ts>.candidate.json` = staging do champion-vs-candidate (`mode` `champion`/`candidate`); na consolidação viram `<ts>.json` com `mode` reescrito p/ `full`
  - `_index.json` = índice append-only (uma entrada `meta` por run canônica; staging **não** entra). Derivado: reconstruível; falha de escrita nunca aborta
  - Hashes = sha256 (16 chars); `"unknown"` se falhar. `kb_hash` = de `kb.md` em disco; `kb_prompt_hash` = da **cópia de scratch** que o avaliador leu (Invariante #8); `questions_hash` = da **face secreta** (`questions.secret.json` — é ela que define a identidade do benchmark). Cada item de `results` também traz `kb_linhas_lidas` e `kb_ultima_linha` (prova de leitura íntegra + EOF; conferidos contra `KB_LINHAS`/`meta.kb_ultima_linha_esperada`)
  - Cada item de `results` tem 3 `status` possíveis: `aprovado` / `reprovado` / `erro_gabarito` (gabarito não executou). Campos de gabarito por item (vindos de `execute_gabarito`): `gabarito_sql`, `valor_gabarito`, `gabarito_job_id`, `gabarito_bytes`, `gabarito_ok`. `bytes_total` soma candidato **+** gabarito.
  - Snapshots antigos (array nu, sem `meta`/`kb_prompt_hash`, ou com `resposta_esperada_valor`) são tolerados na leitura e **nunca** reescritos
- **Datas relativas** em prompts/AskUserQuestion: sempre converter para absoluto antes de gravar em `kb.md`.

## Workflow de mudanças comuns

| Quero mudar… | Onde mexer |
|---|---|
| Comportamento de `/create-kb`, `/run-eval` ou `/eval-report` | `.claude/commands/<cmd>.md` |
| Visual/layout do relatório HTML | template (CSS+JS inline) no corpo de `.claude/commands/eval-report.md` |
| Comportamento de um subagent | `.claude/agents/<agent>.md` |
| Schema/lógica de um MCP | `.claude-plugin/mcps/<name>/server.py` → depois rodar `./setup-mcp.sh` |
| Dependências de um MCP | `.claude-plugin/mcps/<name>/requirements.txt` → `./setup-mcp.sh` |
| Lista de repos sincronizados | `.env` (`KB_GITHUB_ORG`, `KB_GITHUB_REPOS`) |
| Permissões pré-aprovadas | `.claude/settings.json` (compartilhadas) · `.claude/settings.local.json` (locais da máquina, gitignored — paths absolutos, liberadas p/ multi-KB) |
| Manifesto do plugin (commands/agents/mcps declarados) | `.claude-plugin/plugin.json` |

Após editar MCP source, **sempre** rode `./setup-mcp.sh` — instâncias em `mcp-*/` são cópias.

Após mudar `.claude/commands/`, `.claude/agents/` ou `.claude-plugin/plugin.json`: o usuário precisa **reiniciar o Claude Code** para o plugin recarregar. Avise quando for o caso.

## Armadilhas conhecidas

- **MCPs não respondem**: 9/10 vezes é credencial vazia no `.env` ou `gcloud auth application-default login` expirada. Não tente "corrigir" o código do MCP antes de verificar credenciais.
- **`gh repo clone` falha em `sync-repos.sh`**: SSO da org não autorizado. `gh repo view <ORG>/<repo>` aciona o prompt. Não é bug do script.
- **Pergunta histórica que sempre passou reprovou**: pode ser drift de dados no BigQuery (legítimo — atualize `kb.md`) ou tolerância apertada demais. Não relaxe tolerância sem confirmar.
- **`kb-candidate.md` órfão**: execução anterior interrompida. `/create-kb` Passo 1a trata com AskUserQuestion (descartar/usar/abortar). Não delete preventivamente.
- **Editar `mcp-bq/server.py` direto**: será sobrescrito no próximo `setup-mcp.sh`. Sempre `.claude-plugin/mcps/bq/server.py`.
- **Alerta de regressão sumiu / aparece `ℹ alvo móvel`**: se `questions_hash` mudou entre runs (ex.: `--regenerate-questions`), a comparação por pergunta é suprimida **de propósito** (alvo móvel) — não é bug. Para voltar a comparar, mantenha as faces (`questions.secret.json`) fixas entre runs.
- **`/eval-report` diz "Nenhuma avaliação encontrada"**: não há snapshots em `results/`. Rode `/run-eval <kb>` primeiro. Apagar `_index.json` **não** perde histórico — ele é reconstruído varrendo os snapshots.
- **`/run-eval` diz "questions.secret.json ausente" ou "questions.json legado detectado"**: a KB ainda não tem as duas faces. Rode `/create-kb <kb> --regenerate-questions` para gerar/migrar. O `/run-eval` **não** lê o `questions.json` único antigo (a separação em faces é pré-requisito do isolamento do gabarito, Invariante #1).
- **Run marcada "suspeita" / `kb_integra: false`**: ou a cópia de scratch da KB não bateu com o `kb.md` em disco (`kb_prompt_hash != kb_hash` — `cp` errado/corrompido), ou algum avaliador leu a KB parcialmente (`kb_linhas_lidas != KB_LINHAS`). Não é abort; é flag. Investigue antes de confiar no placar e rode de novo.
- **`projectId` default `contaazul-ssbi`**: é um default com override ("1ª parte do FQN") usado em `/run-eval`, `execute_gabarito`, `kb-evaluator` e `question-creator`. Uma KB em outro projeto GCP funciona **se** os FQN nas SQLs carregarem o projeto. Se for criar KB multi-projeto e o default atrapalhar, é caso de tornar o projeto configurável por-KB — discuta antes (não espalhe mais hardcode).

## Como começar a trabalhar aqui

Antes de mexer em algo, leia na ordem:
1. [README.md](README.md) — uso e arquitetura geral
2. [.claude-plugin/plugin.json](.claude-plugin/plugin.json) — o que é exposto e como
3. O command/agent específico que vai tocar — eles têm o contrato completo no próprio frontmatter + corpo

Convenção do projeto: commands e agents são **auto-contidos e prescritivos** (não dependem de docs externas para funcionar). Se precisar adicionar contexto que vale para todos, é candidato a este CLAUDE.md — não a redundar em cada agente.
