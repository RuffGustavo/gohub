from django.db import migrations

# Funções e gatilhos de .knowledge/leads_ddl.sql (seções 4.1 e 4.3 a 4.10), adaptados:
# - P2: sem evento_integracao (gatilho tg_evento_* e FKs evento_id ficam para o app "integracoes");
# - P3: colunas de domínio são varchar + CHECK, então variáveis usam varchar(20), não o ENUM;
# - fn_set_updated_at é reutilizada (criada em users.0002), não recriada;
# - FKs: o Django as cria DEFERRABLE INITIALLY DEFERRED e sem ação; aqui são recriadas com
#   os nomes e o ON DELETE RESTRICT do DDL (falha imediata, não só no commit);
# - acréscimo ao DDL: tg_lead_proteger_anonimizado impede regravar dados pessoais de um
#   lead já anonimizado (ex.: save() de uma instância lida antes da anonimização).
# - search_path fixo (public, pg_temp) em todas as funções: um schema anterior no caminho não
#   consegue "sombrear" lead/sistema; fn_exigir_lead_ativo bloqueia a linha (FOR NO KEY UPDATE).
# - travas por marcador de transação (set_config(..., true) vale só até o fim da transação e é
#   desligado logo após o uso). Protegem contra erro de aplicação e gatilhos futuros, não contra
#   quem já tem SQL livre no banco (esse controle é o papel do banco sem DDL, Entregável 8):
#     gohub.aplicando_snapshot: só fn_status_hist_aplicar/fn_classificacao_aplicar mudam o snapshot;
#     gohub.anonimizando: só fn_anonimizar_lead muda deleted_at/anonimizado_em e limpa o texto
#     livre dos históricos (justificativa/observacao), única exceção à imutabilidade (B9, B15).
# - consentimento_em, politica_versao e created_at são imutáveis depois do INSERT.
GATILHOS_SQL = r"""
-- FKs com os nomes e a ação do DDL
ALTER TABLE lead_status_historico
    DROP CONSTRAINT lead_status_historico_lead_id_816cdc8a_fk_lead_id,
    DROP CONSTRAINT lead_status_historico_usuario_id_ac78b259_fk_usuario_id,
    DROP CONSTRAINT lead_status_historico_sistema_id_c2e2930a_fk_sistema_id,
    ADD CONSTRAINT fk_status_hist_lead    FOREIGN KEY (lead_id)    REFERENCES lead (id)    ON DELETE RESTRICT,
    ADD CONSTRAINT fk_status_hist_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_status_hist_sistema FOREIGN KEY (sistema_id) REFERENCES sistema (id) ON DELETE RESTRICT;

ALTER TABLE lead_classificacao
    DROP CONSTRAINT lead_classificacao_lead_id_8b2be3a8_fk_lead_id,
    DROP CONSTRAINT lead_classificacao_usuario_id_a528cb32_fk_usuario_id,
    DROP CONSTRAINT lead_classificacao_sistema_id_552d26ef_fk_sistema_id,
    ADD CONSTRAINT fk_classificacao_lead    FOREIGN KEY (lead_id)    REFERENCES lead (id)    ON DELETE RESTRICT,
    ADD CONSTRAINT fk_classificacao_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_classificacao_sistema FOREIGN KEY (sistema_id) REFERENCES sistema (id) ON DELETE RESTRICT;

-- 4.1 updated_at automático (função genérica de users.0002)
CREATE TRIGGER tg_sistema_updated_at BEFORE UPDATE ON sistema FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER tg_lead_updated_at    BEFORE UPDATE ON lead    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- 4.3 Normalização do lead e estado inicial forçado (B2, B5)
CREATE FUNCTION fn_lead_normalizar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    IF NEW.anonimizado_em IS NULL THEN
        NEW.email    := lower(btrim(NEW.email));
        NEW.nome     := btrim(regexp_replace(NEW.nome,    '\s+', ' ', 'g'));
        NEW.empresa  := btrim(regexp_replace(NEW.empresa, '\s+', ' ', 'g'));
        NEW.cargo    := btrim(regexp_replace(NEW.cargo,   '\s+', ' ', 'g'));
        NEW.telefone := regexp_replace(NEW.telefone, '[^0-9+]', '', 'g');
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- o estado só nasce de um jeito: "recebido", sem classificação
        NEW.status_atual         := 'recebido';
        NEW.status_atualizado_em := NEW.created_at;
        NEW.classificacao_atual  := NULL;
        NEW.classificado_em      := NULL;
        NEW.deleted_at           := NULL;
        NEW.anonimizado_em       := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_lead_normalizar
    BEFORE INSERT OR UPDATE OF nome, email, telefone, empresa, cargo ON lead
    FOR EACH ROW EXECUTE FUNCTION fn_lead_normalizar();

-- 4.4 O snapshot só muda via tabelas de histórico (B6); colunas de controle protegidas
CREATE FUNCTION fn_lead_proteger_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    IF (NEW.status_atual, NEW.status_atualizado_em, NEW.classificacao_atual, NEW.classificado_em)
       IS DISTINCT FROM
       (OLD.status_atual, OLD.status_atualizado_em, OLD.classificacao_atual, OLD.classificado_em)
       AND NOT (pg_trigger_depth() >= 2
                AND coalesce(current_setting('gohub.aplicando_snapshot', true), '') = 'on') THEN
        RAISE EXCEPTION 'Status e classificação do lead só podem ser alterados via lead_status_historico / lead_classificacao'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.consentimento_em, NEW.politica_versao, NEW.created_at)
       IS DISTINCT FROM (OLD.consentimento_em, OLD.politica_versao, OLD.created_at) THEN
        RAISE EXCEPTION 'Consentimento, versão da política e data de recebimento do lead são imutáveis'
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.deleted_at, NEW.anonimizado_em) IS DISTINCT FROM (OLD.deleted_at, OLD.anonimizado_em)
       AND coalesce(current_setting('gohub.anonimizando', true), '') <> 'on' THEN
        RAISE EXCEPTION 'Exclusão do lead só pela anonimização (fn_anonimizar_lead)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_lead_proteger_snapshot
    BEFORE UPDATE ON lead
    FOR EACH ROW EXECUTE FUNCTION fn_lead_proteger_snapshot();

-- Acréscimo (B15): lead anonimizado não volta a ter dados pessoais nem é "desexcluído"
CREATE FUNCTION fn_lead_proteger_anonimizado() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    IF OLD.anonimizado_em IS NOT NULL
       AND (NEW.nome, NEW.email, NEW.telefone, NEW.empresa, NEW.cargo, NEW.deleted_at, NEW.anonimizado_em)
           IS DISTINCT FROM
           (OLD.nome, OLD.email, OLD.telefone, OLD.empresa, OLD.cargo, OLD.deleted_at, OLD.anonimizado_em) THEN
        RAISE EXCEPTION 'Lead anonimizado não pode ter dados pessoais ou exclusão alterados'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_lead_proteger_anonimizado
    BEFORE UPDATE ON lead
    FOR EACH ROW EXECUTE FUNCTION fn_lead_proteger_anonimizado();

-- 4.5 Todo lead novo ganha o registro inicial no histórico de status (B5)
CREATE FUNCTION fn_lead_status_inicial() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    INSERT INTO lead_status_historico (lead_id, status_novo, sistema_id, observacao, ocorrido_em)
    VALUES (NEW.id, 'recebido',
            (SELECT id FROM sistema WHERE codigo = 'aplicacao_web'),
            'Lead recebido pelo formulário público', NEW.created_at);
    RETURN NULL;
END;
$$;

CREATE TRIGGER tg_lead_status_inicial
    AFTER INSERT ON lead
    FOR EACH ROW EXECUTE FUNCTION fn_lead_status_inicial();

-- 4.6 Registros só podem ser vinculados a leads não excluídos (B13)
CREATE FUNCTION fn_exigir_lead_ativo() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    -- FOR NO KEY UPDATE: uma anonimização concorrente espera este INSERT terminar (ou vice-versa).
    -- Não FOR SHARE: duas inserções seguram o lock compartilhado e ambas precisam promovê-lo
    -- no UPDATE do snapshot (fn_classificacao_aplicar) -> deadlock.
    PERFORM 1 FROM lead WHERE id = NEW.lead_id AND deleted_at IS NULL FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lead % inexistente ou excluído', NEW.lead_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_classificacao_exigir_lead_ativo
    BEFORE INSERT ON lead_classificacao
    FOR EACH ROW EXECUTE FUNCTION fn_exigir_lead_ativo();

-- 4.7 Histórico de status: preenche status_anterior com bloqueio da linha (B8) ...
CREATE FUNCTION fn_status_hist_preparar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    v_atual varchar(20);
BEGIN
    SELECT status_atual INTO v_atual
      FROM lead
     WHERE id = NEW.lead_id AND deleted_at IS NULL
       FOR UPDATE;                       -- serializa atualizações concorrentes do mesmo lead
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lead % inexistente ou excluído', NEW.lead_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF EXISTS (SELECT 1 FROM lead_status_historico WHERE lead_id = NEW.lead_id) THEN
        NEW.status_anterior := v_atual;
    ELSE
        NEW.status_anterior := NULL;     -- primeiro registro
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER tg_status_hist_preparar
    BEFORE INSERT ON lead_status_historico
    FOR EACH ROW EXECUTE FUNCTION fn_status_hist_preparar();

-- ... e aplica o novo status ao snapshot, ignorando eventos fora de ordem (B7)
CREATE FUNCTION fn_status_hist_aplicar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM set_config('gohub.aplicando_snapshot', 'on', true);
    UPDATE lead
       SET status_atual = NEW.status_novo,
           status_atualizado_em = NEW.ocorrido_em
     WHERE id = NEW.lead_id
       AND status_atualizado_em <= NEW.ocorrido_em;
    PERFORM set_config('gohub.aplicando_snapshot', 'off', true);
    RETURN NULL;
END;
$$;

CREATE TRIGGER tg_status_hist_aplicar
    AFTER INSERT ON lead_status_historico
    FOR EACH ROW EXECUTE FUNCTION fn_status_hist_aplicar();

-- 4.8 Histórico de classificação -> snapshot
CREATE FUNCTION fn_classificacao_aplicar() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM set_config('gohub.aplicando_snapshot', 'on', true);
    UPDATE lead
       SET classificacao_atual = NEW.classificacao,
           classificado_em     = NEW.classificado_em
     WHERE id = NEW.lead_id
       AND (classificado_em IS NULL OR classificado_em <= NEW.classificado_em);
    PERFORM set_config('gohub.aplicando_snapshot', 'off', true);
    RETURN NULL;
END;
$$;

CREATE TRIGGER tg_classificacao_aplicar
    AFTER INSERT ON lead_classificacao
    FOR EACH ROW EXECUTE FUNCTION fn_classificacao_aplicar();

-- 4.9 Históricos são imutáveis (B9). Única exceção: durante fn_anonimizar_lead, de um lead já
-- anonimizado, a coluna de texto livre (argumento do gatilho) pode ir para NULL e nada mais.
CREATE FUNCTION fn_bloquear_alteracao() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
    v_coluna text := TG_ARGV[0];
BEGIN
    IF TG_OP = 'UPDATE' AND v_coluna IS NOT NULL
       AND coalesce(current_setting('gohub.anonimizando', true), '') = 'on'
       AND to_jsonb(NEW) ->> v_coluna IS NULL
       AND (to_jsonb(NEW) - v_coluna) = (to_jsonb(OLD) - v_coluna)
       AND EXISTS (SELECT 1 FROM lead WHERE id = OLD.lead_id AND anonimizado_em IS NOT NULL) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A tabela % é somente inserção (append-only)', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER tg_status_hist_imutavel   BEFORE UPDATE OR DELETE ON lead_status_historico FOR EACH ROW EXECUTE FUNCTION fn_bloquear_alteracao('observacao');
CREATE TRIGGER tg_classificacao_imutavel BEFORE UPDATE OR DELETE ON lead_classificacao    FOR EACH ROW EXECUTE FUNCTION fn_bloquear_alteracao('justificativa');

-- 4.10 Anonimização (B15): dados pessoais do lead e texto livre dos históricos (que pode conter
-- dados pessoais digitados por pessoas ou sistemas); status, classificações e datas permanecem.
CREATE FUNCTION fn_anonimizar_lead(p_lead_id uuid) RETURNS void
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
"""

