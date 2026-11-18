"""API interna (n8n -> aplicação): autenticação, assinatura, rede, limites, contrato e cenários do MER."""

import datetime
import json
import time
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from app.leads.models import LeadClassificacao, LeadStatus, LeadStatusHistorico, Sistema

from ..assinatura import assinar, mensagem_entrada
from ..models import EventoIntegracao, LeadIntegracao, StatusIntegracao
from .base import (
    CONFIG_TESTE,
    SEGREDO,
    SEGREDO_ANTERIOR,
    SEGREDOS,
    TOKEN,
    TOKEN_ANTERIOR,
    ComCacheLimpo,
    anonimizar,
    conceder,
    criar_usuario,
    integracoes,
    novo_lead,
)

MENSAGEM_401 = "Credenciais inválidas."


class ClienteInternoMixin(ComCacheLimpo):
    def setUp(self):
        super().setUp()
        self.lead = novo_lead()

    def url(self, lead=None, eventos=False):
        nome = "interno-lead-eventos" if eventos else "interno-lead"
        return reverse(nome, kwargs={"pk": (lead or self.lead).pk})

    def cabecalhos(self, corpo=b"", token=TOKEN, segredo=SEGREDO, timestamp=None, metodo="GET", caminho=None,
                   **extra):
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        mensagem = mensagem_entrada(metodo, caminho or self.url(), corpo)
        cab = {"HTTP_X_GOHUB_TIMESTAMP": timestamp, "HTTP_X_GOHUB_ASSINATURA": assinar(mensagem, timestamp, segredo)}
        if token is not None:
            cab["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        cab.update(extra)
        return cab

    def get_interno(self, lead=None, **kwargs):
        kwargs.setdefault("caminho", self.url(lead))
        return self.client.get(self.url(lead), **self.cabecalhos(b"", **kwargs))

    def post_evento(self, dados, lead=None, corpo=None, **kwargs):
        corpo = json.dumps(dados, default=str).encode() if corpo is None else corpo
        kwargs.setdefault("metodo", "POST")
        kwargs.setdefault("caminho", self.url(lead, eventos=True))
        return self.client.generic(
            "POST", self.url(lead, eventos=True), corpo, content_type="application/json",
            **self.cabecalhos(corpo, **kwargs),
        )

    def evento(self, **extra):
        dados = {
            "chave_idempotencia": "exec-100:no-crm",
            "tipo_evento": "crm.sincronizacao",
            "ocorrido_em": timezone.now().isoformat(),
            "resultado": "sucesso",
        }
        dados.update(extra)
        return dados


@override_settings(**CONFIG_TESTE)
class LeadInternoGetTests(ClienteInternoMixin, APITestCase):
    def test_campos_exatos_e_no_store(self):
        resposta = self.get_interno()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            set(resposta.json()),
            {"id", "nome", "email", "telefone", "empresa", "cargo", "recebido_em", "status_atual",
             "classificacao_atual"},
        )
        self.assertEqual(resposta.json()["email"], "ana@empresa.com")
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_lead_inexistente_anonimizado_ou_uuid_invalido(self):
        outro = novo_lead(email="outro@empresa.com")
        anonimizar(outro)
        self.assertEqual(self.get_interno(lead=outro).status_code, 404)
        self.lead.pk = "00000000-0000-4000-8000-000000000000"
        self.assertEqual(self.get_interno().status_code, 404)
        resposta = self.client.get("/api/interno/leads/nao-e-uuid/", **self.cabecalhos())
        self.assertEqual(resposta.status_code, 404)

    def test_consultas_constantes(self):
        self.get_interno()  # aquece o cache de ContentType/sistemas
        with self.assertNumQueries(2):  # sistema autenticado + lead
            self.assertEqual(self.get_interno().status_code, 200)


