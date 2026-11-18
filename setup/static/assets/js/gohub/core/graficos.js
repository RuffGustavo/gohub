// Gráficos dos dashboards sobre o Chart.js do bundle do tema (global Chart).
// Só desenham dados recebidos da API; sem registros, mostram a mensagem de vazio do template.

const formatoNumero = new Intl.NumberFormat("pt-BR");

function lerCores() {
    const estilo = getComputedStyle(document.documentElement);
    const cor = (nome, padrao) => estilo.getPropertyValue(nome).trim() || padrao;
    return {
        paleta: [
            cor("--bs-primary", "#6576ff"),
            cor("--bs-info", "#09c2de"),
            cor("--bs-warning", "#f4bd0e"),
            cor("--bs-success", "#1ee0ac"),
            cor("--bs-danger", "#e85347"),
            cor("--bs-gray-500", "#8094ae"),
        ],
        texto: cor("--bs-gray-600", "#8094ae"),
        grade: cor("--bs-gray-200", "#e5e9f2"),
    };
}

const movimentoReduzido = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function numeros(valor) {
    return Array.isArray(valor) && valor.every((item) => Number.isFinite(item));
}

function textos(valor) {
    return Array.isArray(valor) ? valor.map((item) => String(item)) : null;
}

function soma(lista) {
    return lista.reduce((total, item) => total + item, 0);
}

function opcoesBase(cores, { legenda = false, eixos = true, horizontal = false } = {}) {
    const opcoes = {
        responsive: true,
        maintainAspectRatio: false,
        animation: movimentoReduzido() ? false : { duration: 300 },
        plugins: {
            legend: legenda
                ? { display: true, position: "bottom", labels: { color: cores.texto, boxWidth: 12 } }
                : { display: false },
        },
    };
    if (eixos) {
        const eixoValor = { beginAtZero: true, grid: { color: cores.grade }, ticks: { color: cores.texto, precision: 0 } };
        const eixoRotulo = { grid: { display: false }, ticks: { color: cores.texto } };
        opcoes.scales = horizontal ? { x: eixoValor, y: eixoRotulo } : { x: eixoRotulo, y: eixoValor };
        if (horizontal) {
            opcoes.indexAxis = "y";
        }
    }
    return opcoes;
}

// Cada montador recebe os dados da API e devolve a configuração do Chart, ou null se a forma
// dos dados não for a esperada; `total` decide se há o que desenhar.
const montadores = {
    linha(dados, cores) {
        const rotulos = textos(dados?.rotulos);
        const series = dados?.series;
        if (!rotulos || !Array.isArray(series) || !series.every((serie) => numeros(serie?.valores))) {
            return null;
        }
        return {
            total: soma(series.map((serie) => soma(serie.valores))),
            config: {
                type: "line",
                data: {
                    labels: rotulos,
                    datasets: series.map((serie, indice) => ({
                        label: String(serie.nome),
                        data: serie.valores,
                        borderColor: cores.paleta[indice % cores.paleta.length],
                        backgroundColor: cores.paleta[indice % cores.paleta.length],
                        tension: 0.3,
                        pointRadius: rotulos.length > 40 ? 0 : 3,
                    })),
                },
                options: opcoesBase(cores, { legenda: series.length > 1 }),
            },
        };
    },
    barras(dados, cores, horizontal = false) {
        const rotulos = textos(dados?.rotulos);
        if (!rotulos || !numeros(dados?.valores)) {
            return null;
        }
        return {
            total: soma(dados.valores),
            config: {
                type: "bar",
                data: {
                    labels: rotulos,
                    datasets: [{ label: "Quantidade", data: dados.valores, backgroundColor: cores.paleta[0], borderRadius: 3 }],
                },
                options: opcoesBase(cores, { horizontal }),
            },
        };
    },
    "barras-horizontais"(dados, cores) {
        return montadores.barras(dados, cores, true);
    },
    rosca(dados, cores) {
        const rotulos = textos(dados?.rotulos);
        if (!rotulos || !numeros(dados?.valores)) {
            return null;
        }
        return {
            total: soma(dados.valores),
            legenda: rotulos.map((rotulo, indice) => ({ rotulo, valor: dados.valores[indice] })),
            config: {
                type: "doughnut",
                data: {
                    labels: rotulos,
                    datasets: [{ data: dados.valores, backgroundColor: cores.paleta, borderWidth: 2 }],
                },
                options: { ...opcoesBase(cores, { eixos: false }), cutout: "68%" },
            },
        };
    },
    desempenho(dados, cores) {
        const rotulos = textos(dados?.rotulos);
        if (!rotulos || !numeros(dados?.recebidos) || !numeros(dados?.convertidos) || !numeros(dados?.taxa)) {
            return null;
        }
        const opcoes = opcoesBase(cores, { legenda: true });
        opcoes.scales.y1 = {
            position: "right",
            beginAtZero: true,
            max: 100,
            grid: { display: false },
            ticks: { color: cores.texto, callback: (valor) => `${valor}%` },
        };
        return {
            total: soma(dados.recebidos),
            config: {
                type: "bar",
                data: {
                    labels: rotulos,
                    datasets: [
                        { label: "Recebidos", data: dados.recebidos, backgroundColor: cores.paleta[0], borderRadius: 3 },
                        { label: "Enviados ao CRM", data: dados.convertidos, backgroundColor: cores.paleta[3], borderRadius: 3 },
                        {
                            label: "Taxa de conversão (%)", data: dados.taxa, type: "line", yAxisID: "y1",
                            borderColor: cores.paleta[2], backgroundColor: cores.paleta[2], tension: 0.3,
                        },
                    ],
                },
                options: opcoes,
            },
        };
    },
};

function renderizarLegenda(lista, itens, cores) {
    if (!lista) {
        return;
    }
    const total = soma(itens.map((item) => item.valor));
    const fragmento = document.createDocumentFragment();
    itens.forEach(({ rotulo, valor }, indice) => {
        const li = document.createElement("li");
        const marcador = document.createElement("span");
        marcador.className = "gohub-legenda__cor";
        marcador.setAttribute("aria-hidden", "true");
        marcador.style.backgroundColor = cores.paleta[indice % cores.paleta.length];
        const percentual = total ? ` (${formatoNumero.format(Math.round((valor * 1000) / total) / 10)}%)` : "";
        li.append(marcador, `${rotulo}: ${formatoNumero.format(valor)}${percentual}`);
        fragmento.append(li);
    });
    lista.replaceChildren(fragmento);
}

export function desenharGrafico(container, dados, raiz) {
    const canvas = container.querySelector("canvas");
    const vazio = container.querySelector("[data-gohub-vazio]");
    const lista = raiz.querySelector(`[data-gohub-legenda-de="${CSS.escape(container.dataset.gohubGrafico)}"]`);
    if (!canvas || typeof Chart === "undefined") {
        return;
    }
    Chart.getChart(canvas)?.destroy();
    const cores = lerCores();
    const montado = montadores[container.dataset.gohubTipo]?.(dados, cores);
    const temDados = Boolean(montado) && montado.total > 0;
    canvas.hidden = !temDados;
    if (vazio) {
        vazio.hidden = temDados;
    }
    if (lista) {
        lista.replaceChildren();
    }
    if (!temDados) {
        return;
    }
    new Chart(canvas, montado.config);
    if (montado.legenda) {
        renderizarLegenda(lista, montado.legenda, cores);
    }
}
