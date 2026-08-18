#!/usr/bin/env python3
"""Checa a integridade de um kb-layer.md (camada semantica estruturada).

Verificacoes (deterministicas, sem dependencias externas):
  1. todo bloco ```yaml meta``` tem `kind` e `id`
  2. `kind` esta entre os 7 validos (`expectation` e' explicitamente proibido)
  3. `id` no formato esperado: kebab-case (snake_case tambem em `source`)
  4. `id` nao derivado de posicao (`q1-...`) - numeracao da fonte nao e' endereco
  5. nenhum `id` duplicado
  6. nenhuma referencia pendurada (id citado que nao existe), tanto em lista
     inline `[a, b]` quanto em lista YAML em bloco (`- a`)
  7. todo `report` tem query executavel (`SELECT` + `FROM`); fragmento de
     formula e' measure, nao report
  8. SQL: toda query do kb.md aparece VERBATIM no layer (identidade, nao contagem)
  9. `in_scope`, quando presente, e' literal `true` ou `false` (campo opcional -
     ausencia nao e' violacao)

Uso:  python scripts/check-kb-layer.py <kb-layer.md> [kb.md]
Saida: relatorio legivel + exit 1 se houver violacao.
"""
import difflib
import io
import re
import sys

# os unicos kinds que a camada aceita (ver .claude/agents/kb-restructurer.md)
KINDS = ("source", "policy", "measure", "report", "pitfall", "term", "note")

# campos cujo valor e' uma lista de ids
LIST_REFS = (
    "applies_to", "policies", "composed_of", "pitfalls",
    "sources", "measures", "provenance",
)
# campos cujo valor e' um id unico
SCALAR_REFS = ("source", "enforced_by", "scoped_by", "quantified_by")

BLOCK_RE = re.compile(r"^```yaml meta\s*$(.*?)^```\s*$", re.M | re.S)
FRONTMATTER_RE = re.compile(r"\A---\s*$(.*?)^---\s*$", re.M | re.S)
ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ID_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")  # source usa snake_case da tabela
# id que carrega numeracao da fonte: quebra toda referencia se a fonte reordenar
POSICIONAL_RE = re.compile(r"^(q|qry|query|metrica|item|bloco|report)\d+([-_]|$)", re.I)
META_SPLIT_RE = re.compile(r"^```yaml meta\s*$", re.M)
SQL_DEPOIS_RE = re.compile(r"^```sql\s*$\n(.*?)(?:^```\s*$|\Z)", re.M | re.S)


def ler(path):
    # utf-8-sig: arquivo salvo com BOM no Windows nao pode derrubar o frontmatter
    return io.open(path, encoding="utf-8-sig").read()


def clean(token):
    return token.strip().strip("\"'").strip()


# ---------------------------------------------------------------- blocos yaml

