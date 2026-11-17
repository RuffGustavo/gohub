// Gráficos de EXEMPLO do dashboard (templates/users/painel.html).
// Usa o Chart.js do bundle do tema (global Chart); dados via json_script "dados-exemplo-painel".

const ID_DADOS = "dados-exemplo-painel";

function lerDados() {
    const elemento = document.getElementById(ID_DADOS);
    if (!elemento) {
        return null;
    }
    try {
        return JSON.parse(elemento.textContent);
    } catch {
        return null;
    }
}

function listaDeNumeros(valor) {
    return Array.isArray(valor) && valor.every((item) => Number.isFinite(item));
}

function serieValida(serie) {
    return Boolean(serie) && Array.isArray(serie.rotulos) && listaDeNumeros(serie.valores);
}

function lerCores() {
    const estilo = getComputedStyle(document.documentElement);
    const cor = (nome, padrao) => estilo.getPropertyValue(nome).trim() || padrao;
    return {
        primaria: cor("--bs-primary", "#6576ff"),
        info: cor("--bs-info", "#09c2de"),
        aviso: cor("--bs-warning", "#f4bd0e"),
        texto: cor("--bs-gray-600", "#8094ae"),
        grade: cor("--bs-gray-200", "#e5e9f2"),
    };
}

function opcoesBase(cores, movimentoReduzido) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: movimentoReduzido ? false : { duration: 300 },
        plugins: { legend: { display: false } },
        scales: {
            x: { grid: { display: false }, ticks: { color: cores.texto } },
            y: { beginAtZero: true, grid: { color: cores.grade }, ticks: { color: cores.texto, precision: 0 } },
        },
    };
}

function desenharAcessos(canvas, dados, cores, movimentoReduzido) {
    if (!serieValida(dados)) {
        return;
    }
    const opcoes = opcoesBase(cores, movimentoReduzido);
    opcoes.scales.x.display = false;
    opcoes.scales.y.display = false;
    new Chart(canvas, {
        type: "bar",
        data: {
            labels: dados.rotulos,
            datasets: [{ label: "Acessos", data: dados.valores, backgroundColor: cores.primaria, borderRadius: 2 }],
        },
        options: opcoes,
    });
}

function desenharEvolucao(canvas, dados, cores, movimentoReduzido) {
    const series = dados?.series;
    if (!Array.isArray(dados?.rotulos) || !Array.isArray(series) || !series.every((s) => listaDeNumeros(s.valores))) {
        return;
    }
    const paleta = [cores.primaria, cores.info];
    const opcoes = opcoesBase(cores, movimentoReduzido);
    opcoes.plugins.legend = { display: true, position: "bottom", labels: { color: cores.texto, boxWidth: 12 } };
    new Chart(canvas, {
        type: "line",
        data: {
            labels: dados.rotulos,
            datasets: series.map((serie, indice) => ({
                label: String(serie.nome),
                data: serie.valores,
                borderColor: paleta[indice % paleta.length],
                backgroundColor: paleta[indice % paleta.length],
                tension: 0.3,
                pointRadius: 3,
            })),
        },
        options: opcoes,
    });
}

function desenharDistribuicao(canvas, dados, cores, movimentoReduzido, raiz) {
    if (!serieValida(dados)) {
        return;
    }
    const paleta = [cores.primaria, cores.info, cores.aviso];
    new Chart(canvas, {
        type: "doughnut",
        data: {
            labels: dados.rotulos,
            datasets: [{ data: dados.valores, backgroundColor: paleta, borderWidth: 2 }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: movimentoReduzido ? false : { duration: 300 },
            cutout: "70%",
            plugins: { legend: { display: false } },
        },
    });
    // A legenda é HTML renderizado pelo Django (texto acessível); aqui só pinta as cores.
    raiz.querySelectorAll("[data-gohub-legenda]").forEach((marcador) => {
        const cor = paleta[Number(marcador.dataset.gohubLegenda)];
        if (cor) {
            marcador.style.backgroundColor = cor;
        }
    });
}

function iniciar() {
    const raiz = document.getElementById("conteudo");
    const dados = lerDados();
    if (!raiz || !dados || typeof Chart === "undefined") {
        return;
    }
    const cores = lerCores();
    const movimentoReduzido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const graficos = {
        acessos: (canvas) => desenharAcessos(canvas, dados.acessos, cores, movimentoReduzido),
        evolucao: (canvas) => desenharEvolucao(canvas, dados.evolucao, cores, movimentoReduzido),
        distribuicao: (canvas) => desenharDistribuicao(canvas, dados.distribuicao, cores, movimentoReduzido, raiz),
    };
    raiz.querySelectorAll("[data-gohub-grafico]").forEach((canvas) => {
        graficos[canvas.dataset.gohubGrafico]?.(canvas);
    });
}

iniciar();
