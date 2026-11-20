-- Fase 12: classificação explícita dos dados guardada NO PRÓPRIO banco.
-- Todos os bancos existentes e novos são 'synthetic'. A mudança para dados reais NÃO é feita por
-- migração: exige trava de código (REAL_DATA_ACTIVATION_AUTHORIZED), autorização escrita e os
-- requisitos de docs/SEGURANCA_ARMAZENAMENTO.md. Idempotente e sem alterar dados existentes.
INSERT OR IGNORE INTO metadata VALUES ('data_classification', 'synthetic')