def parse(texto):
    definidos, kinds, problemas = set(), {}, []

    fm = FRONTMATTER_RE.search(texto)
    if fm:
        for linha in fm.group(1).splitlines():
            m = re.match(r"\s*-\s*id:\s*(.+)$", linha)
            if m:
                definidos.add(clean(m.group(1)))

    refs = []  # (id, campo, kind do bloco)
    for bloco in BLOCK_RE.findall(texto):
        kind = bid = in_scope_val = None
        lista = None  # campo de LIST_REFS aberto em estilo bloco
        for linha in bloco.splitlines():
            item = ITEM_RE.match(linha)
            if lista and item:
                token = item.group(1)
                if not token.startswith("{"):  # `- {name: x}` e' mapa, nao id
                    refs.append((clean(token), lista, kind))
                continue

            m = re.match(r"(\w+):\s*(.*)$", linha)
            if not m:
                continue
            lista = None  # nova chave de topo fecha a lista anterior
            chave, val = m.group(1), m.group(2)

            if chave == "kind" and kind is None:
                kind = clean(val)
            elif chave == "id" and bid is None:
                bid = clean(val)
            elif chave == "in_scope" and in_scope_val is None:
                in_scope_val = clean(val)
            elif chave in LIST_REFS:
                inner = val.strip()
                if inner.startswith("[") and inner.endswith("]"):
                    refs += [(clean(t), chave, kind) for t in inner[1:-1].split(",") if clean(t)]
                elif not inner:
                    lista = chave  # lista em bloco: itens vem nas proximas linhas
            elif chave in SCALAR_REFS and val.strip() and not val.strip().startswith("["):
                refs.append((clean(val), chave, kind))

        # kind e' checado antes do bail-out: bloco proibido sem id merece a
        # mensagem especifica, nao o generico "sem kind/id"
        if kind is not None and kind not in KINDS:
            onde = "'%s'" % bid if bid else "(sem id)"
            if kind == "expectation":
                problemas.append(
                    "kind proibido: 'expectation' no bloco %s "
                    "(valor de referencia nao entra na camada)" % onde)
            else:
                problemas.append(
                    "kind invalido: '%s' no bloco %s (validos: %s)"
                    % (kind, onde, ", ".join(KINDS)))

        if kind is None or bid is None:
            problemas.append("bloco sem kind/id (kind=%s, id=%s)" % (kind, bid))
            continue

        regex = ID_SOURCE_RE if kind == "source" else ID_RE
        if not regex.match(bid):
            problemas.append(
                "id fora do formato: '%s' (kind %s; esperado %s)"
                % (bid, kind, "snake/kebab-case" if kind == "source" else "kebab-case"))

        if POSICIONAL_RE.match(bid):
            problemas.append(
                "id posicional: '%s' - derive do titulo, nao da numeracao da fonte "
                "(reordenar a fonte quebraria toda referencia a ele)" % bid)

        if bid in definidos:
            problemas.append("id duplicado: %s" % bid)
        definidos.add(bid)
        kinds[kind] = kinds.get(kind, 0) + 1

        if in_scope_val is not None and in_scope_val not in ("true", "false"):
            problemas.append(
                "in_scope invalido: '%s' no bloco '%s' (deve ser literal true ou false)"
                % (in_scope_val, bid))

    penduradas = sorted({(r, campo) for r, campo, _ in refs if r not in definidos})
    return definidos, kinds, problemas, penduradas


# ------------------------------------------------------------------ blocos sql

def reports_sem_query(texto):
    """`report` cujo bloco ```sql seguinte nao e' query executavel (SELECT + FROM).

    A cerca ```sql tambem e' usada para fragmento de formula; esse conteudo e'
    `expr` de measure, nao relatorio. Emitir como report duplica o fato.
    """
    ruins = []
    # split pelo fence de meta: cada parte vai ate o proximo bloco meta,
    # entao o ```sql encontrado aqui e' mesmo o deste report
    for parte in META_SPLIT_RE.split(texto)[1:]:
        corpo, _, resto = parte.partition("\n```")
        if not re.search(r"^kind:\s*report\s*$", corpo, re.M):
            continue
        m = re.search(r"^id:\s*(\S+)", corpo, re.M)
        bid = clean(m.group(1)) if m else "(sem id)"
        sql = SQL_DEPOIS_RE.search(resto)
        corpo_sql = sql.group(1) if sql else ""
        tem = re.search(r"\bSELECT\b", corpo_sql, re.I) and re.search(r"\bFROM\b", corpo_sql, re.I)
        if not tem:
            ruins.append(bid)
    return ruins


def queries(texto):
    """Corpo de cada bloco ```sql. Tolera cerca final nao fechada (vai ate o EOF)."""
    out, cur = [], None
    for linha in texto.split("\n"):
        s = linha.strip()
        if cur is None:
            if s == "```sql":
                cur = []
            continue
        if s == "```":
            out.append(cur)
            cur = None
        else:
            cur.append(linha)
    if cur is not None:
        out.append(cur)
    return out


def normalizar(linhas):
    """Ignora espaco no fim da linha e linhas vazias nas bordas do bloco."""
    n = [l.rstrip() for l in linhas]
    while n and not n[0]:
        n.pop(0)
    while n and not n[-1]:
        n.pop()
    return n


