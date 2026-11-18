# Gatilhos e funções do app de integrações (.knowledge/leads_ddl.sql, seções 4.1, 4.6, 4.9, 4.10).
#
# - FKs com os nomes e a ação do DDL (ON DELETE RESTRICT), inclusive evento_id dos históricos.
# - updated_at de lead_integracao pela função genérica fn_set_updated_at (users.0002).
# - Nada se vincula a lead excluído/anonimizado (B13): fn_exigir_lead_ativo (leads.0003) em
#   INSERT de evento_integracao e em INSERT/UPDATE de lead_integracao. No UPDATE, a checagem só é
#   pulada para a limpeza de ultimo_erro feita por fn_anonimizar_lead (gohub.anonimizando ligado e
#   nenhuma outra coluna alterada), que roda depois de marcar o lead como excluído: mantém a ordem
#   de locks lead -> lead_integracao das demais escritas.
# - evento_integracao é append-only (B9): tg_evento_imutavel recusa UPDATE/DELETE, exceto a
#   anonimização (payload -> NULL e mensagem_erro -> '[removido]', nada mais).
# - fn_anonimizar_lead passa a limpar também payload/mensagem_erro dos eventos e ultimo_erro das
#   integrações (I20). id_externo é preservado (necessário para avisar o n8n, decisão P13).
#   O reverse_sql restaura exatamente o corpo de leads.0003.
from django.db import migrations

GATILHOS_SQL = r"""
-- FKs com os nomes e a ação do DDL
ALTER TABLE evento_integracao
    DROP CONSTRAINT evento_integracao_lead_id_cd42e0ba_fk_lead_id,
    DROP CONSTRAINT evento_integracao_sistema_id_8575cac6_fk_sistema_id,
    ADD CONSTRAINT fk_evento_lead    FOREIGN KEY (lead_id)    REFERENCES lead (id)    ON DELETE RESTRICT,
    ADD CONSTRAINT fk_evento_sistema FOREIGN KEY (sistema_id) REFERENCES sistema (id) ON DELETE RESTRICT;

ALTER TABLE lead_integracao
    DROP CONSTRAINT lead_integracao_lead_id_04bc720d_fk_lead_id,
    DROP CONSTRAINT lead_integracao_sistema_id_ac1a66b8_fk_sistema_id,
    ADD CONSTRAINT fk_lead_integracao_lead    FOREIGN KEY (lead_id)    REFERENCES lead (id)    ON DELETE RESTRICT,
    ADD CONSTRAINT fk_lead_integracao_sistema FOREIGN KEY (sistema_id) REFERENCES sistema (id) ON DELETE RESTRICT;

ALTER TABLE lead_classificacao
    DROP CONSTRAINT lead_classificacao_evento_id_80319f4d_fk_evento_integracao_id,
    ADD CONSTRAINT fk_classificacao_evento FOREIGN KEY (evento_id) REFERENCES evento_integracao (id) ON DELETE RESTRICT;

ALTER TABLE lead_status_historico
    DROP CONSTRAINT lead_status_historic_evento_id_bab10dd8_fk_evento_in,
    ADD CONSTRAINT fk_status_hist_evento FOREIGN KEY (evento_id) REFERENCES evento_integracao (id) ON DELETE RESTRICT;

-- 4.1 updated_at automático
CREATE TRIGGER tg_lead_integracao_updated_at
    BEFORE UPDATE ON lead_integracao
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- 4.6 Nada se vincula a lead excluído (B13)
CREATE TRIGGER tg_evento_exigir_lead_ativo
    BEFORE INSERT ON evento_integracao
    FOR EACH ROW EXECUTE FUNCTION fn_exigir_lead_ativo();

CREATE TRIGGER tg_lead_integracao_exigir_lead_ativo
    BEFORE INSERT ON lead_integracao
    FOR EACH ROW EXECUTE FUNCTION fn_exigir_lead_ativo();

-- UPDATE: única exceção é a limpeza feita por fn_anonimizar_lead (gohub.anonimizando ligado e
-- a linha só troca ultimo_erro para NULL); qualquer outra alteração continua checada.
CREATE TRIGGER tg_lead_integracao_exigir_lead_ativo_atualizacao
    BEFORE UPDATE ON lead_integracao
    FOR EACH ROW
    WHEN (NOT (
        coalesce(current_setting('gohub.anonimizando', true), '') = 'on'
        AND NEW.ultimo_erro IS NULL
        AND (to_jsonb(NEW) - 'ultimo_erro' - 'updated_at') = (to_jsonb(OLD) - 'ultimo_erro' - 'updated_at')
    ))
    EXECUTE FUNCTION fn_exigir_lead_ativo();

-- 4.9 Eventos são imutáveis (B9). Única exceção: durante fn_anonimizar_lead, de um lead já
-- anonimizado, payload vai para NULL e mensagem_erro (se havia) para '[removido]'.
CREATE FUNCTION fn_evento_imutavel() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND coalesce(current_setting('gohub.anonimizando', true), '') = 'on'
       AND NEW.payload IS NULL
       AND NEW.mensagem_erro IS NOT DISTINCT FROM
           (CASE WHEN OLD.mensagem_erro IS NULL THEN NULL ELSE '[removido]' END)
       AND (to_jsonb(NEW) - 'payload' - 'mensagem_erro') = (to_jsonb(OLD) - 'payload' - 'mensagem_erro')
       AND EXISTS (SELECT 1 FROM lead WHERE id = OLD.lead_id AND anonimizado_em IS NOT NULL) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A tabela % é somente inserção (append-only)', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER tg_evento_imutavel
    BEFORE UPDATE OR DELETE ON evento_integracao
    FOR EACH ROW EXECUTE FUNCTION fn_evento_imutavel();

-- 4.10 Anonimização (B15, I20): corpo de leads.0003 + eventos e integrações do lead.
CREATE OR REPLACE FUNCTION fn_anonimizar_lead(p_lead_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM set_config('gohub.anonimizando', 'on', true);
    UPDATE lead
       SET nome           = 'ANONIMIZADO',
           email          = 'anon-' || id::text || '@anonimizado.invalid',
           telefone       = '+0',
           empresa        = 'ANONIMIZADO',
           cargo          = 'ANONIMIZADO',
           deleted_at     = coalesce(deleted_at, now()),
           anonimizado_em = now()
     WHERE id = p_lead_id
       AND anonimizado_em IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lead % inexistente ou já anonimizado', p_lead_id;
    END IF;
    UPDATE lead_classificacao    SET justificativa = NULL WHERE lead_id = p_lead_id AND justificativa IS NOT NULL;
    UPDATE lead_status_historico SET observacao    = NULL WHERE lead_id = p_lead_id AND observacao    IS NOT NULL;
    UPDATE evento_integracao
       SET payload       = NULL,
           mensagem_erro = CASE WHEN mensagem_erro IS NULL THEN NULL ELSE '[removido]' END
     WHERE lead_id = p_lead_id
       AND (payload IS NOT NULL OR mensagem_erro IS NOT NULL);
    UPDATE lead_integracao SET ultimo_erro = NULL WHERE lead_id = p_lead_id AND ultimo_erro IS NOT NULL;
    PERFORM set_config('gohub.anonimizando', 'off', true);
END;
$$;
"""

