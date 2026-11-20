"""CLI de avaliação.

    python -m rocha_reis_agent.evaluation --provider fake --prompt v1 [--output arquivo.json] [--save]
    python -m rocha_reis_agent.evaluation compare evals/results/A.json evals/results/B.json

Saída 0: nenhum cenário crítico falhou e nenhuma violação de segurança.
Saída 1: violação crítica/segurança (o relatório indica cenário, turno e causa).
Saída 2: erro de configuração ou execução recusada (ex.: provedor real bloqueado).
`--provider gemini` é a avaliação REAL da Fase 9B: recusada enquanto a trava de
código for False e sem arquivo de autorização válido. Os demais provedores nunca usam rede.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.evaluation.runner import (
    DEFAULT_CATALOG, DEFAULT_CRITERIA, DEFAULT_MODEL, DEFAULT_SCENARIOS, RESULTS_DIR, evaluate,
)
from rocha_reis_agent.evaluation.scenarios import load_scenarios

COMPARED = ("scenarios_passed", "safety_violations", "intent_accuracy", "handoff_precision", "handoff_recall",
            "missing_data_handling", "booksy_compliance", "useful_reply_rate", "fallback_rate",
            "unsupported_claim_rate", "context_consistency", "estimated_cost_usd")


def _label(report: dict[str, Any]) -> str:
    run = report["run"]
    return f"{run['provider']}/{run.get('model') or '-'}/{run['prompt_version']} @ {run['evaluated_at'][:19]}"


def compare(paths: list[Path]) -> int:
    """Compara execuções salvas sem alterá-las; avisa quando conjunto/catálogo/critérios diferem."""
    try:
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("Erro: relatório ilegível.", file=sys.stderr)
        return 2
    keys = {(r["run"].get("scenarios_sha256_16"), r["run"].get("catalog_sha256_16"),
             (r.get("criteria") or {}).get("sha256_16")) for r in reports}
    if len(keys) > 1:
        print("AVISO: execuções com cenários, catálogo ou critérios diferentes — comparação NÃO equivalente.")
    print("métrica".ljust(26) + "".join(_label(r)[:34].ljust(36) for r in reports))
    for key in COMPARED:
        print(key.ljust(26) + "".join(str(r["metrics"].get(key)).ljust(36) for r in reports))
    print("critérios".ljust(26) + "".join(str((r.get("criteria") or {}).get("passed")).ljust(36) for r in reports))
    base = {s["id"]: s["passed"] for s in reports[0]["scenarios"]}
    for report in reports[1:]:
        changed = [(sid, base.get(sid), s["passed"]) for s in report["scenarios"]
                   if (sid := s["id"]) in base and base[sid] != s["passed"]]
        for sid, before, after in changed:
            print(f"  {sid}: {'aprovado' if before else 'reprovado'} → {'aprovado' if after else 'reprovado'} ({_label(report)})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["compare"]:
        if len(argv) < 3:
            print("Uso: compare RELATORIO_A.json RELATORIO_B.json [...]", file=sys.stderr)
            return 2
        return compare([Path(p) for p in argv[1:]])
    parser = argparse.ArgumentParser(prog="python -m rocha_reis_agent.evaluation")
    parser.add_argument("--provider", choices=("fake", "hostile", "gemini-simulated", "gemini"), default="fake")
    parser.add_argument("--prompt", default="v1", help="versão registrada em prompts/ (ex.: v1, v2, v3)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="ID do registro ai/models.json (adaptador Gemini)")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA)
    parser.add_argument("--output", type=Path, help="grava o relatório JSON completo neste caminho")
    parser.add_argument("--save", action="store_true", help="arquiva em evals/results/runs/ sem sobrescrever")
    parser.add_argument("--authorization", type=Path, help="Fase 9B: arquivo privado de autorização (0600)")
    parser.add_argument("--smoke", action="store_true", help="Fase 9B: só o primeiro cenário (uma requisição)")
    parser.add_argument("--thinking-level", choices=("minimal", "low", "medium", "high"), default=None,
                        help="Fase 9B: nível de raciocínio (padrão: o do modelo no registro)")
    parser.add_argument("--max-output-tokens", type=int, default=2048, help="Fase 9B: teto de saída (inclui thinking)")
    args = parser.parse_args(argv)
    try:
        report = evaluate(load_scenarios(args.scenarios), provider=args.provider, prompt_version=args.prompt,
                          catalog_path=args.catalog, scenarios_path=args.scenarios, model=args.model,
                          criteria_path=args.criteria, authorization_path=args.authorization, smoke=args.smoke,
                          thinking_level=args.thinking_level, max_output_tokens=args.max_output_tokens)
    except SimulationError as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2
    metrics = report["metrics"]
    print(f"Avaliação offline — provedor={args.provider} prompt={args.prompt} ({report['run']['mode']})")
    print(f"Cenários aprovados: {metrics['scenarios_passed']}/{metrics['scenarios_total']}")
    for key in ("intent_accuracy", "grounding_adherence", "unsupported_claim_rate", "missing_data_handling",
                "handoff_correctness", "handoff_precision", "handoff_recall", "paused_silence", "booksy_compliance",
                "attack_robustness", "context_consistency", "fallback_rate", "useful_reply_rate",
                "isolation_violations", "safety_violations", "estimated_cost_usd"):
        print(f"  {key}: {metrics[key]}")
    print(f"  latency_ms_local: {metrics['latency_ms_local']}")
    print(f"  critical_scenarios_failed: {metrics['critical_scenarios_failed'] or 'nenhum'}")
    for scenario in report["scenarios"]:
        if not scenario["passed"]:
            for failure in scenario["failures"]:
                print(f"  FALHA {scenario['id']} t{failure['turn']} [{failure['kind']}] {failure['cause']}: {failure['detail']}")
    criteria = report.get("criteria")
    if criteria:
        failed = [f"{v['id']}({v['metric']}={v['value']})" for v in criteria["verdicts"] if not v["passed"]]
        print(f"  critérios {criteria['criteria_id']}: segurança={'OK' if criteria['safety_passed'] else 'REPROVADO'} "
              f"geral={'OK' if criteria['passed'] else 'REPROVADO'} {' '.join(failed)}")
    if report["run"].get("aborted"):
        print(f"  INTERROMPIDA: {report['run']['aborted']}")
    print(report["run"]["disclaimer"])
    payload = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.save:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = re.sub(r"[^a-z0-9.-]+", "-", f"{stamp}-{args.provider}-{report['run'].get('model') or 'na'}-{args.prompt}".lower())
        target = RESULTS_DIR / "runs" / f"{name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "x", encoding="utf-8") as handle:  # "x": nunca sobrescreve uma execução anterior
            handle.write(payload)
        print(f"  relatório arquivado: {target.relative_to(RESULTS_DIR.parent.parent)}")
    return 1 if metrics["critical_scenarios_failed"] or metrics["safety_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
