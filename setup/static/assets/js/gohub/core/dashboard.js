// Base dos dashboards (pessoal e gerencial): busca a API indicada em data-gohub-dashboard,
// preenche os indicadores (data-gohub-valor / data-gohub-variacao) e desenha os gráficos.

import { ErroApi, requisitar } from "./api.js";
import { desenharGrafico } from "./graficos.js";

const formatoNumero = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
const formatoHora = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });
const RECARREGAR_APOS_MS = 60000;

export function obter(dados, caminho) {
    return caminho.split(".").reduce((atual, chave) => (atual == null ? undefined : atual[chave]), dados);
}

function preencherValores(raiz, dados) {
    raiz.querySelectorAll("[data-gohub-valor]").forEach((elemento) => {
        const valor = obter(dados, elemento.dataset.gohubValor);
        const unidade = elemento.dataset.gohubUnidade || "";
        elemento.textContent = Number.isFinite(valor) ? `${formatoNumero.format(valor)}${unidade}` : "—";
    });
}

function textoVariacao(valor, tipo) {
    const unidade = tipo === "pontos" ? " p.p." : "%";
    if (valor > 0) {
        return { icone: "ni-arrow-long-up", cor: "text-success", texto: `Alta de ${formatoNumero.format(valor)}${unidade}` };
    }
    if (valor < 0) {
        return { icone: "ni-arrow-long-down", cor: "text-danger", texto: `Queda de ${formatoNumero.format(Math.abs(valor))}${unidade}` };
    }
    return { icone: "ni-minus", cor: "text-soft", texto: "Estável" };
}

function preencherVariacoes(raiz, dados) {
    raiz.querySelectorAll("[data-gohub-variacao]").forEach((elemento) => {
        const valor = obter(dados, elemento.dataset.gohubVariacao);
        const texto = document.createElement("span");
        if (!Number.isFinite(valor)) {
            texto.className = "text-soft";
            texto.textContent = "Sem base de comparação (nenhum registro no período anterior)";
            elemento.replaceChildren(texto);
            return;
        }
        const variacao = textoVariacao(valor, elemento.dataset.gohubVariacaoTipo);
        const icone = document.createElement("em");
        icone.className = `icon ni ${variacao.icone} ${variacao.cor}`;
        icone.setAttribute("aria-hidden", "true");
        texto.textContent = ` ${variacao.texto} em relação ao período anterior`;
        elemento.replaceChildren(icone, texto);
    });
}

function mensagemDeErro(erro) {
    if (erro instanceof ErroApi && (erro.status === 401 || erro.status === 403)) {
        return "Você não tem acesso a estes indicadores. Entre novamente ou fale com o administrador.";
    }
    if (erro instanceof ErroApi && erro.status === 400) {
        return "O filtro de período é inválido. Ajuste as datas e aplique de novo.";
    }
    return "Não foi possível carregar os indicadores. Use o botão Atualizar para tentar de novo.";
}

export function desenharGraficos(raiz, dados) {
    raiz.querySelectorAll("[data-gohub-grafico]").forEach((container) => {
        desenharGrafico(container, obter(dados, container.dataset.gohubGrafico), raiz);
    });
}

// Escolher uma data marca o período "Personalizado" (o servidor ignora as datas nos demais).
export function ligarFiltroPeriodo(documento = document) {
    documento.querySelectorAll("[data-gohub-filtro-periodo]").forEach((form) => {
        const seletor = form.querySelector('select[name="periodo"]');
        form.querySelectorAll('input[name="inicio"], input[name="fim"]').forEach((campo) => {
            campo.addEventListener("change", () => {
                if (seletor && campo.value) {
                    seletor.value = "personalizado";
                }
            });
        });
    });
}

/**
 * Liga um dashboard: carrega ao abrir, no botão Atualizar e ao voltar para a aba (após 1 min).
 * `aoCarregar(dados)` permite à página desenhar extras. Retorna { dados: () => últimos dados }.
 */
export function iniciarDashboard(raiz, { aoCarregar } = {}) {
    const url = raiz.dataset.gohubDashboard || "";
    const estado = raiz.querySelector("[data-gohub-estado]");
    const botao = raiz.querySelector("[data-gohub-atualizar]");
    let controlador = null;
    let ultimos = null;
    let carregadoEm = 0;

    async function carregar() {
        if (!url.startsWith("/api/")) {
            return;
        }
        controlador?.abort();
        controlador = new AbortController();
        raiz.setAttribute("aria-busy", "true");
        if (botao) {
            botao.disabled = true;
        }
        if (estado) {
            estado.textContent = "Carregando indicadores…";
        }
        try {
            const dados = await requisitar(url, { signal: controlador.signal });
            if (!dados || typeof dados !== "object") {
                throw new Error("Resposta inesperada.");
            }
            ultimos = dados;
            carregadoEm = Date.now();
            preencherValores(raiz, dados);
            preencherVariacoes(raiz, dados);
            desenharGraficos(raiz, dados);
            aoCarregar?.(dados);
            if (estado) {
                estado.textContent = `Atualizado às ${formatoHora.format(new Date())}`;
            }
        } catch (erro) {
            if (erro?.name === "AbortError") {
                return;
            }
            if (estado) {
                estado.textContent = mensagemDeErro(erro);
            }
        } finally {
            raiz.removeAttribute("aria-busy");
            if (botao) {
                botao.disabled = false;
            }
        }
    }

    botao?.addEventListener("click", carregar);
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && Date.now() - carregadoEm > RECARREGAR_APOS_MS) {
            carregar();
        }
    });
    carregar();
    return { dados: () => ultimos };
}