REVERTER_SQL = r"""
-- Corpo original de leads.0003 (sem eventos e integrações).
CREATE OR REPLACE FUNCTION fn_anonimizar_lead(p_lead_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM set_config('gohub.anonimizando', 'on', true);
    UPDATE lead
       SET nome           = 'ANONIMIZADO',
           email          = 'anon-' || id::text || '@anonimizado.invalid',
           telefone       = '+0',
           empresa        = 'ANONIMIZADO',
           cargo          = 'ANONIMIZADO',
           deleted_at     = coalesce(deleted_at, now()),
           anonimizado_em = now()
     WHERE id = p_lead_id
       AND anonimizado_em IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lead % inexistente ou já anonimizado', p_lead_id;
    END IF;
    UPDATE lead_classificacao    SET justificativa = NULL WHERE lead_id = p_lead_id AND justificativa IS NOT NULL;
    UPDATE lead_status_historico SET observacao    = NULL WHERE lead_id = p_lead_id AND observacao    IS NOT NULL;
    PERFORM set_config('gohub.anonimizando', 'off', true);
END;
$$;

DROP TRIGGER IF EXISTS tg_evento_imutavel ON evento_integracao;
DROP FUNCTION IF EXISTS fn_evento_imutavel();
DROP TRIGGER IF EXISTS tg_lead_integracao_exigir_lead_ativo_atualizacao ON lead_integracao;
DROP TRIGGER IF EXISTS tg_lead_integracao_exigir_lead_ativo ON lead_integracao;
DROP TRIGGER IF EXISTS tg_evento_exigir_lead_ativo ON evento_integracao;
DROP TRIGGER IF EXISTS tg_lead_integracao_updated_at ON lead_integracao;
-- fn_set_updated_at (users.0002) e fn_exigir_lead_ativo (leads.0003) não são removidas aqui.

ALTER TABLE lead_status_historico
    DROP CONSTRAINT fk_status_hist_evento,
    ADD CONSTRAINT lead_status_historic_evento_id_bab10dd8_fk_evento_in
        FOREIGN KEY (evento_id) REFERENCES evento_integracao (id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE lead_classificacao
    DROP CONSTRAINT fk_classificacao_evento,
    ADD CONSTRAINT lead_classificacao_evento_id_80319f4d_fk_evento_integracao_id
        FOREIGN KEY (evento_id) REFERENCES evento_integracao (id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE lead_integracao
    DROP CONSTRAINT fk_lead_integracao_lead,
    DROP CONSTRAINT fk_lead_integracao_sistema,
    ADD CONSTRAINT lead_integracao_lead_id_04bc720d_fk_lead_id
        FOREIGN KEY (lead_id) REFERENCES lead (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT lead_integracao_sistema_id_ac1a66b8_fk_sistema_id
        FOREIGN KEY (sistema_id) REFERENCES sistema (id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE evento_integracao
    DROP CONSTRAINT fk_evento_lead,
    DROP CONSTRAINT fk_evento_sistema,
    ADD CONSTRAINT evento_integracao_lead_id_cd42e0ba_fk_lead_id
        FOREIGN KEY (lead_id) REFERENCES lead (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT evento_integracao_sistema_id_8575cac6_fk_sistema_id
        FOREIGN KEY (sistema_id) REFERENCES sistema (id) DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("integracoes", "0001_integracao"),
        ("leads", "0003_gatilhos_lead"),
        # FKs evento_id dos históricos (criadas em leads.0004) também ganham o nome do DDL.
        ("leads", "0004_evento_integracao"),
    ]

    operations = [
        migrations.RunSQL(sql=GATILHOS_SQL, reverse_sql=REVERTER_SQL),
    ]