def explicar(q, candidatas):
    """Diff curto contra a query mais parecida que ainda nao casou."""
    if not candidatas:
        return ["nenhuma query sobrando no layer para comparar"]
    alvo = "\n".join(q)
    ratios = [(difflib.SequenceMatcher(None, alvo, "\n".join(c)).ratio(), c) for c in candidatas]
    ratio, melhor = max(ratios, key=lambda x: x[0])
    if ratio < 0.5:
        return ["nenhuma query semelhante no layer - provavelmente ausente"]
    saida = ["mais parecida no layer difere assim (- kb.md / + layer):"]
    for d in difflib.unified_diff(q, melhor, lineterm="", n=0):
        if d.startswith(("---", "+++", "@@")):
            continue
        if len(saida) > 6:
            saida.append("    ...")
            break
        saida.append("    %s" % d[:120])
    return saida


def executavel(linhas):
    txt = "\n".join(linhas)
    return bool(re.search(r"\bSELECT\b", txt, re.I) and re.search(r"\bFROM\b", txt, re.I))


def comparar_sql(q_orig, q_layer):
    """Para cada query EXECUTAVEL do kb.md, exige uma identica no layer.

    Fragmento de formula (sem SELECT/FROM) fica de fora: ele nao e' query
    validada, e por contrato vira `expr` de measure - nao ha cerca ```sql
    correspondente no layer.
    """
    usados, faltando = set(), []
    for i, q in enumerate(q_orig):
        achou = False
        for j, l in enumerate(q_layer):
            if j not in usados and l == q:
                usados.add(j)
                achou = True
                break
        if not achou:
            sobrando = [l for j, l in enumerate(q_layer) if j not in usados]
            faltando.append((i, q, explicar(q, sobrando)))
    return faltando


# ------------------------------------------------------------------------ main

def main():
    if len(sys.argv) < 2:
        print("uso: python scripts/check-kb-layer.py <kb-layer.md> [kb.md]")
        return 2

    layer = sys.argv[1]
    texto = ler(layer)
    definidos, kinds, problemas, penduradas = parse(texto)
    q_layer = [normalizar(q) for q in queries(texto)]

    resumo = ", ".join("%s=%d" % (k, v) for k, v in sorted(kinds.items())) or "(nenhum)"
    print("blocos por kind: %s" % resumo)
    print("ids definidos: %d" % len(definidos))
    print("blocos sql: %d" % len(q_layer))

    violacoes = 0

    if len(sys.argv) > 2:
        todos = [normalizar(q) for q in queries(ler(sys.argv[2]))]
        q_orig = [q for q in todos if executavel(q)]
        fragmentos = len(todos) - len(q_orig)
        faltando = comparar_sql(q_orig, q_layer)
        ok = len(q_orig) - len(faltando)
        print("cobertura sql: %d/%d queries do kb.md verbatim - %s   "
              "(ignora espaco no fim da linha; %d fragmento(s) sem SELECT/FROM fora da conta)"
              % (ok, len(q_orig), "ok" if not faltando else "DIVERGENTE", fragmentos))
        for idx, q, detalhe in faltando:
            print("  X query %d do kb.md nao aparece verbatim no layer" % (idx + 1))
            print("      inicio: %s" % (q[0][:100] if q else "(bloco vazio)"))
            for linha in detalhe:
                print("      %s" % linha)
            violacoes += 1

    for bid in reports_sem_query(texto):
        print("  X report sem query executavel: '%s' (sem SELECT/FROM) - "
              "fragmento de formula e' expr de measure, nao report" % bid)
        violacoes += 1

    for p in problemas:
        print("  X %s" % p)
        violacoes += 1
    for ref, campo in penduradas:
        print("  X referencia pendurada: '%s' (campo %s) nao existe" % (ref, campo))
        violacoes += 1

    print("OK - layer integro" if violacoes == 0 else "%d violacao(oes)" % violacoes)
    return 1 if violacoes else 0


if __name__ == "__main__":
    sys.exit(main())