@override_settings(**CONFIG_TESTE)
class AutenticacaoTests(ClienteInternoMixin, APITestCase):
    def assert401(self, resposta):
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.json(), {"detail": MENSAGEM_401})
        self.assertEqual(resposta["Cache-Control"], "no-store")

    def test_recusas_com_mensagem_identica(self):
        sem_auth = self.cabecalhos(token=None)
        self.assert401(self.client.get(self.url(), **sem_auth))
        self.assert401(self.client.get(self.url(), **{**sem_auth, "HTTP_AUTHORIZATION": f"Basic {TOKEN}"}))
        self.assert401(self.client.get(self.url(), **{**sem_auth, "HTTP_AUTHORIZATION": "Bearer"}))
        self.assert401(self.client.get(self.url(), **{**sem_auth, "HTTP_AUTHORIZATION": "Bearer  "}))
        self.assert401(self.get_interno(token=TOKEN + "x"))
        self.assert401(self.get_interno(token="Ã©" * 20))
        self.assert401(self.get_interno(token="a" * 600))

    def test_sistema_inativo(self):
        Sistema.objects.filter(codigo=Sistema.N8N).update(ativo=False)
        self.assert401(self.get_interno())

    def test_header_www_authenticate(self):
        self.assertEqual(self.get_interno(token=None)["WWW-Authenticate"], "Bearer")

    def test_sessao_e_basic_nao_valem(self):
        usuario = criar_usuario(is_superuser=True)
        self.client.force_login(usuario)
        self.assert401(self.client.get(self.url(), **self.cabecalhos(token=None)))
        self.client.logout()
        from app.leads.tests.base import basic

        resposta = self.client.get(self.url(), **self.cabecalhos(token=None, HTTP_AUTHORIZATION=basic("operador")))
        self.assert401(resposta)

    @override_settings(INTEGRACOES=integracoes(INTEGRACOES_N8N_TOKEN_ANTERIOR=TOKEN_ANTERIOR))
    def test_token_anterior_durante_rotacao(self):
        with self.assertLogs("gohub.integracoes", "INFO") as logs:
            self.assertEqual(self.get_interno(token=TOKEN_ANTERIOR).status_code, 200)
        self.assertIn("evento=integracao_token_anterior sistema=n8n", logs.output[0])
        self.assertEqual(self.get_interno(token=TOKEN).status_code, 200)

    def test_token_anterior_nao_configurado(self):
        self.assert401(self.get_interno(token=TOKEN_ANTERIOR))

    def test_auditoria_sem_token(self):
        with self.assertLogs("gohub.auditoria", "INFO") as logs:
            self.get_interno(token=TOKEN + "x")
        texto = "\n".join(logs.output)
        self.assertIn("evento=integracao_autenticacao_falhou", texto)
        self.assertIn("motivo=token", texto)
        for segredo in SEGREDOS:
            self.assertNotIn(segredo, texto)


@override_settings(**CONFIG_TESTE)
class AssinaturaTests(ClienteInternoMixin, APITestCase):
    def assert401(self, resposta):
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.json(), {"detail": MENSAGEM_401})

    def test_assinatura_ausente_ou_malformada(self):
        cab = self.cabecalhos()
        del cab["HTTP_X_GOHUB_ASSINATURA"]
        self.assert401(self.client.get(self.url(), **cab))
        cab = self.cabecalhos()
        del cab["HTTP_X_GOHUB_TIMESTAMP"]
        self.assert401(self.client.get(self.url(), **cab))
        self.assert401(self.client.get(self.url(), **self.cabecalhos(HTTP_X_GOHUB_ASSINATURA="sha256=zz")))

    def test_assinatura_de_outro_corpo(self):
        corpo = json.dumps(self.evento()).encode()
        cab = self.cabecalhos(b'{"outro":1}', metodo="POST", caminho=self.url(eventos=True))
        resposta = self.client.generic("POST", self.url(eventos=True), corpo, content_type="application/json", **cab)
        self.assert401(resposta)
        self.assertEqual(EventoIntegracao.objects.count(), 0)

    def test_fora_da_janela_e_replay_tardio(self):
        self.assert401(self.get_interno(timestamp=int(time.time()) - 301))
        self.assert401(self.get_interno(timestamp=int(time.time()) + 301))
        self.assertEqual(self.get_interno(timestamp=int(time.time()) - 290).status_code, 200)

    def test_assinatura_presa_ao_metodo_e_ao_caminho(self):
        outro = novo_lead(email="outro@empresa.com")
        # Assinatura válida de um GET do lead A reaproveitada no lead B.
        cab = self.cabecalhos(caminho=self.url())
        self.assert401(self.client.get(self.url(outro), **cab))
        # Assinatura de POST (corpo vazio) usada num GET do mesmo caminho base.
        self.assert401(self.get_interno(metodo="POST"))
        # Query string também faz parte do que é assinado.
        self.assert401(self.client.get(self.url() + "?x=1", **self.cabecalhos(caminho=self.url())))
        self.assertEqual(
            self.client.get(self.url() + "?x=1", **self.cabecalhos(caminho=self.url() + "?x=1")).status_code, 200
        )

    def test_outro_segredo(self):
        self.assert401(self.get_interno(segredo=SEGREDO_ANTERIOR))

    @override_settings(INTEGRACOES=integracoes(INTEGRACOES_N8N_SEGREDO_HMAC_ANTERIOR=SEGREDO_ANTERIOR))
    def test_segredo_anterior_na_rotacao(self):
        self.assertEqual(self.get_interno(segredo=SEGREDO_ANTERIOR).status_code, 200)


