# Northstar
> Gerado em 2026-08-07
> Período de referência: Último mês fechado = 2026-07-01 a 2026-07-31 (data de hoje: 2026-08-07)
> Fontes:
> - Looker: https://contaazul.cloud.looker.com/dashboards/onboarding_data_mart::north_star_onboarding_cs (dashboard LookML "North Star · Onboarding/CS", substitui o antigo dashboard numérico `1199` — ver nota de reescopo na seção 4)
> - Metabase: —
> - Código autoritativo: `repos/looker/1-ONBOARDING/` (LookML) + `repos/gcp-dataform-contaazul/definitions/` (Dataform/BigQuery)

## 1. Visão Geral

Esta KB documenta o painel **North Star da operação de Customer Success PME** da Conta Azul. **Fonte atual (reescopada nesta revisão): dashboard LookML `onboarding_data_mart::north_star_onboarding_cs`** — substitui o dashboard numérico legado `1199` ("North Star · Onboarding/CS · v1"). Ver nota de reescopo na seção 4 para o motivo da troca e o que pôde/não pôde ser confirmado tile a tile. O painel é um "tabelão" em formato **longo**: uma linha por `(safra, dia, bloco, ordem, metrica)`, com `meta`, `realizado`, `vs target`, `realizado do mês anterior` e `Δ mês anterior`. Os tiles do dashboard são **o mesmo explore** (`onboarding_data_mart / north_star_tabelao`) filtrado por `bloco` — ou seja, **toda** a lógica de cálculo mora numa única derived table LookML (`north_star_tabelao.view.lkml`, 1370 linhas), que é a fonte de verdade das fórmulas e **não muda** com a troca de dashboard.

O modelo operacional que o painel mede: evoluir de "ativação técnica" (implantação) para **recorrência de uso sustentável (Adoção)**. Times: **ISM** (16 pessoas — setup e treinamento, 0 a 15 dias após a contratação), **CSM** (3 pessoas — adoção, monitora uso até D90), **Tools** (1 pessoa — automações/CRM). A régua central de risco é **5 dias úteis consecutivos sem usar nenhuma feature do ERP**.

Cobertura por bloco do painel: **1** Logo Churn/Winback · **1B** Renewal (placeholder vazio) · **2** Cobertura de Onboarding (funil) · **3** # Cobertura (HC)/capacity · **4** Tempo de Cobertura (D15) · **5** Qualidade de Setup (+ **5b/5c** teste A/B Controle vs. Variante) · **6** Adoção/Carteira · **7** Tratativas WhatsApp+E-mail · **8** Tratativas Reunião · **9** Contatos sem resposta · **10** Conversão do status "Abandono".

**Convenção de leitura do painel** (vale para todos os blocos):
- `safra` = **mês de aquisição** do cliente (`DATE_TRUNC(MIN(metric_date do NEW), MONTH)`), não o mês do evento.
- `dia` = **data de referência**: quase toda métrica é **acumulada "até a data"** dentro da safra, reconstruída dia a dia. Números baixos no início da safra são esperados, **não são bug**.
- `realizado` (a coluna do KPI) = valor no **último dia disponível dentro do mês da safra** — é isso que as queries desta KB reproduzem com `@fim`.
- `vs target` tem **duas convenções**: Bloco 1 (KPI negativo) = `meta ÷ realizado` (≥100% é bom, sobrou budget de churn); todos os outros blocos (KPI positivo) = `(realizado ÷ meta) − 1`.
- O escopo do projeto começa em **2026-06-01** (piso fixo, hardcoded em todas as views). Não há safra anterior a junho/2026 e o `Δ mês anterior` de junho vem vazio por isso.

## 2. Tabelas e Schemas

Projeto BigQuery padrão: **`contaazul-ssbi`** (exceto onde indicado). Todas as consultas são read-only.

### `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
- Ciclo de vida diário das assinaturas — é a **fonte da safra/aquisição e da base de assinantes**. Tabela grande (~193M linhas / ~14GB por varredura completa, conforme comentário de otimização na LookML): sempre filtre por `metric_date`.
- Campos principais: `contaazul_company_id`, `metric_date` (DATE), `metric_type` (`NEW`, `CHURN`, `WINBACK`, `OVERDUE`, …), `channel` (`SMB` = Venda Direta, `ACC` = Recomendação), `is_freemium` (BOOL).
- Filtro-base usado em **todas** as views North Star: `channel IN ('SMB','ACC') AND is_freemium = FALSE`.
- Definição em código: não é gerada pelo Dataform — é **dependência externa** declarada em `repos/gcp-dataform-contaazul/definitions/external_dependencies.js` (linha 200).

### `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle`
- Histórico de **transições de etapa do playbook** de onboarding (Salesforce). Grão: `nk_transition_id` (1 linha por mudança de etapa; a criação do playbook aparece como `new_step = 'Criado'`). Particionada por `DATE(changed_at)`, clusterizada por `nk_company_id`.
- Campos principais: `nk_company_id`, `changed_at` (DATETIME, America/Sao_Paulo), `old_step`, `new_step`, `playbook_owner_name` (dono/analista do playbook), `assigned_to`, `is_no_show`, `is_reschedule`, `is_meeting`, `playbook_name`, `onboarding_type`, `is_deleted`.
- Etapas (`new_step`) observadas nas fórmulas: `Criado`, `1ª Reunião`, `Finalização`, `Finalizado`, `Abandono`, `Não Compareceu` (e outras intermediárias, tratadas como "avançou além da 1ª reunião").
- Definição em código: `repos/gcp-dataform-contaazul/definitions/gold/onboarding/tables/fact_playbook_stage_lifecycle.sqlx`

### `contaazul-ssbi.gold_onboarding.fact_playbook_agenda_lifecycle`
- Eventos de **agenda/reunião** dos playbooks (Salesforce Events). Grão: `nk_event_id`. Particionada por `activity_date`.
- Campos principais: `nk_company_id`, `activity_date`, `subject`, `schedule_status` (`Realizado`/`Cancelado`/`Planejado`), `is_realized`, `is_cancelled`, `playbook_owner_name`, `last_modified_datetime`.
- Definição em código: `repos/gcp-dataform-contaazul/definitions/gold/onboarding/tables/fact_playbook_agenda_lifecycle.sqlx`

### `contaazul-ssbi.gold_capro_features.fact_capro_features`
- Uso de features por `(dia, empresa, feature)` — é a **fonte única de "usou o ERP"**. Particionada por `DATE_TRUNC(nk_date, MONTH)`.
- Campos principais: `nk_date`, `nk_company_id`, `feature`, `category` (`Ativo`/`Trial`), `total_use`, `total_value`, `any_feature` (BOOL — o conceito "usou qualquer feature manual"), `is_onboarding_activation_feature`, `is_ongoing_feature`.
- **Toda** régua de uso/engajamento do painel é `any_feature = TRUE`.
- Definição em código: `repos/gcp-dataform-contaazul/definitions/gold/capro_features/tables/fact_capro_features.sqlx`

### `contaazul-ssbi.gold_common.dim_date`
- Calendário. Usada para **dias úteis**: a régua de 5 dias é sempre em **dias úteis** (`work_day > 0`), não em dias corridos.
- Campos principais: `nk_date`, `work_day` (0 = não útil, 50 = meio período, 100 = útil), `national_work_day`, `work_day_in_month`, `year_month`.
- Definição em código: `repos/gcp-dataform-contaazul/definitions/gold/common/tables/dim_date.sqlx`

### `contaazul-ssbi.silver_onboarding.onboarding_events`
- Eventos de comportamento nas telas de onboarding/treinamento. Particionada por `event_date` (histórico completo desde 2023-03-01).
- Campos principais: `id`, `event_date` (DATE), `contaazul_company_id`, `user_id`, `module`, `action`, `view`.
- Ações usadas: `TRAINING_DECLINED_CLICKED` (recusou o formulário de treinamento), `TRAINING_ROLLOVER_VIEWED` e `INTRUSIVE_TRAINING_ROLLOVER_VIEWED` (viu a tela do formulário).
- Definição em código: `repos/gcp-dataform-contaazul/definitions/silver/onboarding/tables/onboarding_events.sqlx`

### `contaazul-ssbi.gold_onboarding.dim_adoption_portfolio`
- **Carteira de adoção do CS** — planilha Google Sheets mantida à mão pelas CSMs, carregada via Airbyte. Grão: **1 linha por conta** (`nk_company_id`), versão mais recente do sync → é um **SNAPSHOT** (ver limitação de grão na seção 4).
- Campos com **data própria** (têm série histórica reconstruível): `portfolio_entry_date`, `churn_date`, `email_sent_at`, `whatsapp_sent_at`, `responded_at`, `whatsapp_replied_at` (DATETIME), `meeting_scheduled_at` (DATETIME), `setup_completion_at` (DATETIME), `renewal_date`, `contract_date`, `subscription_date`, `last_feature_date`.
- Campos de **estado sem data** (NÃO têm histórico): `adoption_d90`, `d90_reached`, `no_usage_last_5_days`, `risk_zone_recurrence_count`, `five_day_no_usage_blocks`, `consecutive_days_no_usage`, `current_situation`, `csat_post_setup`, `csat_post_csm_call`, `perceived_adoption`, `renewal_status`, `playbook_stage`, `contact_status`.
- Outros: `cohort_month` (STRING `YYYY-MM`, `'unknown'` quando vazio), `plan_recurrence`, `meeting_completed`, `mrr_amount`, `csm_owner`, `ism_owner`, `origin_channel`, `perceived_fit_notes`.
- **Sentinelas** (o Dataform troca nulo por sentinela, nunca por NULL): datas ausentes viram `DATE '0001-01-01'` / `DATETIME '0001-01-01 00:00:00'`; textos ausentes viram a string `'unknown'`; inteiros ausentes viram `0`. `csat_post_setup`/`csat_post_csm_call` são `INT64` (0 = sem resposta).
- Definição em código: `repos/gcp-dataform-contaazul/definitions/gold/onboarding/tables/dim_adoption_portfolio.sqlx`

### `contaazul-ssbi.silver_revenue.db_core_history`
- Histórico de movimentações de assinatura pela ótica de Receita — é a **fonte do Logo Churn/Winback** (Bloco 1).
- Campos principais: `metric_date`, `daily_metric` (tipo do movimento: `NEW`, `CHURN`, `WINBACK`, …), `channel`, `channel_1` (último canal do cliente — é o usado no Bloco 1), `is_freemium`, `contaazul_company_id`, `billing_legacy_company_id`, `mrr_value`.
- Definição em código: `repos/gcp-dataform-contaazul/definitions/silver/revenue/core_metrics_migrations/tables/db_core_history.sqlx`

### `contaazul-ssbi.silver_revenue.smb_migrations` e `contaazul-ssbi.silver_revenue.legacy_batch_flow_base`
- Bases de **migração/fluxo legado** usadas apenas para **reclassificar** movimentos como `LOTE` (e assim **excluí-los** do churn de `SMB`/`ACC`). Chave: `billing_legacy_company_id`.
- Definição em código: `repos/gcp-dataform-contaazul/definitions/silver/revenue/core_metrics_migrations/tables/smb_migrations.sqlx`; `legacy_batch_flow_base` é **dependência externa** (`definitions/external_dependencies.js`, linha 1006).

### `contaazul-ssbi.silver_revenue.batch_lifecycle_migrations`
- Ajustes de churn/winback de lotes migrados (`metric_type`, `access_variation`, `DATE`). Aparece na query do Bloco 1 mas **é sempre rotulada `LOTE`** e depois filtrada fora (`WHERE Canal_Adj IN ('SMB','ACC')`) → **não afeta nenhum número do painel**.
- Definição em código: `repos/gcp-dataform-contaazul/definitions/silver/revenue/core_metrics_migrations/tables/batch_lifecycle_migrations.sqlx`

### `contaazul-ssbi.gold_churn.dim_churn_targets`
- **Metas diárias de Logo Churn** definidas por FP&A/Logo (origem: planilha `metaslogo_churn`).
- Campos principais: `nk_date` (DATE), `meta_churn_acc`, `meta_churn_smb`, `meta_churn_total` (INTEGER **positivos**), `nk_year_month` (STRING `YYYY-MM`).
- **Convenção de sinal**: o North Star **nega** esses valores (meta de churn é negativa, ex. `-1350` no mês).
- Definição em código: **atenção à divergência** — a LookML consome `gold_churn.dim_churn_targets`, mas o Dataform materializa a mesma tabela como `gold_onboarding.dim_meta_logo_churn_targets` em `repos/gcp-dataform-contaazul/definitions/gold/onboarding/tables/dim_meta_churn_onboarding.sqlx` (mesmíssimos nomes de coluna). Ver armadilha na seção 5.