REVERTER_SQL = r"""
DROP FUNCTION IF EXISTS fn_anonimizar_lead(uuid);
DROP TRIGGER IF EXISTS tg_classificacao_imutavel ON lead_classificacao;
DROP TRIGGER IF EXISTS tg_status_hist_imutavel ON lead_status_historico;
DROP FUNCTION IF EXISTS fn_bloquear_alteracao();
DROP TRIGGER IF EXISTS tg_classificacao_aplicar ON lead_classificacao;
DROP FUNCTION IF EXISTS fn_classificacao_aplicar();
DROP TRIGGER IF EXISTS tg_status_hist_aplicar ON lead_status_historico;
DROP FUNCTION IF EXISTS fn_status_hist_aplicar();
DROP TRIGGER IF EXISTS tg_status_hist_preparar ON lead_status_historico;
DROP FUNCTION IF EXISTS fn_status_hist_preparar();
DROP TRIGGER IF EXISTS tg_classificacao_exigir_lead_ativo ON lead_classificacao;
DROP FUNCTION IF EXISTS fn_exigir_lead_ativo();
DROP TRIGGER IF EXISTS tg_lead_status_inicial ON lead;
DROP FUNCTION IF EXISTS fn_lead_status_inicial();
DROP TRIGGER IF EXISTS tg_lead_proteger_anonimizado ON lead;
DROP FUNCTION IF EXISTS fn_lead_proteger_anonimizado();
DROP TRIGGER IF EXISTS tg_lead_proteger_snapshot ON lead;
DROP FUNCTION IF EXISTS fn_lead_proteger_snapshot();
DROP TRIGGER IF EXISTS tg_lead_normalizar ON lead;
DROP FUNCTION IF EXISTS fn_lead_normalizar();
DROP TRIGGER IF EXISTS tg_lead_updated_at ON lead;
DROP TRIGGER IF EXISTS tg_sistema_updated_at ON sistema;
-- fn_set_updated_at pertence a users.0002: não é removida aqui.

ALTER TABLE lead_classificacao
    DROP CONSTRAINT fk_classificacao_lead,
    DROP CONSTRAINT fk_classificacao_usuario,
    DROP CONSTRAINT fk_classificacao_sistema,
    ADD CONSTRAINT lead_classificacao_lead_id_8b2be3a8_fk_lead_id
        FOREIGN KEY (lead_id) REFERENCES lead (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT lead_classificacao_usuario_id_a528cb32_fk_usuario_id
        FOREIGN KEY (usuario_id) REFERENCES usuario (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT lead_classificacao_sistema_id_552d26ef_fk_sistema_id
        FOREIGN KEY (sistema_id) REFERENCES sistema (id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE lead_status_historico
    DROP CONSTRAINT fk_status_hist_lead,
    DROP CONSTRAINT fk_status_hist_usuario,
    DROP CONSTRAINT fk_status_hist_sistema,
    ADD CONSTRAINT lead_status_historico_lead_id_816cdc8a_fk_lead_id
        FOREIGN KEY (lead_id) REFERENCES lead (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT lead_status_historico_usuario_id_ac78b259_fk_usuario_id
        FOREIGN KEY (usuario_id) REFERENCES usuario (id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT lead_status_historico_sistema_id_c2e2930a_fk_sistema_id
        FOREIGN KEY (sistema_id) REFERENCES sistema (id) DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_gatilhos_usuario"),
        ("leads", "0002_sistemas_iniciais"),
    ]

    operations = [
        migrations.RunSQL(sql=GATILHOS_SQL, reverse_sql=REVERTER_SQL),
    ]
