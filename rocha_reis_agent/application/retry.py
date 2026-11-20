"""Retentativa limitada de snapshots locais; não repetir efeitos externos."""

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from rocha_reis_agent.domain.contracts import ConcurrentUpdate

P = ParamSpec("P")
T = TypeVar("T")


def retry_conflicts(operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(3):
            try:
                return operation(*args, **kwargs)
            except ConcurrentUpdate:
                if attempt == 2:
                    raise ConcurrentUpdate(
                        "Conflito concorrente; nenhuma alteração desta tentativa foi salva. Reenvie o mesmo evento."
                    ) from None
        raise AssertionError("unreachable")
    return run