### `contaazul-datalake-prod.bronze_ext_google_sheets.metaslogo_churn`
- Planilha crua das metas de churn (**outro projeto GCP**), com `data_ref` em texto `DD/MM/YYYY` e valores em notação contábil (`"(15)"`). Ainda é lida pela view `north_star_meta_logo_churn`, mas o tabelão já migrou para a tabela gold. **Não usar em análise nova** — preferir a gold.

## 3. KPIs e Queries Validadas

**Como parametrizar todas as queries desta seção:**
- `@inicio` = **primeiro dia do mês da safra** (para o período de referência: `2026-07-01`).
- `@fim` = **data de referência / último dia da safra** (para o período de referência: `2026-07-31`).
- `@fim` deve ser **anterior a hoje** (o painel só apura até D-1) e, para o "realizado do mês", deve ser o último dia do mês de `@inicio`.
- Todas as queries reproduzem o **estado acumulado na data `@fim`**, que é exatamente a coluna `Realizado` do painel. O tabelão calcula o mesmo número montando a grade dia a dia e pegando o último dia; aqui o cálculo é direto na data de referência (resultado equivalente, custo muito menor).
- O piso `DATE '2026-06-01'` que aparece nas queries é o **marco de início do projeto North Star** e é hardcoded na LookML — mantenha-o.

### 3.1 Logo Churn e Winback do mês, por canal (Bloco 1)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba **"LogoChurn"** (tile "Logo Churn & Winback" — mapeamento confirmado via `tab_name=LogoChurn` na URL de entrada) · LookML: `repos/looker/1-ONBOARDING/views/north_star_tabelao.view.lkml` (CTE `churn_raw`, linhas 460-547)
> **Definição de negócio (métrica de Negócio, peso 50%, meta oficial de FP&A — a meta primeira e mais central):**
> - **Churn (gross)** = total de contas canceladas no período, contado como `-1` por evento `CHURN`.
> - **Winback (gross)** = contas recuperadas, contadas como `+1` por evento `WINBACK`.
> - **Logo Churn (o KPI do bloco)** = **NET** = `churn + winback`, acumulado no mês. É um número **negativo** (quanto menos negativo, melhor).
> - Canais: `SMB` = **Venda Direta**, `ACC` = **Recomendação**. O canal usado é `channel_1` (último canal do cliente), com dois ajustes: `BIZDEV` é reclassificado como `SMB`; e quem tem `billing_legacy_company_id` em `smb_migrations` **ou** em `legacy_batch_flow_base` é reclassificado como `LOTE` e **sai da conta**. Clientes com origem freemium viram `FREE_ORIGEM` e também saem.
> - `vs target` deste bloco = `meta ÷ realizado` (≥ 100% = bom: churnou menos que o teto).

```sql
WITH base_1 AS (
  SELECT DISTINCT
    db.contaazul_company_id,
    db.metric_date AS dia,
    CASE WHEN free.billing_legacy_company_id IS NOT NULL THEN 'FREE_ORIGEM' ELSE db.channel_1 END AS channel_1,
    db.daily_metric,
    CASE WHEN db.daily_metric = 'CHURN'   THEN -1 END AS churn_customer,
    CASE WHEN db.daily_metric = 'WINBACK' THEN  1 END AS winback_customer,
    mig.billing_legacy_company_id AS id_migracao,
    fv.billing_legacy_company_id  AS id_fluxo_velho
  FROM `contaazul-ssbi.silver_revenue.db_core_history` db
  LEFT JOIN `contaazul-ssbi.silver_revenue.smb_migrations` mig
    ON db.billing_legacy_company_id = mig.billing_legacy_company_id
  LEFT JOIN `contaazul-ssbi.silver_revenue.legacy_batch_flow_base` fv
    ON db.billing_legacy_company_id = fv.billing_legacy_company_id
  LEFT JOIN (
    SELECT DISTINCT billing_legacy_company_id
    FROM `contaazul-ssbi.silver_revenue.db_core_history`
    WHERE daily_metric = 'NEW' AND is_freemium = TRUE
  ) free ON free.billing_legacy_company_id = db.billing_legacy_company_id
  WHERE db.metric_date BETWEEN @inicio AND @fim
),
base_1_adj AS (
  SELECT
    CASE
      WHEN channel_1 IN ('ACC','SMB') AND (id_migracao IS NOT NULL OR id_fluxo_velho IS NOT NULL) THEN 'LOTE'
      WHEN channel_1 = 'BIZDEV' THEN 'SMB'
      ELSE channel_1
    END AS canal_adj,
    churn_customer,
    winback_customer
  FROM base_1
),
por_canal AS (
  SELECT
    CASE canal_adj WHEN 'SMB' THEN 'Venda Direta' WHEN 'ACC' THEN 'Recomendação' END AS canal,
    churn_customer,
    winback_customer
  FROM base_1_adj
  WHERE canal_adj IN ('SMB','ACC')
)
SELECT
  IFNULL(canal, 'Total') AS canal_agregacao,
  IFNULL(SUM(churn_customer), 0)                                       AS churn_gross,
  IFNULL(SUM(winback_customer), 0)                                     AS winback,
  IFNULL(SUM(churn_customer), 0) + IFNULL(SUM(winback_customer), 0)    AS logo_churn_net
FROM por_canal
GROUP BY ROLLUP(canal)
ORDER BY canal_agregacao
```

### 3.2 Meta de Logo Churn do mês (Bloco 1)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba **"LogoChurn"** (mesmo tile "Logo Churn & Winback", linhas "Meta" e "target (meta acumulada)") · LookML: `north_star_tabelao.view.lkml` (CTEs `meta_churn`/`meta_churn_acum`, linhas 557-578) · Dataform: `repos/gcp-dataform-contaazul/definitions/gold/onboarding/tables/dim_meta_churn_onboarding.sqlx`
> **Definição:** a meta é **diária** e vem de FP&A. A coluna `Meta` do cabeçalho é a **soma do mês inteiro** (constante em todas as datas); a sub-linha `target (meta acumulada)` é a soma **até a data de referência**. A tabela gold guarda os valores **positivos**; o painel os **nega** (convenção: meta de churn é negativa). Julho/2026 = `-1350` no total (`-1140` SMB, `-210` ACC), substituindo o valor antigo digitado à mão (`-1355`).

```sql
SELECT
  FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
  -SUM(IF(m.nk_date BETWEEN @inicio AND @fim, m.meta_churn_smb,   0)) AS meta_venda_direta_acum,
  -SUM(IF(m.nk_date BETWEEN @inicio AND @fim, m.meta_churn_acc,   0)) AS meta_recomendacao_acum,
  -SUM(IF(m.nk_date BETWEEN @inicio AND @fim, m.meta_churn_total, 0)) AS meta_total_acum,
  -SUM(m.meta_churn_smb)   AS meta_venda_direta_mes,
  -SUM(m.meta_churn_acc)   AS meta_recomendacao_mes,
  -SUM(m.meta_churn_total) AS meta_total_mes
FROM `contaazul-ssbi.gold_churn.dim_churn_targets` m
WHERE DATE_TRUNC(m.nk_date, MONTH) = DATE_TRUNC(@inicio, MONTH)
```

### 3.3 Cobertura de Onboarding (Bloco 2)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Cobertura de Onboarding (tile "Cobertura de Onboarding"; nome exato da aba não pôde ser confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (CTE `funil_calc`, linhas 609-625) e `north_star_funil_onboarding.view.lkml` (measures `cobertura_pct`/`cobertura_bruta_pct`, linhas 627-639)
> **Definição de negócio (Estratégico, peso 25%, meta aspiracional 80%):** "clientes atendidos 1:1 sobre o total de quem pediu ajuda". Como ainda **não existe** o número de quem solicita apoio, o denominador provisório é o **total de vendas da safra**.
> - **Cobertura (oficial, confirmada com o time em 15/07)** = `(avançou além da 1ª reunião + concluiu onboarding) ÷ (total da safra − recusou o formulário (líquido))`.
> - **Cobertura Bruta** (variante informativa) = mesma coisa **sem** descontar quem recusou o formulário do denominador.
> - **Forecast** = `(0,9 × em 1ª reunião + avançou além + concluiu) ÷ (total da safra − recusou líquido)`.
> - Meta = **80%** para as duas primeiras; `Forecast` não tem meta.

```sql
WITH entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ultimo_estado AS (
  SELECT contaazul_company_id, playbook_step
  FROM (
    SELECT
      p.nk_company_id AS contaazul_company_id,
      p.new_step      AS playbook_step,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE p.is_deleted = FALSE
      AND p.nk_company_id IS NOT NULL
      AND p.changed_at >= DATETIME '2026-06-01'
      AND DATE(p.changed_at) <= @fim
  )
  WHERE rn = 1
),
recusa AS (
  SELECT contaazul_company_id, MIN(event_date) AS data_recusa
  FROM `contaazul-ssbi.silver_onboarding.onboarding_events`
  WHERE action = 'TRAINING_DECLINED_CLICKED'
  GROUP BY contaazul_company_id
),
agenda AS (
  SELECT DISTINCT a.nk_company_id AS contaazul_company_id
  FROM `contaazul-ssbi.gold_onboarding.fact_playbook_agenda_lifecycle` a
  WHERE a.is_cancelled = FALSE
    AND a.activity_date IS NOT NULL
    AND a.last_modified_datetime >= DATETIME '2026-06-01'
    AND DATE(a.last_modified_datetime) <= @fim
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    COUNT(*) AS total_safra,
    COUNTIF(ue.playbook_step IS NOT NULL
            AND ue.playbook_step NOT IN ('Criado','1ª Reunião','Finalizado','Abandono','Não Compareceu')) AS avancou_alem_1a_reuniao,
    COUNTIF(ue.playbook_step = 'Finalizado') AS concluiu_onboarding,
    COUNTIF(ue.playbook_step = '1ª Reunião'
            OR (ue.playbook_step = 'Criado' AND ag.contaazul_company_id IS NOT NULL)) AS primeira_reuniao,
    COUNTIF(r.data_recusa <= @fim AND ue.playbook_step IS NULL) AS recusou_formulario_liquido
  FROM entrada e
  LEFT JOIN ultimo_estado ue ON ue.contaazul_company_id = e.contaazul_company_id
  LEFT JOIN recusa        r  ON r.contaazul_company_id  = e.contaazul_company_id
  LEFT JOIN agenda        ag ON ag.contaazul_company_id = e.contaazul_company_id
)
SELECT
  safra,
  data_referencia,
  total_safra,
  recusou_formulario_liquido,
  avancou_alem_1a_reuniao,
  concluiu_onboarding,
  SAFE_DIVIDE(avancou_alem_1a_reuniao + concluiu_onboarding, total_safra) AS cobertura_bruta_pct,
  SAFE_DIVIDE(avancou_alem_1a_reuniao + concluiu_onboarding, total_safra - recusou_formulario_liquido) AS cobertura_pct,
  SAFE_DIVIDE(0.9 * primeira_reuniao + avancou_alem_1a_reuniao + concluiu_onboarding,
              total_safra - recusou_formulario_liquido) AS forecast_pct,
  0.80 AS meta
FROM agg
```

