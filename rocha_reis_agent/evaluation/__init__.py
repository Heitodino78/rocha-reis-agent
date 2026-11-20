"""Avaliação reproduzível da camada conversacional (Fase 8).

Executa cenários sintéticos contra o núcleo REAL (AgentService + SQLite + estados de
atendimento), trocando apenas o provedor de IA. Um oráculo independente do validador
do backend confere cada resposta contra o catálogo aprovado.

Resultados com FakeAIProvider ou com modelos simulados medem o BACKEND (regras,
validação, roteamento). Não validam a qualidade de um modelo generativo real.
"""