@override_settings(**CONFIG_TESTE)
class RedeELimitesTests(ClienteInternoMixin, APITestCase):
    @override_settings(INTEGRACOES=integracoes(INTEGRACOES_N8N_REDES_PERMITIDAS="10.0.0.0/8"))
    def test_rede_fora_da_lista(self):
        resposta = self.get_interno()  # REMOTE_ADDR 127.0.0.1
        self.assertEqual(resposta.status_code, 403)
        forjado = self.get_interno(HTTP_X_FORWARDED_FOR="10.0.0.5")
        self.assertEqual(forjado.status_code, 403)
        # Fora da rede, token válido e inválido recebem a mesma resposta (sem oráculo de token).
        invalido = self.get_interno(token=TOKEN + "x")
        self.assertEqual((invalido.status_code, invalido.json()), (resposta.status_code, resposta.json()))
        self.assertEqual(self.get_interno(token=None).status_code, 403)
        self.assertEqual(self.get_interno(REMOTE_ADDR="10.1.2.3").status_code, 200)

    @override_settings(INTEGRACOES=integracoes(INTEGRACOES_AUTH_FALHA_TAXA="3/min"))
    def test_falhas_de_autenticacao_por_ip(self):
        for _ in range(3):
            self.assertEqual(self.get_interno(token=TOKEN + "x").status_code, 401)
        self.assertEqual(self.get_interno(token=TOKEN + "x").status_code, 429)
        self.assertEqual(self.get_interno().status_code, 429)  # o IP fica bloqueado na janela
        self.assertEqual(self.get_interno(REMOTE_ADDR="10.9.9.9").status_code, 200)

    @override_settings(INTEGRACOES=integracoes(INTEGRACOES_INTERNO_TAXA="2/min"))
    def test_taxa_por_sistema(self):
        self.assertEqual(self.get_interno().status_code, 200)
        self.assertEqual(self.get_interno().status_code, 200)
        self.assertEqual(self.get_interno().status_code, 429)

    def test_corpo_grande_demais(self):
        corpo = json.dumps(self.evento(mensagem_erro="x" * (17 * 1024))).encode()
        resposta = self.post_evento(None, corpo=corpo)
        self.assertEqual(resposta.status_code, 413)
        self.assertEqual(EventoIntegracao.objects.count(), 0)