### 3.4 Funil de onboarding completo por safra (Bloco 2, detalhe)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Cobertura de Onboarding (tile "Cobertura de Onboarding", sub-linhas; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (CTE `funil_raw`, linhas 274-315) e descrições em `north_star_funil_onboarding.view.lkml` (linhas 451-587)
> **Definição de cada etapa (acumulada até `@fim`, por safra):**
> - `sem_playbook` — nenhuma transição de playbook até a data. Desdobra em **dois cortes ortogonais**: (a) por dias de vida — `apto_contato` (7+ dias corridos desde a aquisição; a regra do time é só contatar a partir do 8º dia) vs. `fora_janela` (< 7 dias); (b) por comportamento — `recusou_formulario_liquido` + `nunca_usou_erp` + `usa_erp_engajado` + `usa_erp_desengajado`.
> - `inscrito_sem_agendamento` — etapa `Criado` **sem** reunião já agendada (não cancelada) no Salesforce.
> - `primeira_reuniao` — etapa `1ª Reunião`, **ou** `Criado` com reunião agendada (agendado e realizado são tratados como a mesma coisa, por decisão do time).
> - `avancou_alem_1a_reuniao` — qualquer etapa que não seja `Criado`, `1ª Reunião`, `Finalizado`, `Abandono`, `Não Compareceu` (inclui a etapa `Finalização`, que é **diferente** de `Finalizado`).
> - `concluiu_onboarding` = `Finalizado`; `abandonou` = `Abandono`; `nao_compareceu` = `Não Compareceu`.
> - `recusou_formulario_bruto` — clicou em recusar treinamento (`TRAINING_DECLINED_CLICKED`); **líquido** = recusou **e** continua sem playbook. `viu_tela_formulario` — viu a tela de rollover.
> - `engajado`/`desengajado` = streak de inatividade em **dias úteis** `< 5` / `>= 5` (régua contínua desde a aquisição).
> - **Checksum de auditoria**: `primeira_reuniao + avancou_alem + concluiu + abandonou + nao_compareceu + inscrito_sem_agendamento + recusou_liquido + usa_erp_engajado + usa_erp_desengajado + nunca_usou_erp` deve dar **exatamente** `total_safra`. Se não der, há sobreposição/lacuna na classificação.

```sql
WITH entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ref AS (
  SELECT LEAST(@fim, MAX(nk_date)) AS data_max
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features`
  WHERE nk_date >= DATE '2026-06-01'
),
ultimo_estado AS (
  SELECT contaazul_company_id, playbook_step
  FROM (
    SELECT
      p.nk_company_id AS contaazul_company_id,
      p.new_step      AS playbook_step,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE p.is_deleted = FALSE
      AND p.nk_company_id IS NOT NULL
      AND p.changed_at >= DATETIME '2026-06-01'
      AND DATE(p.changed_at) <= @fim
  )
  WHERE rn = 1
),
agenda AS (
  SELECT DISTINCT a.nk_company_id AS contaazul_company_id
  FROM `contaazul-ssbi.gold_onboarding.fact_playbook_agenda_lifecycle` a
  WHERE a.is_cancelled = FALSE
    AND a.activity_date IS NOT NULL
    AND a.last_modified_datetime >= DATETIME '2026-06-01'
    AND DATE(a.last_modified_datetime) <= @fim
),
recusa AS (
  SELECT contaazul_company_id, MIN(event_date) AS data_recusa
  FROM `contaazul-ssbi.silver_onboarding.onboarding_events`
  WHERE action = 'TRAINING_DECLINED_CLICKED'
  GROUP BY contaazul_company_id
),
tela AS (
  SELECT contaazul_company_id, MIN(event_date) AS data_tela
  FROM `contaazul-ssbi.silver_onboarding.onboarding_events`
  WHERE action IN ('TRAINING_ROLLOVER_VIEWED','INTRUSIVE_TRAINING_ROLLOVER_VIEWED')
  GROUP BY contaazul_company_id
),
primeiro_uso AS (
  SELECT nk_company_id AS contaazul_company_id, MIN(nk_date) AS data_primeiro_uso
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features`
  WHERE any_feature = TRUE AND nk_date >= DATE '2026-06-01'
  GROUP BY nk_company_id
),
dias_uteis AS (
  SELECT d.nk_date AS dia
  FROM `contaazul-ssbi.gold_common.dim_date` d
  CROSS JOIN ref r
  WHERE d.work_day > 0
    AND d.nk_date BETWEEN (SELECT MIN(data_aquisicao) FROM entrada) AND r.data_max
),
grade AS (
  SELECT
    e.contaazul_company_id,
    d.dia,
    ROW_NUMBER() OVER (PARTITION BY e.contaazul_company_id ORDER BY d.dia) AS dia_util_n
  FROM entrada e
  JOIN dias_uteis d ON d.dia >= e.data_aquisicao
),
uso_diario AS (
  SELECT DISTINCT f.nk_company_id AS contaazul_company_id, f.nk_date AS dia
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features` f
  CROSS JOIN ref r
  WHERE f.any_feature = TRUE
    AND f.nk_date BETWEEN DATE '2026-06-01' AND r.data_max
),
streak AS (
  SELECT
    g.contaazul_company_id,
    g.dia_util_n,
    g.dia_util_n - COALESCE(
      MAX(IF(u.dia IS NOT NULL, g.dia_util_n, NULL)) OVER (
        PARTITION BY g.contaazul_company_id ORDER BY g.dia_util_n ROWS UNBOUNDED PRECEDING), 0) AS streak_atual
  FROM grade g
  LEFT JOIN uso_diario u
    ON u.contaazul_company_id = g.contaazul_company_id AND u.dia = g.dia
),
streak_ref AS (
  SELECT contaazul_company_id, streak_atual
  FROM (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.contaazul_company_id ORDER BY s.dia_util_n DESC) AS rn
    FROM streak s
  )
  WHERE rn = 1
)
SELECT
  FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
  @fim AS data_referencia,
  COUNT(*) AS total_safra,
  COUNTIF(ue.playbook_step IS NULL) AS sem_playbook,
  COUNTIF(ue.playbook_step IS NULL AND DATE_DIFF(@fim, e.data_aquisicao, DAY) >= 7) AS sem_playbook_apto_contato,
  COUNTIF(ue.playbook_step IS NULL AND DATE_DIFF(@fim, e.data_aquisicao, DAY) <  7) AS sem_playbook_fora_janela,
  COUNTIF(ue.playbook_step = 'Criado' AND ag.contaazul_company_id IS NULL) AS inscrito_sem_agendamento,
  COUNTIF(ue.playbook_step = '1ª Reunião'
          OR (ue.playbook_step = 'Criado' AND ag.contaazul_company_id IS NOT NULL)) AS primeira_reuniao,
  COUNTIF(ue.playbook_step IS NOT NULL
          AND ue.playbook_step NOT IN ('Criado','1ª Reunião','Finalizado','Abandono','Não Compareceu')) AS avancou_alem_1a_reuniao,
  COUNTIF(ue.playbook_step = 'Finalizado')     AS concluiu_onboarding,
  COUNTIF(ue.playbook_step = 'Abandono')       AS abandonou,
  COUNTIF(ue.playbook_step = 'Não Compareceu') AS nao_compareceu,
  COUNTIF(r.data_recusa <= @fim)                                        AS recusou_formulario_bruto,
  COUNTIF(r.data_recusa <= @fim AND ue.playbook_step IS NULL)           AS recusou_formulario_liquido,
  COUNTIF(t.data_tela   <= @fim)                                        AS viu_tela_formulario,
  COUNTIF(ue.playbook_step IS NULL
          AND (r.data_recusa IS NULL OR r.data_recusa > @fim)
          AND (pu.data_primeiro_uso IS NULL OR pu.data_primeiro_uso > @fim)) AS sem_playbook_nunca_usou_erp,
  COUNTIF(ue.playbook_step IS NULL
          AND (r.data_recusa IS NULL OR r.data_recusa > @fim)
          AND pu.data_primeiro_uso <= @fim
          AND (sr.streak_atual IS NULL OR sr.streak_atual < 5)) AS sem_playbook_usa_erp_engajado,
  COUNTIF(ue.playbook_step IS NULL
          AND (r.data_recusa IS NULL OR r.data_recusa > @fim)
          AND pu.data_primeiro_uso <= @fim
          AND sr.streak_atual >= 5) AS sem_playbook_usa_erp_desengajado
FROM entrada e
LEFT JOIN ultimo_estado ue ON ue.contaazul_company_id = e.contaazul_company_id
LEFT JOIN agenda        ag ON ag.contaazul_company_id = e.contaazul_company_id
LEFT JOIN recusa        r  ON r.contaazul_company_id  = e.contaazul_company_id
LEFT JOIN tela          t  ON t.contaazul_company_id  = e.contaazul_company_id
LEFT JOIN primeiro_uso  pu ON pu.contaazul_company_id = e.contaazul_company_id
LEFT JOIN streak_ref    sr ON sr.contaazul_company_id = e.contaazul_company_id
```

### 3.5 # Cobertura (HC) e capacity por analista (Bloco 3)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Cobertura/HC (tile "# Cobertura (HC)"; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (`funil_calc`, linhas 609-625; bloco 3 nas linhas 851-858)
> **Definição de negócio ([ISM] Meta de participantes por analista, peso 50%):** `(vendas do mês × 80%) ÷ headcount disponível no mês`, limitada a um **teto de 70 clientes por analista**.
> **Como o painel calcula:**
> - `Vendas (acumulativa) Bruto` = `total_safra`; `Vendas (acumulativa) Líquido` = `ROUND(total_safra × 0,8)`.
> - `HC Combinado (fixo)` = **14** (HC planejado, decisão de negócio; fica fora da query por opção do time).
> - `Cobertura - Conclusão Onboarding (HC)` = `ROUND(total_safra × 0,8 ÷ 14)` → é a **meta de participantes por analista**. **O teto de 70 não está implementado em código** — a query abaixo devolve as duas versões.
> - `# Cobertura` (o KPI, meta **80%**) = `concluiu_onboarding ÷ (total_safra × 0,8)`.
> - `HC Realizado (via query)` = contagem distinta de `playbook_owner_name` **com transição de playbook no dia de referência**; `Delta · HC Planejado vs Realizado` = `HC Realizado − 14`.

```sql
WITH entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ultimo_estado AS (
  SELECT contaazul_company_id, playbook_step
  FROM (
    SELECT
      p.nk_company_id AS contaazul_company_id,
      p.new_step      AS playbook_step,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE p.is_deleted = FALSE
      AND p.nk_company_id IS NOT NULL
      AND p.changed_at >= DATETIME '2026-06-01'
      AND DATE(p.changed_at) <= @fim
  )
  WHERE rn = 1
),
hc AS (
  SELECT COUNT(DISTINCT p.playbook_owner_name) AS hc_ativo
  FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
  WHERE p.is_deleted = FALSE
    AND DATE(p.changed_at) = @fim
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    COUNT(*) AS total_safra,
    COUNTIF(ue.playbook_step = 'Finalizado') AS concluiu_onboarding,
    (SELECT hc_ativo FROM hc) AS hc_ativo,
    14 AS hc_combinado_fixo
  FROM entrada e
  LEFT JOIN ultimo_estado ue ON ue.contaazul_company_id = e.contaazul_company_id
)
SELECT
  safra,
  data_referencia,
  total_safra                                AS vendas_acumuladas_bruto,
  ROUND(total_safra * 0.8)                   AS vendas_acumuladas_liquido,
  hc_combinado_fixo,
  hc_ativo                                   AS hc_realizado,
  hc_ativo - hc_combinado_fixo               AS delta_hc_planejado_vs_realizado,
  concluiu_onboarding,
  ROUND(total_safra * 0.8 / hc_combinado_fixo)                AS meta_participantes_por_analista,
  LEAST(ROUND(total_safra * 0.8 / hc_combinado_fixo), 70)     AS meta_participantes_por_analista_com_teto,
  SAFE_DIVIDE(concluiu_onboarding, total_safra * 0.8)         AS hcobertura_pct,
  0.80 AS meta
FROM agg
```

### 3.6 Tempo de Cobertura — setup em até 15 dias (Bloco 4)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Tempo de Cobertura (tile "Tempo de Cobertura"; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (`funil_cobertura`, linhas 131-142; `tempo_cobertura_pct`, linha 622) e `north_star_funil_onboarding.view.lkml` (linha 661)
> **Definição de negócio ([ISM] Tempo de Cobertura, peso 10%, meta 85%):** `total de setups concluídos em até 15 dias ÷ total de setups concluídos`. O critério do time é **setup de 0 a 15 dias após a contratação**, contados em **dias corridos** (`DATE_DIFF(data_finalizado, data_aquisicao, DAY) <= 15`). Só entra quem está com o playbook em `Finalizado` **na data de referência** (corrigido na v5: antes usava o estado global, o que quebrava para clientes reabertos).

