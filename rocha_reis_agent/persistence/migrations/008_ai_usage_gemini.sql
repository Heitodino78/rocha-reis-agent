-- Fase 9B: consumo do provedor Gemini. Tokens de raciocínio (thoughts) são cobrados como
-- saída e passam a ser registrados à parte; o modelo pedido fica ao lado do modelo que
-- respondeu (model_version). Somente metadados: nenhum texto, prompt ou resposta.
ALTER TABLE ai_usage ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0 CHECK(reasoning_tokens >= 0);
ALTER TABLE ai_usage ADD COLUMN requested_model TEXT CHECK(requested_model IS NULL OR length(requested_model) BETWEEN 1 AND 80)
