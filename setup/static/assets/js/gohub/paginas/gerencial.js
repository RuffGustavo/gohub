// Dashboard gerencial (templates/dashboard/gerencial.html): dados de /api/dashboard/gerencial/.
// A API só devolve as seções permitidas ao usuário; seções ausentes ficam com "—".

import { iniciarDashboard, ligarFiltroPeriodo } from "../core/dashboard.js";

const raiz = document.querySelector("[data-gohub-dashboard]");
if (raiz) {
    ligarFiltroPeriodo();
    iniciarDashboard(raiz);
}