```sql
WITH entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ultimo_estado AS (
  SELECT contaazul_company_id, playbook_step, data_step
  FROM (
    SELECT
      p.nk_company_id       AS contaazul_company_id,
      p.new_step            AS playbook_step,
      DATE(p.changed_at)    AS data_step,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE p.is_deleted = FALSE
      AND p.nk_company_id IS NOT NULL
      AND p.changed_at >= DATETIME '2026-06-01'
      AND DATE(p.changed_at) <= @fim
  )
  WHERE rn = 1
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    COUNTIF(ue.playbook_step = 'Finalizado') AS concluiu_onboarding,
    COUNTIF(ue.playbook_step = 'Finalizado'
            AND DATE_DIFF(ue.data_step, e.data_aquisicao, DAY) <= 15) AS finalizado_dentro_15d
  FROM entrada e
  LEFT JOIN ultimo_estado ue ON ue.contaazul_company_id = e.contaazul_company_id
)
SELECT
  safra,
  data_referencia,
  concluiu_onboarding,
  finalizado_dentro_15d,
  SAFE_DIVIDE(finalizado_dentro_15d, concluiu_onboarding) AS tempo_cobertura_pct,
  0.85 AS meta
FROM agg
```

### 3.7 Qualidade do Setup — reuso contínuo nos 30 dias após o setup (Bloco 5)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Qualidade de Setup (tile "Qualidade de Setup"; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (CTEs `dias_uteis_pos_fin`/`streak_pos_fin`/`funil_engajamento_pos_finalizado`, linhas 155-216; `qualidade_setup_pct`, linha 623)
> **Definição de negócio ([ISM] Qualidade SETUP, peso 50%, meta 80%):** `(clientes que mantiveram uso contínuo, sem nenhuma lacuna de 5 ou mais dias úteis consecutivos, durante os primeiros 30 dias após o setup finalizado) ÷ (total de setups realizados) × 100`.
> **Como o painel calcula:**
> - `pos_finalizado_apurados` = clientes cuja última transição é `Finalizado` e cuja data de referência está entre **D+1 e D+30** da finalização (o próprio dia da finalização **não** conta).
> - `pos_finalizado_engajados` = dentre os apurados, os que têm **streak de inatividade < 5 dias úteis** na data de referência.
> - `Qualidade de Setup` = `engajados ÷ apurados`. Mede **engajamento, não CSAT** (decisão do time, 15/07).
> - Streak = nº de **dias úteis** (`dim_date.work_day > 0`) desde o último dia útil com `any_feature = TRUE`, dentro da janela D+1..D+30. Dia com uso zera o streak.
> - Calcule a diferença vs. meta a partir da razão bruta, não do valor já arredondado (evita erro de arredondamento em cascata).

