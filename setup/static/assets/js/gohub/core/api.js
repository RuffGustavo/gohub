// Cliente único da API do GoHub (fetch same-origin com sessão, CSRF e timeout).

const TIMEOUT_PADRAO_MS = 15000;
const METODOS_SEGUROS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ErroApi extends Error {
    constructor(status, corpo) {
        super(`Falha na requisição (HTTP ${status})`);
        this.name = "ErroApi";
        this.status = status;
        this.corpo = corpo;
    }
}

function lerCookie(nome) {
    const item = document.cookie.split("; ").find((parte) => parte.startsWith(`${nome}=`));
    return item ? decodeURIComponent(item.slice(nome.length + 1)) : "";
}

export async function requisitar(url, { metodo = "GET", corpo, signal, timeoutMs = TIMEOUT_PADRAO_MS } = {}) {
    const destino = new URL(url, window.location.origin);
    if (destino.origin !== window.location.origin) {
        throw new Error("Requisição bloqueada: só a própria origem é permitida.");
    }
    const cabecalhos = { Accept: "application/json" };
    if (!METODOS_SEGUROS.has(metodo)) {
        cabecalhos["X-CSRFToken"] = lerCookie("csrftoken");
        cabecalhos["Content-Type"] = "application/json";
    }
    const sinais = [AbortSignal.timeout(timeoutMs)];
    if (signal) {
        sinais.push(signal);
    }
    const resposta = await fetch(destino, {
        method: metodo,
        credentials: "same-origin",
        headers: cabecalhos,
        body: corpo === undefined ? undefined : JSON.stringify(corpo),
        signal: AbortSignal.any(sinais),
    });
    const tipo = resposta.headers.get("Content-Type") || "";
    const dados = tipo.includes("application/json") ? await resposta.json() : null;
    if (!resposta.ok) {
        throw new ErroApi(resposta.status, dados);
    }
    return dados;
}
