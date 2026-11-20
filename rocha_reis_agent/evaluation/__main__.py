"""CLI: python -m rocha_reis_agent.evaluation --provider fake --prompt v1 [--output arquivo.json]

Saída 0: nenhum cenário crítico falhou e nenhuma violação de segurança.
Saída 1: violação crítica/segurança (o relatório indica cenário, turno e causa).
Nunca chama serviços externos: o provedor real não é selecionável aqui.
"""

import argparse
import json
from pathlib import Path
import sys

from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.evaluation.runner import DEFAULT_CATALOG, DEFAULT_SCENARIOS, evaluate
from rocha_reis_agent.evaluation.scenarios import load_scenarios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rocha_reis_agent.evaluation")
    parser.add_argument("--provider", choices=("fake", "hostile", "anthropic-simulated"), default="fake")
    parser.add_argument("--prompt", default="v1", help="versão registrada em prompts/ (ex.: v1, v2)")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, help="grava o relatório JSON completo")
    args = parser.parse_args(argv)
    try:
        report = evaluate(load_scenarios(args.scenarios), provider=args.provider, prompt_version=args.prompt,
                          catalog_path=args.catalog, scenarios_path=args.scenarios)
    except SimulationError as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2
    metrics = report["metrics"]
    print(f"Avaliação offline — provedor={args.provider} prompt={args.prompt} ({report['run']['mode']})")
    print(f"Cenários aprovados: {metrics['scenarios_passed']}/{metrics['scenarios_total']}")
    for key in ("intent_accuracy", "grounding_adherence", "unsupported_claim_rate", "missing_data_handling",
                "handoff_correctness", "paused_silence", "booksy_compliance", "attack_robustness",
                "context_consistency", "isolation_violations", "safety_violations", "estimated_cost_usd"):
        print(f"  {key}: {metrics[key]}")
    print(f"  latency_ms_local: {metrics['latency_ms_local']}")
    print(f"  critical_scenarios_failed: {metrics['critical_scenarios_failed'] or 'nenhum'}")
    for scenario in report["scenarios"]:
        if not scenario["passed"]:
            for failure in scenario["failures"]:
                print(f"  FALHA {scenario['id']} t{failure['turn']} [{failure['kind']}] {failure['cause']}: {failure['detail']}")
    print(report["run"]["disclaimer"])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 1 if metrics["critical_scenarios_failed"] or metrics["safety_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