```sql
WITH ref AS (
  SELECT LEAST(@fim, MAX(nk_date)) AS data_max
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features`
  WHERE nk_date >= DATE '2026-06-01'
),
entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ultimo_estado AS (
  SELECT contaazul_company_id, playbook_step, data_step
  FROM (
    SELECT
      p.nk_company_id    AS contaazul_company_id,
      p.new_step         AS playbook_step,
      DATE(p.changed_at) AS data_step,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE p.is_deleted = FALSE
      AND p.nk_company_id IS NOT NULL
      AND p.changed_at >= DATETIME '2026-06-01'
      AND DATE(p.changed_at) <= @fim
  )
  WHERE rn = 1
),
fin AS (
  SELECT e.contaazul_company_id, ue.data_step AS data_finalizado
  FROM entrada e
  JOIN ultimo_estado ue ON ue.contaazul_company_id = e.contaazul_company_id
  WHERE ue.playbook_step = 'Finalizado'
),
dias_uteis_pos_fin AS (
  SELECT
    f.contaazul_company_id,
    d.nk_date AS dia,
    ROW_NUMBER() OVER (PARTITION BY f.contaazul_company_id ORDER BY d.nk_date) AS dia_util_n
  FROM fin f
  CROSS JOIN ref r
  JOIN `contaazul-ssbi.gold_common.dim_date` d
    ON d.nk_date BETWEEN DATE_ADD(f.data_finalizado, INTERVAL 1 DAY)
                     AND LEAST(DATE_ADD(f.data_finalizado, INTERVAL 30 DAY), r.data_max)
   AND d.work_day > 0
),
uso_pos_fin AS (
  SELECT DISTINCT f.contaazul_company_id, c.nk_date AS dia
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features` c
  JOIN fin f ON f.contaazul_company_id = c.nk_company_id
  JOIN `contaazul-ssbi.gold_common.dim_date` d ON d.nk_date = c.nk_date AND d.work_day > 0
  WHERE c.any_feature = TRUE
    AND c.nk_date >= DATE '2026-06-01'
    AND c.nk_date BETWEEN DATE_ADD(f.data_finalizado, INTERVAL 1 DAY)
                      AND DATE_ADD(f.data_finalizado, INTERVAL 30 DAY)
),
streak AS (
  SELECT
    du.contaazul_company_id,
    du.dia_util_n,
    du.dia_util_n - COALESCE(
      MAX(IF(u.dia IS NOT NULL, du.dia_util_n, NULL)) OVER (
        PARTITION BY du.contaazul_company_id ORDER BY du.dia_util_n ROWS UNBOUNDED PRECEDING), 0) AS streak_atual
  FROM dias_uteis_pos_fin du
  LEFT JOIN uso_pos_fin u
    ON u.contaazul_company_id = du.contaazul_company_id AND u.dia = du.dia
),
streak_ref AS (
  SELECT contaazul_company_id, streak_atual
  FROM (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.contaazul_company_id ORDER BY s.dia_util_n DESC) AS rn
    FROM streak s
  )
  WHERE rn = 1
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    COUNTIF(DATE_DIFF(@fim, f.data_finalizado, DAY) BETWEEN 1 AND 30) AS pos_finalizado_apurados,
    COUNTIF(DATE_DIFF(@fim, f.data_finalizado, DAY) BETWEEN 1 AND 30
            AND (sr.streak_atual IS NULL OR sr.streak_atual < 5)) AS pos_finalizado_engajados
  FROM fin f
  LEFT JOIN streak_ref sr ON sr.contaazul_company_id = f.contaazul_company_id
)
SELECT
  safra,
  data_referencia,
  pos_finalizado_apurados,
  pos_finalizado_engajados,
  SAFE_DIVIDE(pos_finalizado_engajados, pos_finalizado_apurados) AS qualidade_setup_pct,
  0.80 AS meta
FROM agg
```

### 3.8 Teste A/B de playbook — Controle vs. Variante (Blocos 5b/5c)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Qualidade de Setup (mesmo tile "Qualidade de Setup", linhas "Qualidade de Setup - Variante/Controle"; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (classificação `grupo`, linhas 279-283; `funil_calc_grupo`, linhas 627-639)
> **Definição de negócio:** experimento de 5 semanas comparando dois playbooks de setup, medido pela **mesma** métrica de Qualidade de Setup (engajamento nos 30 dias pós-finalização), meta 80% nos dois grupos.
> - **Controle** — 8 ISMs, **1 reunião**, playbook atual "MMA" (Minimamente Ativo). Pede só arquivo OFX / dados da integração bancária; na 1ª reunião deixa 7 features ativas. Objetivo: **ativação técnica**.
> - **Variante** — 4 ISMs, **2 reuniões**, novo playbook focado no ecossistema financeiro. Pede integração bancária **+ planilha completa de Despesas Fixas**; na 1ª reunião lança as despesas fixas e faz conciliação (baixa) ao vivo; a 2ª reunião é o eixo vendas, calibrado por tipo de negócio. Objetivo: **geração de valor + autonomia de uso**.
> - **Hipótese central:** cliente que sai da 1ª reunião com o eixo despesas rodando e da 2ª com o eixo vendas calibrado mantém uso autônomo e cria hábito de recorrência semanal.
> - **Roteamento**: o Salesforce cria o objeto "Playbook" e direciona para um ISM; a bifurcação A/B acontece na **pré-reunião**.
> - **Como o código classifica o grupo** (não há flag de experimento no dado): pelo **nome do analista que finalizou o playbook** (`playbook_owner_name` da última transição). `Variante` = `Vanessa Inaiara de Assis Bayersdorfer`, `Franciele Franca`, `Jessica Souza`, `Eduarda Silva`; qualquer outro nome = `Controle`; quem ainda não finalizou fica **fora** do A/B (grupo `NULL`).

```sql
WITH ref AS (
  SELECT LEAST(@fim, MAX(nk_date)) AS data_max
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features`
  WHERE nk_date >= DATE '2026-06-01'
),
entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ultimo_estado AS (
  SELECT contaazul_company_id, playbook_step, data_step, playbook_owner_name
  FROM (
    SELECT
      p.nk_company_id       AS contaazul_company_id,
      p.new_step            AS playbook_step,
      DATE(p.changed_at)    AS data_step,
      p.playbook_owner_name AS playbook_owner_name,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE p.is_deleted = FALSE
      AND p.nk_company_id IS NOT NULL
      AND p.changed_at >= DATETIME '2026-06-01'
      AND DATE(p.changed_at) <= @fim
  )
  WHERE rn = 1
),
fin AS (
  SELECT
    e.contaazul_company_id,
    ue.data_step AS data_finalizado,
    CASE
      WHEN ue.playbook_owner_name IN ('Vanessa Inaiara de Assis Bayersdorfer','Franciele Franca','Jessica Souza','Eduarda Silva') THEN 'Variante'
      WHEN ue.playbook_owner_name IS NULL THEN NULL
      ELSE 'Controle'
    END AS grupo
  FROM entrada e
  JOIN ultimo_estado ue ON ue.contaazul_company_id = e.contaazul_company_id
  WHERE ue.playbook_step = 'Finalizado'
),
dias_uteis_pos_fin AS (
  SELECT
    f.contaazul_company_id,
    d.nk_date AS dia,
    ROW_NUMBER() OVER (PARTITION BY f.contaazul_company_id ORDER BY d.nk_date) AS dia_util_n
  FROM fin f
  CROSS JOIN ref r
  JOIN `contaazul-ssbi.gold_common.dim_date` d
    ON d.nk_date BETWEEN DATE_ADD(f.data_finalizado, INTERVAL 1 DAY)
                     AND LEAST(DATE_ADD(f.data_finalizado, INTERVAL 30 DAY), r.data_max)
   AND d.work_day > 0
),
uso_pos_fin AS (
  SELECT DISTINCT f.contaazul_company_id, c.nk_date AS dia
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features` c
  JOIN fin f ON f.contaazul_company_id = c.nk_company_id
  JOIN `contaazul-ssbi.gold_common.dim_date` d ON d.nk_date = c.nk_date AND d.work_day > 0
  WHERE c.any_feature = TRUE
    AND c.nk_date >= DATE '2026-06-01'
    AND c.nk_date BETWEEN DATE_ADD(f.data_finalizado, INTERVAL 1 DAY)
                      AND DATE_ADD(f.data_finalizado, INTERVAL 30 DAY)
),
streak AS (
  SELECT
    du.contaazul_company_id,
    du.dia_util_n,
    du.dia_util_n - COALESCE(
      MAX(IF(u.dia IS NOT NULL, du.dia_util_n, NULL)) OVER (
        PARTITION BY du.contaazul_company_id ORDER BY du.dia_util_n ROWS UNBOUNDED PRECEDING), 0) AS streak_atual
  FROM dias_uteis_pos_fin du
  LEFT JOIN uso_pos_fin u
    ON u.contaazul_company_id = du.contaazul_company_id AND u.dia = du.dia
),
streak_ref AS (
  SELECT contaazul_company_id, streak_atual
  FROM (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY s.contaazul_company_id ORDER BY s.dia_util_n DESC) AS rn
    FROM streak s
  )
  WHERE rn = 1
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    f.grupo,
    COUNTIF(DATE_DIFF(@fim, f.data_finalizado, DAY) BETWEEN 1 AND 30) AS pos_finalizado_apurados,
    COUNTIF(DATE_DIFF(@fim, f.data_finalizado, DAY) BETWEEN 1 AND 30
            AND (sr.streak_atual IS NULL OR sr.streak_atual < 5)) AS pos_finalizado_engajados
  FROM fin f
  LEFT JOIN streak_ref sr ON sr.contaazul_company_id = f.contaazul_company_id
  WHERE f.grupo IS NOT NULL
  GROUP BY safra, data_referencia, f.grupo
)
SELECT
  safra,
  data_referencia,
  grupo,
  pos_finalizado_apurados,
  pos_finalizado_engajados,
  SAFE_DIVIDE(pos_finalizado_engajados, pos_finalizado_apurados) AS qualidade_setup_pct,
  0.80 AS meta
FROM agg
ORDER BY grupo
```

### 3.9 Adoção da safra e zona de risco (Bloco 6, métricas 1-8)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Adoção/Carteira (tile "Adoção / Carteira"; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (CTE `adocao_raw`, linhas 320-455; `adocao_calc`, linhas 641-648)
> **Definição de negócio ([Estratégico] Adoção, peso 25%, meta aspiracional 80%):** no fechamento mensal, `(clientes com uso nos últimos 5 dias úteis consecutivos do mês) ÷ (total de assinantes ativos)`. Cliente que entrou em churn **sai da conta** a partir do churn.
> **Como o painel calcula (fórmula do tabelão, validada com a planilha — ex.: 192/1009 = 19% → 81% de engajados):**
> - `[engajados] / [total safra]` = `1 − (streak_5d_no_final ÷ base_acumulada)`.
> - `base_acumulada` (= `total_safra_ate_essa_data`) = clientes da safra adquiridos até o dia.
> - `streak_5d_no_final` = **zona de risco**: assinante, **sem uso no dia**, e com **5+ dias úteis consecutivos sem usar nenhuma feature**. Desdobra em `streak_5d_onb_finalizado` (playbook `Finalizado` naquela data) e `streak_5d_onb_nao_finalizado`.
> - **Gate de observabilidade (fix de 20/07)**: o cliente só é elegível à flag de risco depois de **≥ 5 dias úteis E ≥ 7 dias corridos** de vida desde a aquisição. Antes disso, nunca é marcado como em risco.
> - `churn_acum` é **líquido**: churnou até o dia **e** não está assinante nem em overdue no dia (desconta winback/reativação). `overdue_acum` é **pontual** (quem tem `metric_type = 'OVERDUE'` naquele dia), não acumulado.
> - O streak é contado em **dias úteis** e **reinicia a cada mês-calendário de vida** (`mes_vida`).
> - Referência de negócio: cliente com gap > 5 dias úteis tem queda drástica na renovação; gap de 22+ dias seguidos é caso quase perdido.

```sql
WITH entrada AS (
  SELECT
    contaazul_company_id,
    MIN(metric_date) AS data_aquisicao
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle`
  WHERE channel IN ('SMB','ACC')
    AND is_freemium = FALSE
    AND metric_type = 'NEW'
    AND metric_date >= DATE '2026-06-01'
    AND metric_date <= @fim
  GROUP BY contaazul_company_id
  HAVING DATE_TRUNC(MIN(metric_date), MONTH) = DATE_TRUNC(@inicio, MONTH)
),
ref AS (
  SELECT LEAST(@fim, MAX(nk_date)) AS data_max
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features`
  WHERE nk_date >= DATE '2026-06-01'
),
dias AS (
  SELECT d.nk_date AS dia
  FROM `contaazul-ssbi.gold_common.dim_date` d
  CROSS JOIN ref r
  WHERE d.work_day > 0
    AND d.nk_date BETWEEN (SELECT MIN(data_aquisicao) FROM entrada) AND r.data_max
),
dia_ref AS (
  SELECT MAX(dia) AS dia FROM dias
),
uso_diario AS (
  SELECT DISTINCT f.nk_company_id AS contaazul_company_id, f.nk_date AS dia
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features` f
  CROSS JOIN ref r
  WHERE f.any_feature = TRUE
    AND f.nk_date BETWEEN DATE '2026-06-01' AND r.data_max
),
grade AS (
  SELECT
    e.contaazul_company_id,
    e.data_aquisicao,
    d.dia,
    ROW_NUMBER() OVER (PARTITION BY e.contaazul_company_id ORDER BY d.dia) AS du_vida,
    CAST(DATE_DIFF(DATE_TRUNC(d.dia, MONTH), DATE_TRUNC(@inicio, MONTH), MONTH) + 1 AS INT64) AS mes_vida
  FROM entrada e
  JOIN dias d ON d.dia >= e.data_aquisicao
),
grade_mv AS (
  SELECT g.*, IF(u.dia IS NULL, 0, 1) AS usou
  FROM grade g
  LEFT JOIN uso_diario u
    ON u.contaazul_company_id = g.contaazul_company_id AND u.dia = g.dia
),
grade_seq AS (
  SELECT gm.*, ROW_NUMBER() OVER (PARTITION BY gm.contaazul_company_id, gm.mes_vida ORDER BY gm.dia) AS seq
  FROM grade_mv gm
),
grade_streak AS (
  SELECT gs.*,
    MAX(IF(gs.usou = 1, gs.seq, NULL)) OVER (
      PARTITION BY gs.contaazul_company_id, gs.mes_vida ORDER BY gs.seq
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS ult_seq_uso
  FROM grade_seq gs
),
inat AS (
  SELECT
    contaazul_company_id,
    dia,
    (du_vida >= 5 AND DATE_DIFF(dia, data_aquisicao, DAY) + 1 >= 7)
      AND ((CASE WHEN usou = 1 THEN 0 ELSE seq - COALESCE(ult_seq_uso, 0) END) >= 5) AS flag_inativo
  FROM grade_streak
),
assinante_dia AS (
  SELECT DISTINCT b.contaazul_company_id, b.metric_date AS dia
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle` b
  JOIN entrada e ON e.contaazul_company_id = b.contaazul_company_id
  WHERE b.channel IN ('SMB','ACC')
    AND b.is_freemium = FALSE
    AND b.metric_type NOT IN ('CHURN','OVERDUE')
    AND b.metric_date >= DATE '2026-06-01'
    AND b.metric_date BETWEEN e.data_aquisicao AND @fim
),
overdue_dia AS (
  SELECT DISTINCT b.contaazul_company_id, b.metric_date AS dia
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle` b
  JOIN entrada e ON e.contaazul_company_id = b.contaazul_company_id
  WHERE b.channel IN ('SMB','ACC')
    AND b.is_freemium = FALSE
    AND b.metric_type = 'OVERDUE'
    AND b.metric_date >= DATE '2026-06-01'
    AND b.metric_date BETWEEN e.data_aquisicao AND @fim
),
churn_first AS (
  SELECT b.contaazul_company_id, MIN(b.metric_date) AS data_churn
  FROM `contaazul-ssbi.bronze_db_bridge.core_metrics_daily_subscription_lifecycle` b
  JOIN entrada e ON e.contaazul_company_id = b.contaazul_company_id
  WHERE b.channel IN ('SMB','ACC')
    AND b.is_freemium = FALSE
    AND b.metric_type = 'CHURN'
    AND b.metric_date >= DATE '2026-06-01'
  GROUP BY b.contaazul_company_id
),
pb_estado AS (
  SELECT contaazul_company_id, playbook_step
  FROM (
    SELECT
      p.nk_company_id AS contaazul_company_id,
      p.new_step      AS playbook_step,
      ROW_NUMBER() OVER (PARTITION BY p.nk_company_id ORDER BY p.changed_at DESC) AS rn
    FROM `contaazul-ssbi.gold_onboarding.fact_playbook_stage_lifecycle` p
    WHERE COALESCE(p.is_deleted, FALSE) = FALSE
      AND p.nk_company_id IS NOT NULL
      AND DATE(p.changed_at) <= (SELECT dia FROM dia_ref)
  )
  WHERE rn = 1
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    (SELECT dia FROM dia_ref) AS dia_referencia,
    COUNT(DISTINCT g.contaazul_company_id) AS base_acumulada,
    COUNTIF(cf.data_churn IS NOT NULL AND cf.data_churn <= g.dia
            AND s.dia IS NULL AND o.dia IS NULL) AS churn_acum,
    COUNTIF(o.dia IS NOT NULL) AS overdue_acum,
    COUNTIF(s.dia IS NOT NULL AND u.dia IS NOT NULL) AS ativos,
    COUNTIF(s.dia IS NOT NULL AND u.dia IS NULL AND COALESCE(i.flag_inativo, FALSE)) AS streak_5d_no_final,
    COUNTIF(s.dia IS NOT NULL AND u.dia IS NULL AND COALESCE(i.flag_inativo, FALSE)
            AND pb.playbook_step = 'Finalizado') AS streak_5d_onb_finalizado,
    COUNTIF(s.dia IS NOT NULL AND u.dia IS NULL AND COALESCE(i.flag_inativo, FALSE)
            AND (pb.playbook_step IS NULL OR pb.playbook_step <> 'Finalizado')) AS streak_5d_onb_nao_finalizado
  FROM grade_mv g
  LEFT JOIN assinante_dia s ON s.contaazul_company_id = g.contaazul_company_id AND s.dia = g.dia
  LEFT JOIN overdue_dia   o ON o.contaazul_company_id = g.contaazul_company_id AND o.dia = g.dia
  LEFT JOIN churn_first  cf ON cf.contaazul_company_id = g.contaazul_company_id
  LEFT JOIN uso_diario    u ON u.contaazul_company_id = g.contaazul_company_id AND u.dia = g.dia
  LEFT JOIN inat          i ON i.contaazul_company_id = g.contaazul_company_id AND i.dia = g.dia
  LEFT JOIN pb_estado    pb ON pb.contaazul_company_id = g.contaazul_company_id
  WHERE g.dia = (SELECT dia FROM dia_ref)
)
SELECT
  safra,
  dia_referencia,
  base_acumulada,
  churn_acum,
  overdue_acum,
  ativos,
  streak_5d_no_final,
  streak_5d_onb_finalizado,
  streak_5d_onb_nao_finalizado,
  1 - SAFE_DIVIDE(streak_5d_no_final, base_acumulada) AS engajados_pct,
  0.80 AS meta
FROM agg
```

### 3.10 Carteira de CS — conversão de retomada de uso e reincidência (Bloco 6, métricas 9-22)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, aba de Adoção/Carteira (mesmo tile "Adoção / Carteira", segunda metade; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (CTEs `carteira_raw`/`carteira_diaria`/`carteira_snapshot`/`carteira_calc`, linhas 661-784) e `north_star_carteira_cs.view.lkml`
> **Definição de negócio:**
> - **[CSM] Adoção — % clientes convertidos para uso após 5 dias consecutivos sem usar** (peso 50%, meta **90%**; referência histórica: 75% voltam a usar organicamente, sem ação) = `[convertidos] ÷ [carteira ativa]`, onde "convertido" = teve **uso nos últimos 5 dias** (janela móvel de 5 dias corridos até a data de referência, vinda do capro).
> - **[CSM] Adoção — Reincidência** (peso 20%, meta **≤ 40%**; histórico ~78,1% voltam a ficar sem uso uma 2ª vez dentro de 90 dias) = `[clientes que voltaram à zona de risco] ÷ [total com contato realizado]`.
> - **`# sim forms D90`** (meta 90%) = `d90_reached = 'sim' ÷ carteira ativa`. Em julho está zerado no gold — leia como "sem dado", não como 0%.
> - Carteira ativa = entrou na carteira até a data **e** não churnou até a data; desdobrada por recorrência do plano (mensal/trimestral/semestral/anual).
> - Estimativa de capacity: ~460 clientes por CSM num ciclo de 90 dias; atuação da CSM até 90 dias após a contratação.
> - **Atenção ao grão**: `reincidencia_cnt` (`risk_zone_recurrence_count`) é **snapshot sem data** — o numerador de Reincidência não tem corte temporal, só o denominador tem. Ver seção 4.

```sql
WITH carteira AS (
  SELECT
    nk_company_id,
    NULLIF(portfolio_entry_date, DATE '0001-01-01') AS entrada,
    NULLIF(churn_date,           DATE '0001-01-01') AS churn,
    LEAST(
      IFNULL(NULLIF(email_sent_at,    DATE '0001-01-01'), DATE '9999-12-31'),
      IFNULL(NULLIF(whatsapp_sent_at, DATE '0001-01-01'), DATE '9999-12-31')
    ) AS contato,
    LOWER(TRIM(NULLIF(plan_recurrence, 'unknown'))) AS recorrencia,
    LOWER(TRIM(NULLIF(playbook_stage,  'unknown'))) AS playbook_stage_norm,
    LOWER(TRIM(NULLIF(d90_reached,     'unknown'))) AS d90_flag,
    risk_zone_recurrence_count AS reincidencia_cnt
  FROM `contaazul-ssbi.gold_onboarding.dim_adoption_portfolio`
  WHERE SAFE.PARSE_DATE('%Y-%m', NULLIF(cohort_month, 'unknown')) = DATE_TRUNC(@inicio, MONTH)
),
uso_ult5dias AS (
  SELECT DISTINCT f.nk_company_id
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features` f
  WHERE f.any_feature = TRUE
    AND f.nk_date BETWEEN DATE_SUB(@fim, INTERVAL 5 DAY) AND @fim
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    COUNTIF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim)) AS carteira_ativa,
    COUNTIF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim) AND c.recorrencia = 'mensal')     AS carteira_ativa_mensal,
    COUNTIF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim) AND c.recorrencia = 'trimestral') AS carteira_ativa_trimestral,
    COUNTIF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim) AND c.recorrencia = 'semestral')  AS carteira_ativa_semestral,
    COUNTIF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim) AND c.recorrencia = 'anual')      AS carteira_ativa_anual,
    COUNTIF(c.entrada <= @fim AND c.contato <= @fim) AS teve_contato,
    COUNTIF(c.entrada <= @fim AND c.playbook_stage_norm = 'finalizado') AS status_finalizado,
    COUNTIF(c.entrada <= @fim AND (c.playbook_stage_norm IS NULL OR c.playbook_stage_norm <> 'finalizado')) AS outros_status,
    COUNT(DISTINCT IF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim) AND u.nk_company_id IS NOT NULL,
                      c.nk_company_id, NULL)) AS usando_ult5dias,
    COUNTIF(c.reincidencia_cnt > 0) AS reincidentes,
    COUNTIF(c.d90_flag IN ('sim','s')) AS d90_sim
  FROM carteira c
  LEFT JOIN uso_ult5dias u ON u.nk_company_id = c.nk_company_id
)
SELECT
  safra,
  data_referencia,
  carteira_ativa,
  carteira_ativa_mensal,
  carteira_ativa_trimestral,
  carteira_ativa_semestral,
  carteira_ativa_anual,
  status_finalizado,
  outros_status,
  teve_contato,
  usando_ult5dias,
  reincidentes,
  d90_sim,
  SAFE_DIVIDE(usando_ult5dias, carteira_ativa) AS convertidos_pct,
  0.90 AS meta_convertidos,
  SAFE_DIVIDE(reincidentes, teve_contato)      AS reincidencia_pct,
  0.40 AS meta_reincidencia,
  SAFE_DIVIDE(d90_sim, carteira_ativa)         AS sim_forms_d90_pct