@override_settings(**CONFIG_TESTE)
class EventosTests(ClienteInternoMixin, APITestCase):
    def test_cenario_1_quente_regra_crm_sucesso(self):
        dados = self.evento(
            sistema_destino="crm", id_externo="CRM-0001",
            classificacao={"classificacao": "quente", "metodo": "regra", "versao_criterio": "regras-v3"},
            status_novo="enviado_crm",
        )
        resposta = self.post_evento(dados)
        self.assertEqual(resposta.status_code, 201, resposta.content)
        corpo = resposta.json()
        self.assertNotIn("payload", corpo)
        self.assertNotIn("mensagem_erro", corpo)
        evento = EventoIntegracao.objects.get(pk=corpo["id"])
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status_atual, self.lead.classificacao_atual), ("enviado_crm", "quente"))
        self.assertEqual(LeadClassificacao.objects.get(lead=self.lead).evento, evento)
        self.assertEqual(LeadStatusHistorico.objects.get(lead=self.lead, status_novo="enviado_crm").evento, evento)
        crm = LeadIntegracao.objects.get(lead=self.lead, sistema__codigo="crm")
        self.assertEqual((crm.status, crm.id_externo, crm.tentativas), (StatusIntegracao.SUCESSO, "CRM-0001", 1))

    def test_cenario_2_ia_frio_em_nutricao(self):
        dados = self.evento(
            tipo_evento="classificacao.concluida",
            classificacao={"classificacao": "frio", "metodo": "ia", "confianca": "0.87",
                           "justificativa": "Empresa fora do perfil"},
            status_novo="em_nutricao",
        )
        self.assertEqual(self.post_evento(dados).status_code, 201)
        classificacao = LeadClassificacao.objects.get(lead=self.lead)
        self.assertEqual((classificacao.metodo, classificacao.confianca), ("ia", Decimal("0.8700")))
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status_atual, self.lead.classificacao_atual), ("em_nutricao", "frio"))

    def test_cenario_6_falha_no_crm_e_retry(self):
        dados = self.evento(
            resultado="erro", sistema_destino="crm", mensagem_erro="CRM 503", status_novo="erro",
            observacao="Falha ao enviar ao CRM", payload={"execucao_id": "100", "codigo_http": 503},
        )
        self.assertEqual(self.post_evento(dados).status_code, 201)
        repetido = self.post_evento(dados)
        self.assertEqual(repetido.status_code, 200)
        self.assertEqual(EventoIntegracao.objects.count(), 1)
        crm = LeadIntegracao.objects.get(lead=self.lead, sistema__codigo="crm")
        self.assertEqual((crm.status, crm.tentativas, crm.ultimo_erro), (StatusIntegracao.ERRO, 1, "CRM 503"))
        self.assertEqual(LeadStatusHistorico.objects.filter(lead=self.lead, status_novo="erro").count(), 1)
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.nome, self.lead.status_atual), ("Ana Souza", "erro"))

    def test_cenario_7_evento_atrasado(self):
        recente = timezone.now()
        antigo = self.lead.created_at + datetime.timedelta(microseconds=1)
        self.assertEqual(
            self.post_evento(self.evento(status_novo="enviado_crm", ocorrido_em=recente.isoformat())).status_code, 201
        )
        atrasado = self.evento(chave_idempotencia="exec-099:no", status_novo="em_nutricao",
                               tipo_evento="nutricao.inscricao", ocorrido_em=antigo.isoformat())
        self.assertEqual(self.post_evento(atrasado).status_code, 201)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status_atual, "enviado_crm")
        self.assertTrue(LeadStatusHistorico.objects.filter(lead=self.lead, status_novo="em_nutricao").exists())

    def test_mesma_chave_outro_lead_409(self):
        self.assertEqual(self.post_evento(self.evento()).status_code, 201)
        outro = novo_lead(email="outro@empresa.com")
        resposta = self.post_evento(self.evento(status_novo="enviado_crm"), lead=outro)
        self.assertEqual(resposta.status_code, 409)
        self.assertFalse(LeadStatusHistorico.objects.filter(lead=outro, status_novo="enviado_crm").exists())

    def test_id_externo_em_uso_409(self):
        self.assertEqual(self.post_evento(self.evento(sistema_destino="crm", id_externo="CRM-1")).status_code, 201)
        outro = novo_lead(email="outro@empresa.com")
        with self.assertLogs("gohub.integracoes", "WARNING") as logs:
            resposta = self.post_evento(
                self.evento(chave_idempotencia="exec-200:no", sistema_destino="crm", id_externo="CRM-1",
                            status_novo="enviado_crm"),
                lead=outro,
            )
        self.assertEqual(resposta.status_code, 409)
        self.assertNotIn("CRM-1", "\n".join(logs.output))
        self.assertEqual(EventoIntegracao.objects.filter(lead=outro).count(), 0)
        self.assertFalse(LeadStatusHistorico.objects.filter(lead=outro, status_novo="enviado_crm").exists())

    def test_lead_anonimizado_404_mesmo_com_chave_existente(self):
        self.assertEqual(self.post_evento(self.evento()).status_code, 201)
        anonimizar(self.lead)
        self.assertEqual(self.post_evento(self.evento()).status_code, 404)

    def test_sanitizacao_do_texto_livre(self):
        dados = self.evento(
            resultado="erro", sistema_destino="crm", status_novo="erro",
            mensagem_erro="Falhou para ana@empresa.com\n(11) 98765-4321",
            observacao="Contato: +55 11 98765-4321",
        )
        self.assertEqual(self.post_evento(dados).status_code, 201)
        evento = EventoIntegracao.objects.get()
        self.assertEqual(evento.mensagem_erro, "Falhou para [email] [telefone]")
        crm = LeadIntegracao.objects.get(sistema__codigo="crm")
        self.assertEqual(crm.ultimo_erro, "Falhou para [email] [telefone]")
        historico = LeadStatusHistorico.objects.get(lead=self.lead, status_novo="erro")
        self.assertEqual(historico.observacao, "Contato: [telefone]")

    def test_payload_com_texto_sanitizado(self):
        dados = self.evento(payload={"workflow": "Leads de ana@empresa.com", "no": "CRM\nenvio", "tentativa": 2})
        self.assertEqual(self.post_evento(dados).status_code, 201)
        self.assertEqual(EventoIntegracao.objects.get().payload,
                         {"workflow": "Leads de [email]", "no": "CRM envio", "tentativa": 2})

    def test_entradas_invalidas_400_sem_gravar(self):
        agora = timezone.now()
        casos = {
            "campo desconhecido": {"usuario": "x"},
            "tipo fora do catálogo": {"tipo_evento": "lead.notificacao"},
            "tipo malformado": {"tipo_evento": "CRM SYNC"},
            "chave curta": {"chave_idempotencia": "abc"},
            "chave com espaço": {"chave_idempotencia": "exec 1 no crm"},
            "resultado inválido": {"resultado": "ok"},
            "status recebido": {"status_novo": "recebido"},
            "método manual": {"classificacao": {"classificacao": "frio", "metodo": "manual"}},
            "confiança > 1": {"classificacao": {"classificacao": "frio", "metodo": "ia", "confianca": "1.01"}},
            "confiança sem ia": {"classificacao": {"classificacao": "frio", "metodo": "regra", "confianca": "0.5"}},
            "classificação com campo extra": {"classificacao": {"classificacao": "frio", "metodo": "ia", "x": 1}},
            "justificativa com e-mail": {"classificacao": {"classificacao": "frio", "metodo": "ia",
                                                           "justificativa": "ver ana@empresa.com"}},
            "sem fuso": {"ocorrido_em": agora.replace(tzinfo=None).isoformat()},
            "futuro": {"ocorrido_em": (agora + datetime.timedelta(minutes=6)).isoformat()},
            "antes do lead": {"ocorrido_em": (self.lead.created_at - datetime.timedelta(seconds=1)).isoformat()},
            "data inválida": {"ocorrido_em": "ontem"},
            "destino aplicação web": {"sistema_destino": "aplicacao_web"},
            "destino é o próprio chamador": {"sistema_destino": "n8n"},
            "destino inexistente": {"sistema_destino": "erp"},
            "erro sem mensagem": {"resultado": "erro"},
            "id_externo sem destino": {"id_externo": "CRM-1"},
            "id_externo malformado": {"sistema_destino": "crm", "id_externo": "CRM 1"},
            "payload com chave proibida": {"payload": {"email": "ana@empresa.com"}},
            "payload aninhado": {"payload": {"workflow": {"id": 1}}},
            "payload lista": {"payload": [1, 2]},
            "payload com float": {"payload": {"duracao_ms": 1.5}},
            "payload texto longo": {"payload": {"workflow": "x" * 201}},
            "mensagem longa": {"resultado": "erro", "mensagem_erro": "x" * 1001},
            "observação longa": {"observacao": "x" * 1001},
        }
        for nome, extra in casos.items():
            with self.subTest(nome):
                resposta = self.post_evento(self.evento(**extra))
                self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertEqual(EventoIntegracao.objects.count(), 0)
        self.assertEqual(LeadIntegracao.objects.exclude(sistema__codigo="n8n").count(), 0)

    def test_destino_inativo(self):
        Sistema.objects.filter(codigo="crm").update(ativo=False)
        self.assertEqual(self.post_evento(self.evento(sistema_destino="crm")).status_code, 400)

    def test_limites_exatos_aceitos(self):
        dados = self.evento(
            chave_idempotencia="k" * 100, resultado="erro", mensagem_erro="x" * 1000, observacao="y" * 1000,
            status_novo="erro", payload={"workflow": "w" * 200, "tentativa": 1, "versao": None, "no": "crm"},
            ocorrido_em=(timezone.now() + datetime.timedelta(minutes=4)).isoformat(),
        )
        self.assertEqual(self.post_evento(dados).status_code, 201)

    def test_json_invalido_e_corpo_nao_objeto(self):
        self.assertEqual(self.post_evento(None, corpo=b"{nao json").status_code, 400)
        self.assertEqual(self.post_evento(None, corpo=b"[1,2]").status_code, 400)

    def test_consultas_do_post_constantes(self):
        self.post_evento(self.evento(chave_idempotencia="aquecer-1"))
        with self.assertNumQueries(5):  # sistema, lead, SAVEPOINT, INSERT evento, RELEASE
            self.assertEqual(self.post_evento(self.evento(chave_idempotencia="exec-x01")).status_code, 201)
        with self.assertNumQueries(11):  # + destino, upsert, lock e update da integração, classificação, status
            resposta = self.post_evento(self.evento(
                chave_idempotencia="exec-x02", sistema_destino="crm", status_novo="enviado_crm",
                classificacao={"classificacao": "morno", "metodo": "regra"},
            ))
            self.assertEqual(resposta.status_code, 201)

    def test_logs_sem_credenciais_nem_dados_pessoais(self):
        with self.assertLogs(level="DEBUG") as logs:
            self.post_evento(self.evento(resultado="erro", mensagem_erro="ana@empresa.com", sistema_destino="crm"))
            self.get_interno(token=TOKEN + "x")
            self.post_evento(self.evento(chave_idempotencia="exec-z01", id_externo="X"), segredo=SEGREDO_ANTERIOR)
        texto = "\n".join(logs.output)
        for proibido in (*SEGREDOS, "ana@empresa.com", "+5511987654321", "sha256="):
            self.assertNotIn(proibido, texto)


class PermissoesDoPainelTests(ClienteInternoMixin, APITestCase):
    """O token do n8n não acessa a API do painel; usuário com permissões não acessa a interna."""

    @override_settings(**CONFIG_TESTE)
    def test_token_nao_acessa_painel(self):
        resposta = self.client.get("/api/integracoes/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertIn(resposta.status_code, (401, 403))
        resposta = self.client.get("/painel/integracoes/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(resposta.status_code, 302)

    @override_settings(**CONFIG_TESTE)
    def test_usuario_com_todas_as_permissoes_nao_acessa_interna(self):
        usuario = criar_usuario()
        conceder(usuario, "integracoes.view_leadintegracao", "integracoes.view_eventointegracao",
                 "integracoes.reprocessar_integracao", "integracoes.gerenciar_integracoes", "leads.view_lead")
        self.client.force_authenticate(usuario)
        resposta = self.client.get(self.url(), **self.cabecalhos(token=None))
        self.assertEqual(resposta.status_code, 401)
