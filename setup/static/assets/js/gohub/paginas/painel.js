// Dashboard pessoal (templates/users/painel.html): dados de /api/dashboard/pessoal/.

import { desenharGraficos, iniciarDashboard, ligarFiltroPeriodo, obter } from "../core/dashboard.js";

function ligarJanelas(raiz, painel) {
    const botoes = raiz.querySelectorAll("[data-gohub-janela]");
    const descricao = raiz.querySelector("[data-gohub-janela-descricao]");
    const container = raiz.querySelector('[data-gohub-grafico^="graficos.por_periodo."]');
    if (!container) {
        return;
    }
    botoes.forEach((botao) => {
        botao.addEventListener("click", () => {
            const janela = botao.dataset.gohubJanela;
            botoes.forEach((outro) => {
                const ativo = outro === botao;
                outro.classList.toggle("active", ativo);
                outro.setAttribute("aria-pressed", String(ativo));
            });
            container.dataset.gohubGrafico = `graficos.por_periodo.${janela}`;
            atualizarDescricao(painel.dados());
            const dados = painel.dados();
            if (dados) {
                desenharGraficos(container.parentElement, dados);
            }
        });
    });

    function atualizarDescricao(dados) {
        const texto = obter(dados, `${container.dataset.gohubGrafico}.descricao`);
        if (descricao && typeof texto === "string") {
            descricao.textContent = texto;
        }
    }

    return atualizarDescricao;
}

const formatoDataHora = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

function renderizarIps(raiz, dados) {
    const lista = raiz.querySelector("[data-gohub-ips]");
    const ips = dados?.ips_recentes;
    if (!lista || !Array.isArray(ips)) {
        return;
    }
    const itens = ips
        .filter((item) => typeof item?.ip === "string" && Number.isFinite(item?.acessos))
        .map((item) => {
            const li = document.createElement("li");
            const ip = document.createElement("strong");
            ip.textContent = item.ip;
            const ultimo = new Date(item.ultimo_em);
            const quando = Number.isNaN(ultimo.getTime()) ? "" : `, último em ${formatoDataHora.format(ultimo)}`;
            li.append(ip, ` · ${item.acessos} acesso(s)${quando}`);
            return li;
        });
    if (!itens.length) {
        const vazio = document.createElement("li");
        vazio.className = "text-soft";
        vazio.textContent = lista.dataset.gohubIpsVazio || "Nenhum registro.";
        itens.push(vazio);
    }
    lista.replaceChildren(...itens);
}

function iniciar() {
    const raiz = document.querySelector("[data-gohub-dashboard]");
    if (!raiz) {
        return;
    }
    ligarFiltroPeriodo();
    let atualizarDescricao = null;
    const painel = iniciarDashboard(raiz, {
        aoCarregar: (dados) => {
            atualizarDescricao?.(dados);
            renderizarIps(raiz, dados);
        },
    });
    atualizarDescricao = ligarJanelas(raiz, painel);
}

iniciar();