FROM agg
```

### 3.11 Efetividade das tratativas da CSM por canal — WhatsApp/E-mail, reunião, sem resposta e "Abandono" (Blocos 7, 8, 9 e 10)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs`, abas de Tratativas CSM (tiles "Adoção · Tratativas WhatsApp + Email", "Adoção · Tratativas Reunião", "Adoção · Contatos sem resposta", "Conversão clientes status Abandono"; nomes exatos das abas não confirmados nesta revisão — ver nota de reescopo na seção 4) · LookML: `north_star_tabelao.view.lkml` (`carteira_calc`, linhas 761-784; blocos 7-10, linhas 916-951)
> **Régua de contato da CSM** (escala a cada 2 dias, a partir do 5º dia sem uso): **Dia 5 e-mail · Dia 7 WhatsApp · Dia 9 ligação**. Antes disso (D1–D4) rodam trilhas de prevenção automáticas (in-app, e-mail, WhatsApp automático no D4). Do D5 em diante vale a classificação "Normal", "Alerta 1", "Alerta 2/3" (reincidência) e "Recuperado".
> **Definições das métricas:**
> - **Conversão WhatsApp + Email** = `respondeu ÷ teve contato` (contato = 1º e-mail **ou** WhatsApp enviado; resposta = `responded_at` **ou** `whatsapp_replied_at`, o que vier primeiro).
> - **Efetividade WhatsApp + Email** = `usando nos últimos 5 dias entre quem respondeu ÷ quem respondeu`.
> - **Contatos sem resposta** (Bloco 9) = `teve contato e nunca respondeu ÷ teve contato`.
> - **Conversão Reunião** = `reunião realizada ÷ agendou reunião`. **Cuidado**: o denominador `agendou_reuniao` conta **apenas** contas em `playbook_stage = 'Abandono'` que agendaram reunião (mesmo escopo da planilha operacional) — não é "todos os agendamentos".
> - **Efetividade Reunião** = `usando nos últimos 5 dias entre quem realizou reunião ÷ reuniões realizadas`. "Realizada" = `meeting_completed` em (`sim`, `s`, `realizada`).
> - **Reincidência [reincidentes] / participantes** = `reincidentes que realizaram reunião ÷ reuniões realizadas` (numerador é snapshot).
> - **Conversão clientes status "Abandono"** (Bloco 10) = `contas em Abandono que responderam ÷ contas em Abandono`.
> - No painel, as linhas de **reincidência após resposta** (Bloco 7, linhas 5-9) estão **fixadas em 0** de propósito: `risk_zone_recurrence_count` é contador snapshot sem a data de cada reincidência, então o filtro temporal "reincidiu **após** responder" não é computável. A query abaixo devolve a versão sem corte temporal, para ser lida com essa ressalva.

```sql
WITH carteira AS (
  SELECT
    nk_company_id,
    NULLIF(portfolio_entry_date, DATE '0001-01-01') AS entrada,
    NULLIF(churn_date,           DATE '0001-01-01') AS churn,
    LEAST(
      IFNULL(NULLIF(email_sent_at,    DATE '0001-01-01'), DATE '9999-12-31'),
      IFNULL(NULLIF(whatsapp_sent_at, DATE '0001-01-01'), DATE '9999-12-31')
    ) AS contato,
    LEAST(
      IFNULL(NULLIF(responded_at, DATE '0001-01-01'), DATE '9999-12-31'),
      IFNULL(DATE(NULLIF(whatsapp_replied_at, DATETIME '0001-01-01 00:00:00')), DATE '9999-12-31')
    ) AS respondeu,
    DATE(NULLIF(meeting_scheduled_at, DATETIME '0001-01-01 00:00:00')) AS reuniao_agendada,
    LOWER(TRIM(NULLIF(meeting_completed, 'unknown'))) AS reuniao_realizada_flag,
    LOWER(TRIM(NULLIF(playbook_stage,    'unknown'))) AS playbook_stage_norm,
    risk_zone_recurrence_count AS reincidencia_cnt
  FROM `contaazul-ssbi.gold_onboarding.dim_adoption_portfolio`
  WHERE SAFE.PARSE_DATE('%Y-%m', NULLIF(cohort_month, 'unknown')) = DATE_TRUNC(@inicio, MONTH)
),
uso_ult5dias AS (
  SELECT DISTINCT f.nk_company_id
  FROM `contaazul-ssbi.gold_capro_features.fact_capro_features` f
  WHERE f.any_feature = TRUE
    AND f.nk_date BETWEEN DATE_SUB(@fim, INTERVAL 5 DAY) AND @fim
),
agg AS (
  SELECT
    FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
    @fim AS data_referencia,
    COUNTIF(c.entrada <= @fim AND c.contato <= @fim) AS teve_contato,
    COUNTIF(c.entrada <= @fim AND c.contato <= @fim AND c.respondeu <= @fim) AS respondeu_contato,
    COUNTIF(c.entrada <= @fim AND c.contato <= @fim
            AND (c.respondeu IS NULL OR c.respondeu > @fim)) AS nunca_respondeu,
    COUNT(DISTINCT IF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim)
                      AND u.nk_company_id IS NOT NULL AND c.respondeu <= @fim,
                      c.nk_company_id, NULL)) AS usando_apos_resposta,
    COUNTIF(c.entrada <= @fim AND c.playbook_stage_norm = 'abandono' AND c.reuniao_agendada <= @fim) AS agendou_reuniao,
    COUNTIF(c.entrada <= @fim AND c.reuniao_agendada <= @fim
            AND c.reuniao_realizada_flag IN ('sim','s','realizada')) AS reuniao_realizada,
    COUNT(DISTINCT IF(c.entrada <= @fim AND (c.churn IS NULL OR c.churn > @fim)
                      AND u.nk_company_id IS NOT NULL
                      AND c.reuniao_realizada_flag IN ('sim','s','realizada'),
                      c.nk_company_id, NULL)) AS usando_apos_reuniao,
    COUNTIF(c.reincidencia_cnt > 0 AND c.reuniao_realizada_flag IN ('sim','s','realizada')) AS reincidentes_apos_reuniao,
    COUNTIF(c.entrada <= @fim AND c.playbook_stage_norm = 'abandono') AS status_abandono,
    COUNTIF(c.entrada <= @fim AND c.playbook_stage_norm = 'abandono' AND c.respondeu <= @fim) AS abandono_respondeu
  FROM carteira c
  LEFT JOIN uso_ult5dias u ON u.nk_company_id = c.nk_company_id
)
SELECT
  safra,
  data_referencia,
  teve_contato,
  respondeu_contato,
  nunca_respondeu,
  usando_apos_resposta,
  agendou_reuniao,
  reuniao_realizada,
  usando_apos_reuniao,
  reincidentes_apos_reuniao,
  status_abandono,
  abandono_respondeu,
  SAFE_DIVIDE(respondeu_contato,         teve_contato)      AS conversao_wpp_email_pct,
  SAFE_DIVIDE(usando_apos_resposta,      respondeu_contato) AS efetividade_wpp_email_pct,
  SAFE_DIVIDE(nunca_respondeu,           teve_contato)      AS contatos_sem_resposta_pct,
  SAFE_DIVIDE(reuniao_realizada,         agendou_reuniao)   AS conversao_reuniao_pct,
  SAFE_DIVIDE(usando_apos_reuniao,       reuniao_realizada) AS efetividade_reuniao_pct,
  SAFE_DIVIDE(reincidentes_apos_reuniao, reuniao_realizada) AS reincidencia_participantes_pct,
  SAFE_DIVIDE(abandono_respondeu,        status_abandono)   AS conversao_abandono_pct
FROM agg
```

### 3.12 Prontidão e satisfação do cliente — CSAT e aptidão D90 (Bloco 7, snapshot)
> Fonte: dashboard `onboarding_data_mart::north_star_onboarding_cs` / explore `north_star_carteira_cs` (bloco "Carteira CS · Posição Atual"; nome exato da aba não confirmado nesta revisão — ver nota de reescopo na seção 4) · LookML: `repos/looker/1-ONBOARDING/views/north_star_carteira_cs.view.lkml` (CTE `snapshot`, linhas 117-146) · Dataform: `repos/gcp-dataform-contaazul/definitions/gold/onboarding/tables/dim_adoption_portfolio.sqlx`
> **Definições de negócio:**
> - **[ISM] CSAT pós-setup** (meta ≥ 95%) = `respondentes com avaliação ≥ 4 de 5 ÷ total de respondentes`. Pesquisa via WhatsApp **24h após o setup**.
> - **[CSM] CSAT pós-call** (meta ≥ 95%) = mesma fórmula. Pesquisa via WhatsApp **2h após a call**.
> - **[CSM] % aptos para uso (D+90)** (peso 30%, meta 90%) = `clientes que responderam SIM à pesquisa ao final dos 90 dias ÷ total de clientes que tiveram setup`.
> - **[ISM] % aptos para uso** = `respondeu ≥ 4 de 5 em "você se sente pronto para usar?" ÷ total com setup concluído`. É o 3º critério do **handover ISM → CSM** (os três têm de coexistir: time sinaliza setup mínimo + treinamento; dados confirmam o setup; **cliente confirma explicitamente que se sente pronto**).
> - **Sem uso nos últimos 5 dias** (`no_usage_last_5_days`) e a contagem de reincidência em zona de risco também vivem aqui.
> - **Este bloco é SNAPSHOT**: os campos não têm data própria, então **não existe série histórica** — o valor é "hoje", qualquer que seja o eixo de data. Ele é ancorado no último dia do período apenas para caber no layout. Ver seção 4.
> - Ressalva registrada no código: `adoption_d90`/`d90_reached`/`no_usage_last_5_days` são **texto livre digitado pela CSM** na planilha; o match `'sim'`/`'s'` **não foi validado** contra os valores reais da coluna.

