"""Registro da homologação com a equipe (11B) — Fase 15.

`gerar`: planilha CSV (abre no LibreOffice/Excel) com os cenários do PLANO_VALIDACAO_SALAO.md
(fonte única), carimbada com a versão e a impressão digital testadas, e TODOS os campos de resultado
em branco. Gravada FORA do repositório (terá nomes de pessoas), com permissão 0600.
`conferir`: só confere o que pessoas preencheram — nunca preenche, nunca aprova sozinho.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
import re

from rocha_reis_agent.ops.backup import BackupError
from rocha_reis_agent.ops.release import ROOT

PLAN = ROOT / "docs" / "PLANO_VALIDACAO_SALAO.md"
RESULTS = ("Aprovado", "Reprovado", "Não executado")
FIELDS = ("versao_testada", "impressao_digital", "cenario", "perfil", "objetivo", "pre_condicoes", "passos",
          "resultado_esperado", "criterio", "data", "participante", "resultado_observado", "situacao",
          "dificuldades", "defeitos", "sugestoes", "acompanhado_por")
PROBLEM_FIELDS = ("numero", "cenario", "papel_de_quem_achou", "o_que_aconteceu", "tipo", "gravidade",
                  "decisao_posterior", "responsavel")
FILLED_BY_PEOPLE = ("data", "participante", "resultado_observado", "situacao", "dificuldades", "defeitos",
                    "sugestoes", "acompanhado_por")
KEY_SCENARIOS = ("AT-01", "AT-04", "AT-05", "AT-08", "AT-09", "AT-10", "AT-16",
                 "AD-03", "AD-04", "AD-06", "AD-10", "AD-13")  # seção 7 do plano


def scenarios(plan: Path = PLAN) -> list[dict[str, str]]:
    rows = []
    for line in plan.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\| (AT|AD)-\d{2} \|", line):
            continue
        cells = [c.strip().replace("`", "").replace("**", "") for c in line.strip().strip("|").split("|")]
        identifier, objective, pre, steps, expected, _real, _note, criterion = cells[:8]
        rows.append({"cenario": identifier, "perfil": "Atendente" if identifier.startswith("AT") else "Administração",
                     "objetivo": objective, "pre_condicoes": pre, "passos": steps,
                     "resultado_esperado": expected, "criterio": criterion})
    return rows


def _outside_repository(path: Path) -> Path:
    path = Path(path).absolute()
    if path == ROOT or ROOT in path.parents:
        raise BackupError("Grave o registro FORA do repositório: ele terá nomes de pessoas e não deve ser versionado.")
    return path


def generate(destination: Path) -> dict[str, object]:
    from rocha_reis_agent.ops.release import release_info
    destination = _outside_repository(destination)
    if destination.exists():
        raise BackupError("O arquivo já existe; um registro preenchido nunca é sobrescrito.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = release_info()
    rows = scenarios()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({**{f: "" for f in FIELDS}, **row, "versao_testada": info["versao"],
                             "impressao_digital": info["impressao_digital_conteudo"]})
    problems = destination.with_name(destination.stem + "-problemas.csv")
    descriptor = os.open(problems, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
        csv.DictWriter(handle, fieldnames=PROBLEM_FIELDS, delimiter=";").writeheader()
    return {"registro": str(destination), "problemas": str(problems), "cenarios": len(rows),
            "versao": info["versao"], "impressao_digital": info["impressao_digital_conteudo"],
            "aviso": "Preencha durante a sessão, com os participantes. Nenhum resultado vem preenchido."}


def check(path: Path) -> dict[str, object]:
    """Confere o preenchimento. Não decide a aprovação: só mostra o que está completo, faltando ou inválido."""
    from rocha_reis_agent import __version__
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    known = {s["cenario"] for s in scenarios()}
    problems, pending, summary = [], [], {"Aprovado": 0, "Reprovado": 0, "Não executado": 0}
    key_status: dict[str, list[str]] = {k: [] for k in KEY_SCENARIOS}
    for number, row in enumerate(rows, start=2):  # linha 1 = cabeçalho
        scenario = row.get("cenario", "")
        if scenario not in known:
            problems.append(f"linha {number}: cenário desconhecido '{scenario}'")
            continue
        status = row.get("situacao", "").strip()
        if not status:
            pending.append(scenario)
            continue
        if status not in RESULTS:
            problems.append(f"linha {number} ({scenario}): situação '{status}' inválida (use {', '.join(RESULTS)})")
            continue
        summary[status] += 1
        missing = [f for f in ("data", "participante", "acompanhado_por") if not row.get(f, "").strip()]
        if status != "Não executado" and not row.get("resultado_observado", "").strip():
            missing.append("resultado_observado")
        if missing:
            problems.append(f"linha {number} ({scenario}): faltam {', '.join(missing)}")
        if row.get("versao_testada") != __version__:
            problems.append(f"linha {number} ({scenario}): versão testada {row.get('versao_testada')} "
                            f"diferente da atual {__version__}")
        if scenario in key_status:
            key_status[scenario].append(status)
    return {"linhas": len(rows), "resumo": summary, "sem_resultado": pending, "problemas": problems,
            "cenarios_chave_secao_7": {k: v or ["sem registro"] for k, v in key_status.items()},
            "aviso": "Conferência de preenchimento. A decisão de aprovação é do responsável pelo salão (seção 7)."}
