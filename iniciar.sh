#!/bin/sh
# Inicia o painel Rocha Reis LOCALMENTE (127.0.0.1), com dados FICTÍCIOS.
# Sem Docker, sem porta pública, sem Meta/Gemini reais. Ctrl+C encerra com segurança.
#
#   sh iniciar.sh                inicia (cria a demonstração na primeira vez)
#   sh iniciar.sh --porta 8001   outra porta local
#   sh iniciar.sh --verificar    só executa as verificações
#   sh iniciar.sh --nova-demo    apaga SÓ a demonstração e cria outra limpa (ex.: entre grupos de teste)
#   sh iniciar.sh --mensagem "Quero falar com uma pessoa"   cliente fictício escreve (painel aberto)
#
# Python: variável ROCHA_REIS_PYTHON, ou .venv/ do projeto, ou o venv local de desenvolvimento.
set -eu
cd "$(dirname "$0")"
PY="${ROCHA_REIS_PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in .venv/bin/python /tmp/rocha-reis-agent-venv/bin/python; do
        if [ -x "$candidate" ]; then PY="$candidate"; break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "ERRO: Python do projeto não encontrado. Crie um ambiente:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock" >&2
    exit 2
fi
PROFILE="${ROCHA_REIS_PROFILE:-${XDG_DATA_HOME:-$HOME/.local/share}/rocha-reis-agent/demo}"
if [ "${1:-}" = "--mensagem" ]; then
    shift
    exec "$PY" -m rocha_reis_agent.ops mensagem --perfil "$PROFILE" "$@"
fi
if [ "${1:-}" = "--nova-demo" ]; then
    shift
    echo "Recriando a demonstração fictícia em $PROFILE"
    "$PY" -m rocha_reis_agent.ops demo --perfil "$PROFILE" --recriar >/dev/null
fi
if [ ! -f "$PROFILE/perfil.json" ]; then
    echo "Primeira execução: criando a demonstração fictícia em $PROFILE"
    "$PY" -m rocha_reis_agent.ops demo --perfil "$PROFILE" >/dev/null
fi
exec "$PY" -m rocha_reis_agent.ops iniciar --perfil "$PROFILE" "$@"