```sql
SELECT
  FORMAT_DATE('%Y-%m', DATE_TRUNC(@inicio, MONTH)) AS safra,
  @fim AS data_referencia_snapshot,
  COUNT(*)                                                                   AS contas_na_carteira,
  COUNTIF(LOWER(TRIM(NULLIF(d90_reached,  'unknown'))) IN ('sim','s'))       AS d90_atingido_sim,
  COUNTIF(LOWER(TRIM(NULLIF(adoption_d90, 'unknown'))) IN ('sim','s'))       AS adocao_d90_sim,
  COUNTIF(LOWER(TRIM(NULLIF(no_usage_last_5_days, 'unknown'))) IN ('sim','s')) AS sem_uso_5dias_sim,
  COUNTIF(risk_zone_recurrence_count > 0)                                    AS reincidentes,
  SUM(risk_zone_recurrence_count)                                            AS reincidencias_total,
  SUM(five_day_no_usage_blocks)                                              AS blocos_5dias_sem_uso,
  AVG(consecutive_days_no_usage)                                             AS media_dias_consec_sem_uso,
  COUNTIF(csat_post_setup    > 0)                                            AS respondentes_csat_pos_setup,
  COUNTIF(csat_post_csm_call > 0)                                            AS respondentes_csat_pos_call,
  AVG(IF(csat_post_setup    > 0, csat_post_setup,    NULL))                   AS csat_pos_setup_medio,
  AVG(IF(csat_post_csm_call > 0, csat_post_csm_call, NULL))                   AS csat_pos_call_medio,
  SAFE_DIVIDE(COUNTIF(csat_post_setup    >= 4), COUNTIF(csat_post_setup    > 0)) AS csat_pos_setup_pct_4_ou_5,
  SAFE_DIVIDE(COUNTIF(csat_post_csm_call >= 4), COUNTIF(csat_post_csm_call > 0)) AS csat_pos_call_pct_4_ou_5,
  SAFE_DIVIDE(COUNTIF(LOWER(TRIM(NULLIF(d90_reached, 'unknown'))) IN ('sim','s')), COUNT(*)) AS d90_atingido_pct
FROM `contaazul-ssbi.gold_onboarding.dim_adoption_portfolio`
WHERE SAFE.PARSE_DATE('%Y-%m', NULLIF(cohort_month, 'unknown')) = DATE_TRUNC(@inicio, MONTH)
```

### 3.13 Renewal (Bloco 1B) — não implementado
> Fonte: `north_star_tabelao.view.lkml`, linhas 953-955.
> O Bloco 1B ("Renewal - By Day" e "Renewal - By Day acumulado") existe no tabelão apenas como **placeholder com valores NULL**. **Não há SQL nem número** para renovação no painel hoje. As definições de negócio correlatas — **[CSM] % renovação da carteira em 90 dias (planos trimestrais)** = `clientes com plano trimestral que renovaram ÷ total com vencimento no período`, e **[CSM] % trimestrais que migraram para o plano anual** = `migrados ÷ total do plano trimestral` — **não estão implementadas**. Os insumos existem na carteira (`renewal_date`, `renewal_status`, `plan_recurrence`), mas como campos de snapshot sem histórico. Não invente número de Renewal a partir do painel.

## 4. Notas e Definições

### Definições fornecidas pelo usuário

Contexto: esta KB documenta o painel "North Star" da operação de Customer Success PME da Conta Azul (dashboard Looker 1199). O material abaixo vem do documento interno de produto "Nova Estrutura de Customer Success — PME" (Notion) — é a fonte de negócio das definições. Use-o para nomear e explicar as métricas; a aterragem técnica (tabelas, colunas, SQL) deve vir das fontes de código e do Looker.

PISTA DE ONDE ESTÁ O CÓDIGO (verifique, não confie): o dashboard 1199 é alimentado pelo projeto LookML em repos/looker/1-ONBOARDING/ — em especial as views north_star_tabelao.view.lkml (tabelão em formato longo: bloco/ordem/metrica/meta/realizado), north_star_carteira_cs.view.lkml (carteira de adoção do CS), north_star_funil_onboarding.view.lkml (funil e cobertura), north_star_adocao_carteira.view.lkml (adoção diária por safra) e north_star_meta_logo_churn.view.lkml (metas de churn). Essas views carregam derived tables com a SQL canônica das métricas — são a melhor fonte de verdade para reproduzir cada número. Leia-as por inteiro antes de escrever a KB.

OBJETIVO DO MODELO OPERACIONAL
Evoluir o modelo de implantação, que hoje garante ativação técnica, para um modelo focado em recorrência de uso sustentável ("Adoção"). A meta não é só ativar o cliente no ERP: é fazê-lo voltar a usar toda semana.

ESTRUTURA DO TIME
- ISM (Implementation Success Manager) — 16 pessoas — Setup e Treinamento.
- CSM (Customer Success Manager) — 3 pessoas — Adoção, monitora uso.
- Tools (Ferramentas) — 1 pessoa — Automações e CRM.
Critérios ISM: prioridade para clientes que querem ajuda 1:1; setup de 0 a 15 dias após a contratação.
Critérios CSM: clientes com 5 dias consecutivos sem usar nenhuma feature; prioridade para quem passou por onboarding 1:1, depois quem não quis onboarding; atuação até 90 dias após a contratação.

EXPERIMENTO A/B DE SETUP (5 semanas)
- Grupo Controle — 8 ISMs, 1 reunião. Playbook atual "MMA" (Minimamente Ativo). Solicita apenas arquivo OFX / dados da integração bancária. Na 1ª reunião: 7 features ativas. Objetivo: ativação técnica.
- Grupo Variante — 4 ISMs, 2 reuniões. Novo playbook, foco no ecossistema financeiro. Solicita integração bancária + planilha completa de Despesas Fixas. Na 1ª reunião: lançamento das despesas fixas + conciliação (baixa) ao vivo. A 2ª reunião é o eixo vendas, calibrado por tipo de negócio. Objetivo: geração de valor + autonomia de uso.
- Roteamento: o Salesforce cria o objeto "Playbook" e direciona para um ISM; a bifurcação A/B acontece na pré-reunião.
- Hipótese central: se o cliente sai da 1ª reunião com o eixo despesas rodando (despesas fixas cadastradas, integração bancária ativa e sabendo conciliar) e da 2ª com o eixo vendas calibrado, ele mantém o uso de forma autônoma e cria hábito de recorrência semanal.

HANDOVER ISM → CSM (3 critérios têm de coexistir)
1. Time sinaliza que executou setup mínimo + treinamento.
2. Dados confirmam o setup (eventos de configuração no produto).
3. Cliente confirma explicitamente que se sente pronto para usar.
Três níveis de setup: abaixo do mínimo (retreinamento), mínimo aceitável (monitorar uso), completo (monitoramento padrão).

FASE DE ADOÇÃO (CSM)
- Acompanhamento por até 90 dias após a contratação. A régua dos 5 dias úteis vale em qualquer ponto da janela.
- Dado interno: clientes com gap maior que 5 dias úteis têm queda drástica na renovação; gap de 22+ dias seguidos é caso quase perdido.
- Monitoramento D1 a D90. D1–D4: trilhas de prevenção (in-app, e-mail, WhatsApp automático no D4). D5+: lógica de alertas, com classificação "Normal", "Alerta 1", "Alerta 2/3" (reincidência) e "Recuperado".
- Régua de contato da CSM, escalando a cada 2 dias: Dia 5 e-mail, Dia 7 WhatsApp, Dia 9 ligação.
- Estimativa de carteira: ~460 clientes por CSM num ciclo de 90 dias.
- Fontes operacionais citadas: Salesforce, planilha de monitoramento, BigQuery (uso de features).

TABELA DE INDICADORES DE SUCESSO (definições oficiais, com fórmula, meta e peso)
- [Negócio] Churn (volume nominal) — total de contas canceladas no período ÷ meta definida no mês. Meta já definida oficialmente por FP&A. Peso 50%. É a meta primeira e mais central.
- [Estratégico] Cobertura — quantidade de clientes que tiveram 1:1 ÷ quantidade de clientes que pediram ajuda. Enquanto não há o número de quem solicita apoio, divide-se pelo total de vendas. Meta 80% (aspiracional). Peso 25%.
- [Estratégico] Adoção — no fechamento mensal: (clientes com uso nos últimos 5 dias úteis consecutivos do mês) ÷ (total de assinantes ativos). Meta 80% (aspiracional). Peso 25%. Conceito de churn: o cliente que entrou em churn sai da conta a partir do momento do churn.
- [ISM] (Cobertura) Meta de participantes por analista — (vendas do mês × 80%) ÷ headcounts disponíveis no mês, limitada a um teto de 70 clientes por analista. Peso 50%.
- [ISM] (Qualidade SETUP) % de clientes com uso em até 30 dias após o setup — (clientes que mantiveram uso contínuo, sem nenhuma lacuna de 5 ou mais dias úteis consecutivos, durante os primeiros 30 dias após o setup finalizado) ÷ (total de setups realizados) × 100. Meta 80%. Peso 50%.
- [CSM] (Adoção) % clientes convertidos para uso após 5 dias consecutivos sem usar — (clientes convertidos para uso após 5 dias consecutivos sem usar) ÷ (total na carteira ativa) × 100. Meta 90%; referência histórica: 75% voltam a usar organicamente, sem ação. Peso 50%.
- [CSM] (Adoção) Reincidência (voltaram à zona de risco) — (clientes que voltaram à zona de risco após intervenção) ÷ (total com contato realizado) × 100. Meta ≤40%; histórico ~78,1% voltam a ficar sem uso uma segunda vez dentro de 90 dias. Peso 20%.
- [CSM] (Qualidade) % aptos para uso (D+90) — (clientes que responderam SIM à pesquisa ao final dos 90 dias de onboarding) ÷ (total de clientes que tiveram setup). Meta 90%. Peso 30%.
- [ISM e CSM] % baixo fit detectado — (handovers com campo fit = "Baixo") ÷ (total de handovers) × 100. Health check.
- [ISM] (Qualidade) CSAT pós-setup — (respondentes com avaliação ≥4 de 5) ÷ (total de respondentes) × 100. Pesquisa via WhatsApp 24h após o setup. Meta ≥95%.
- [ISM] (Qualidade) % aptos para uso — (clientes que responderam ≥4 de 5 em "você se sente pronto para usar?") ÷ (total com setup concluído) × 100.
- [ISM] (Tempo de Cobertura) % setup concluído em até D15 — (total de setups realizados em até 15 dias) ÷ (total de setups realizados). Meta 85%. Peso 10%.
- [CSM] % renovação da carteira em 90 dias (planos trimestrais) — (clientes com plano trimestral que renovaram) ÷ (total com vencimento no período) × 100.
- [CSM] % trimestrais que migraram para o plano anual — (clientes migrados ÷ total de clientes do plano trimestral).
- [CSM] (Qualidade) CSAT pós-call — (respondentes com avaliação ≥4 de 5) ÷ (total de respondentes) × 100. Pesquisa via WhatsApp 2h após a call. Meta ≥95%.

LIMITAÇÃO DE GRÃO CONHECIDA (documente-a explicitamente na KB)
A carteira de adoção do CS chega como SNAPSHOT (uma linha por conta, deduplicada pela extração mais recente). Só há série histórica para campos que carregam data própria (entrada na carteira, churn, envio de e-mail, envio de WhatsApp, resposta, agendamento de reunião, conclusão do setup) — esses viram acumulado "até a data". Campos de estado sem data (aptidão D90, "sem uso nos últimos 5 dias", contagem de reincidência em zona de risco, situação atual, CSAT) NÃO têm histórico: plotá-los num eixo diário repete o valor de hoje em todo o passado. Quem consultar a KB precisa saber disso para não pedir série histórica de métrica de snapshot.

### Limitação de grão da carteira de CS (confirmada no código)

A limitação acima está **implementada e documentada** no LookML (`north_star_carteira_cs.view.lkml`, linhas 14-25; `north_star_tabelao.view.lkml`, linhas 651-659 e 734-736) e no Dataform (`dim_adoption_portfolio.sqlx`, grão = 1 linha por `nk_company_id`):

- **Tem série histórica** (viram acumulado "até a data"): `portfolio_entry_date`, `churn_date`, `email_sent_at`, `whatsapp_sent_at`, `responded_at`, `whatsapp_replied_at`, `meeting_scheduled_at`, `setup_completion_at`.
- **NÃO tem série histórica** (snapshot repetido em todo dia): `adoption_d90`, `d90_reached`, `no_usage_last_5_days`, `risk_zone_recurrence_count`, `five_day_no_usage_blocks`, `consecutive_days_no_usage`, `current_situation`, `csat_post_setup`, `csat_post_csm_call`.
- Contas **sem** `portfolio_entry_date` ficam **fora** do Bloco 6 (não há como datá-las) e aparecem só no bloco de snapshot.
- Para virar série real seria preciso um fact diário append-only (particionado por `_airbyte_extracted_at`) — **não existe hoje**. O gold **não expõe** `_airbyte_extracted_at`, então nem a data do snapshot é recuperável.
- **Exceção implementada**: `usando_ult5dias` / "sem uso nos últimos 5 dias" **do Bloco 6** foi reconstruído como série real a partir do `fact_capro_features` (janela móvel de 5 dias), justamente para não ficar cravado no valor de hoje. O campo `no_usage_last_5_days` da planilha continua snapshot.

### Notas de execução deste build

- ⚠ **SQL por tile do Looker indisponível**: a API do Looker devolveu `-- SQL unavailable: Bad json` para os 10 tiles do antigo dashboard 1199 (todos usam o mesmo explore `onboarding_data_mart / north_star_tabelao`, filtrado por `bloco`). As fórmulas desta KB foram portadas das **derived tables LookML em `repos/looker/`**, que são a fonte de verdade do explore — não de SQL devolvida pelo Looker.
- Metabase: nenhuma URL fornecida (0 fontes).
- As queries desta KB **não foram executadas** contra o BigQuery neste build (o builder não tem acesso ao MCP de BQ). Elas são portes diretos e verificáveis da SQL canônica do LookML, com o cálculo reposicionado para uma **data de referência única** (`@fim`) em vez da grade dia a dia.

### Nota de reescopo de fonte (patch — dashboard 1199 → `onboarding_data_mart::north_star_onboarding_cs`)

- O dashboard Looker **numérico `1199`** ("North Star · Onboarding/CS · v1") foi **descontinuado/substituído** pelo dashboard **LookML `onboarding_data_mart::north_star_onboarding_cs`**. Todas as citações "Fonte: dashboard 1199" nesta KB (seção 3, blocos 1 a 10 + bloco de snapshot da Carteira CS) foram atualizadas para apontar ao novo dashboard.
- **O que foi confirmado nesta revisão**: a URL de entrada trazia `tab_name=LogoChurn`, confirmando que a aba/tile do **Bloco 1 (Logo Churn & Winback)** no novo dashboard se chama **"LogoChurn"**.
- **O que NÃO foi possível confirmar nesta revisão**: o mapeamento exato de aba/tile para os Blocos 2 a 10 (Cobertura de Onboarding, HC/capacity, Tempo de Cobertura, Qualidade de Setup + A/B, Adoção/Carteira, Tratativas WhatsApp/E-mail/Reunião/Sem resposta, Conversão "Abandono") e para o snapshot da Carteira CS. Motivo: o MCP `looker_local` não estava registrado nesta sessão (nenhuma tool `mcp__looker_local__*` pôde ser carregada via ToolSearch — provável credencial ausente no `.env`), então não foi possível chamar `get_dashboard` no novo slug para listar os tiles/abas reais. `repos/looker/1-ONBOARDING/dashboards/` também não tem o `.dashboard.lookml` do painel versionado (só `.gitkeep`), então não há definição de layout no código para cross-referenciar.
- **Nenhuma fórmula/tabela mudou** nesta revisão: os 11 assuntos de negócio da ementa continuam mapeados às mesmas CTEs/measures em `north_star_tabelao.view.lkml` e demais views — a LookML é a mesma para os dois dashboards (o Looker permite reapontar o mesmo explore para telas/dashboards diferentes). O que mudou é **apenas o container/URL do dashboard**, não a semântica dos blocos.
- **Ação recomendada de acompanhamento**: quando o MCP `looker_local` estiver disponível, rodar `get_dashboard` em `onboarding_data_mart::north_star_onboarding_cs` e confirmar/corrigir os nomes de aba citados como "não confirmado nesta revisão" acima (blocos 2-10 e o snapshot da Carteira CS).

## 5. Glossário / Armadilhas

**Glossário**
- **Safra** — mês de aquisição do cliente (`DATE_TRUNC(MIN(metric_date) do NEW, MONTH)`). Não é o mês do evento.
- **Venda Direta / Recomendação** — os canais `SMB` e `ACC`. `BIZDEV` é somado a `SMB` no Bloco 1.
- **ISM / CSM / Tools** — Implementation Success Manager (setup/treino, D0-D15) / Customer Success Manager (adoção, até D90) / Ferramentas (automações e CRM).
- **Playbook** — objeto do Salesforce que carrega a jornada de onboarding do cliente. Suas etapas (`new_step`): `Criado`, `1ª Reunião`, `Finalização`, `Finalizado`, `Abandono`, `Não Compareceu`.
- **Streak / zona de risco** — nº de **dias úteis** consecutivos sem `any_feature`. `>= 5` = **zona de risco** (desengajado); `< 5` = engajado.
- **Apurado (pós-setup)** — cliente dentro da janela D+1..D+30 após a finalização do setup.
- **Cobertura** — proporção de clientes que receberam atendimento 1:1 (proxy: avançaram além da 1ª reunião ou concluíram).
- **Winback** — cliente recuperado depois do churn (`+1` no Logo Churn net).
- **`ate_data` / "acumulado até a data"** — sufixo que marca métrica cumulativa dentro da safra; **nunca somar entre dias**.

**Armadilhas**
1. **Não peça série histórica de métrica de snapshot.** CSAT, aptidão D90, `no_usage_last_5_days`, reincidência em zona de risco e situação atual **não têm data**. Plotados num eixo diário, repetem o valor de hoje em todo o passado. Números desses campos são sempre "posição atual".
2. **Nunca somar métricas `_ate_data` entre dias.** Elas já são cumulativas — somar duplica. No LookML as measures correspondentes usam `max`/`average`, nunca `sum` (ex.: `hc_ativo` se repete por linha de `owner_finalizacao`/`canal`/`grupo` no mesmo dia → use média; `meta_total_mes` se repete por dia do mês → use média).
3. **`# Cobertura (HC)` tem duas fórmulas divergentes no código.** O **tabelão** (o que alimenta o dashboard `onboarding_data_mart::north_star_onboarding_cs`, antigo `1199`) usa `concluiu ÷ (total_safra × 0,8)`; a view `north_star_funil_onboarding` usa `concluiu ÷ HC planejado (14)`. Um comentário no tabelão registra que o tooltip do HTML dizia "÷ HC Combinado", mas **os dados batem com `÷ (total_safra × 0,8)`**. Para reproduzir o painel, use a fórmula do tabelão.
4. **`% engajados` (Adoção, Bloco 6) também tem duas fórmulas.** Tabelão (painel): `1 − streak_5d_no_final ÷ base_acumulada`. View `north_star_adocao_carteira`: `ativos ÷ (base_acumulada − churn_acum − overdue_acum)`. São números diferentes; a do tabelão é a que o dashboard mostra.
5. **Meta de Logo Churn: FQN divergente.** A LookML lê `contaazul-ssbi.gold_churn.dim_churn_targets`, mas o Dataform materializa a mesma tabela como `contaazul-ssbi.gold_onboarding.dim_meta_logo_churn_targets` (colunas idênticas: `nk_date`, `meta_churn_acc`, `meta_churn_smb`, `meta_churn_total`, `nk_year_month`). Se a query 3.2 falhar com "table not found", troque o FQN pelo segundo. E lembre do **sinal**: a tabela guarda positivo, o painel usa negativo.
6. **Meta de churn: valor do mês ≠ valor acumulado.** `Meta` (cabeçalho) é a soma do **mês inteiro**; `target (meta acumulada)` é a soma **até a data**. Confundir os dois inverte a leitura de `vs target`.
7. **`vs target` inverte no Bloco 1.** Logo Churn é KPI negativo: `vs target = meta ÷ realizado` e **≥ 100% é bom**. Nos outros blocos é `(realizado ÷ meta) − 1` e **positivo é bom**.
8. **O denominador de "Conversão Reunião" é restrito.** `agendou_reuniao` conta **só** contas em `playbook_stage = 'Abandono'` que agendaram reunião (escopo copiado da planilha operacional). Não leia como "todos os agendamentos da carteira".
9. **Reincidência após resposta está fixada em 0 no painel** (Bloco 7, linhas 5-9) porque `risk_zone_recurrence_count` não tem a data de cada reincidência. Não é "zero reincidência" — é **não computável**.
10. **`# sim forms D90` está zerado em julho** no gold. Leia como "sem dado", não como 0% de aptidão.
11. **Sentinelas em vez de NULL.** Na `dim_adoption_portfolio`, data ausente = `0001-01-01`, texto ausente = `'unknown'`, número ausente = `0`. Filtrar por `IS NULL` **não** funciona — é preciso `NULLIF(...)`. E `mrr_amount` volta `0` quando a planilha traz moeda corrompida (ex.: `'R$ 20.245.333.333.333.300,00'`).
12. **Flags `sim`/`s` são texto livre da planilha.** O próprio LookML avisa que o match `LOWER(TRIM(x)) IN ('sim','s')` para `adoption_d90`/`d90_reached`/`no_usage_last_5_days` **não foi validado** contra os valores reais. Confira antes de confiar.
13. **A régua de 5 dias é em DIAS ÚTEIS** (`dim_date.work_day > 0`), não em dias corridos. Já os prazos de setup (**15 dias**) e a janela pós-setup (**30 dias**) são em **dias corridos**.
14. **Gate de observabilidade da zona de risco.** Cliente com menos de **5 dias úteis** OU menos de **7 dias corridos** de vida nunca é marcado como em risco (fix de 20/07). Não estranhe safra recém-aberta com zero em risco.
15. **`churn_acum` do Bloco 6 é líquido; `overdue_acum` é pontual.** O primeiro desconta quem voltou (winback/reativação); o segundo é a foto do dia, não um acumulado. Não some `overdue_acum` ao longo do mês.
16. **Piso fixo 2026-06-01.** Todas as views cortam em `2026-06-01`. A safra de um cliente cuja primeira venda foi antes disso é **atribuída a junho/2026** — a coorte não é o "primeiro NEW da história", é o primeiro NEW **desde o piso**. Também por isso o `Δ mês anterior` de junho/2026 vem vazio.
17. **`Finalização` ≠ `Finalizado`.** `Finalização` é etapa intermediária e conta como "avançou além da 1ª reunião"; só `Finalizado` conta como setup concluído.
18. **"Agendado" e "realizado" são tratados como a mesma coisa** na etapa de 1ª reunião do funil (decisão do time). E `inscrito_sem_agendamento` foi corrigido na v5 para olhar `fact_playbook_agenda_lifecycle` (antes inflava: 49→5 em junho, 150→2 em julho).
19. **`total_safra` é sensível ao horário da leitura** — a base de aquisição é atualizada ao longo do dia. Divergir de um print anterior do mesmo dia **não é bug**.
20. **Bloco 1B (Renewal) é placeholder vazio.** Não há SQL nem número de renovação no painel. Renovação de trimestrais e migração trimestral→anual **não estão implementadas**.
21. **O teto de 70 clientes/analista não está no código.** `Cobertura - Conclusão Onboarding (HC)` é `ROUND(total_safra × 0,8 ÷ 14)` sem `LEAST(..., 70)`. Se a regra de negócio importar, aplique o teto explicitamente.
22. **`HC Realizado` é ruidoso**: é a contagem distinta de `playbook_owner_name` **com transição no dia**. Num dia sem movimento (feriado, fim de semana) despenca sem que o time tenha mudado. `HC Combinado (fixo) = 14` é decisão de negócio, fora da query.
23. **`batch_lifecycle_migrations` no Bloco 1 é código morto**: é sempre rotulada `LOTE` e depois filtrada fora. Não a inclua esperando que mude o churn de SMB/ACC.
24. **`total_aptos_para_carteira` (view `north_star_adocao_carteira`) está marcada `[CONFIRMAR]`** no próprio LookML — a regra dos 5 critérios pode ter mudado. Não use como número oficial sem confirmar com o time.
25. **Grão do tabelão**: uma linha por `(safra, dia, bloco, ordem, metrica)`, e o filtro final é `DATE_TRUNC(dia, MONTH) = safra` — uma safra **nunca** aparece em dias de outro mês. Por isso não existe "safra de junho em julho" no painel.

<!-- FIM DA KB northstar -->
