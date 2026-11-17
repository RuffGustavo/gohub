---
paths:
  - "setup/static/**/*.js"
  - "**/static/**/*.js"
---
# JavaScript — padrões do projeto

## Contexto real
- Sem npm, bundler, transpilação, ESLint, Prettier ou testes JS. O JS é servido como está pelo WhiteNoise. Não adicione `package.json`, ferramentas ou bibliotecas sem aprovação.
- O tema carrega globalmente via `bundle.js` + `scripts.js` (fim do `<body>` em `templates/base.html`): jQuery, Bootstrap 5 (`bootstrap`), `NioApp`, select2, DataTables 2, SimpleBar, SweetAlert2, toastr. Não os recarregue nem adicione outra versão.
- Arquivos do tema (`bundle.js`, `scripts.js`, `editors.js`, `apps/`, `charts/`, `libs/`, `example-*.js`) não são editados. `example-*.js` e `charts/*.js` são demonstração: não carregue em páginas reais.
- Alvo: navegadores evergreen. ES2022+ nativo (módulos, `?.`, `??`, `AbortController`, `AbortSignal.timeout()`, observers) sem polyfill.

## Organização
- JS próprio em `setup/static/assets/js/gohub/`: `core/` para módulos compartilhados (sem lógica de página) e `paginas/<pagina>.js` como ponto de entrada de cada tela.
- Carregue como módulo no bloco da página: `{% block js_extra %}<script type="module" src="{% static 'assets/js/gohub/paginas/eventos.js' %}"></script>{% endblock %}`. Módulos rodam depois do tema (são diferidos), então `bootstrap`/`NioApp` já existem.
- Imports relativos com extensão (`import { requisitar } from "../core/api.js";`). O manifest do Django não reescreve imports de módulo: o arquivo importado é servido pelo nome original (cache curto), o que é esperado.
- Sem globais próprias (`window.x = …`); estado encapsulado no módulo. Separe acesso a dados (`core/api.js`), regras puras (funções sem DOM) e manipulação do DOM.
- `const` por padrão, `let` se reatribuir, nunca `var`; `===`/`!==`; early return; funções curtas com nome de ação (`carregarEventos`, `renderizarLinha`); nomes e mensagens em português; 4 espaços, aspas duplas, ponto e vírgula.
- Código novo em JS puro. jQuery só para integrar plugins do tema que o exigem (select2, DataTables); não misture com manipulação manual do mesmo elemento.
- Componentes do Bootstrap pela API do tema (`bootstrap.Modal.getOrCreateInstance(el)`), não reimplemente modal/dropdown/toast.

## DOM e eventos
- Selecione uma vez e reutilize; escopo no container da página (`raiz.querySelector(...)`), via `data-*` (`data-gohub-…`) em vez de classes de estilo ou IDs do tema.
- Texto com `textContent`; elementos com `createElement`/`<template>` renderizado pelo Django + `cloneNode`; inserção em lote com `DocumentFragment` ou `replaceChildren()`.
- `addEventListener`; delegação para listas dinâmicas; nunca `on*=` no HTML. `preventDefault()`/`stopPropagation()` só com motivo.
- Listeners/observers de elementos temporários: remova com `AbortController` (`{ signal }`) ou `disconnect()`.
- Acessibilidade: gerencie foco após abrir/fechar conteúdo, atualize `aria-expanded`/`aria-busy`/`aria-invalid` ao mudar estado, anuncie resultados em região `role="status"`, e mantenha tudo operável por teclado.

## Requisições à API
- Use um único cliente em `core/api.js` (crie na primeira necessidade e reutilize): `fetch` same-origin com `credentials: "same-origin"`, `Accept: application/json`, `X-CSRFToken` (cookie `csrftoken`) em POST/PUT/PATCH/DELETE — a `SessionAuthentication` do DRF exige CSRF —, timeout com `AbortSignal.timeout()`, checagem de `response.ok` e erro tipado com `status` e corpo.
- Erros do DRF: `{ "detail": "…" }` ou `{ "campo": ["…"] }`; mostre mensagem por campo (ligada via `aria-describedby`) ou geral. 401/403 → mensagem de acesso, nunca "tente novamente" em loop.
- Listas paginadas: `{ count, next, previous, results }` (PageNumberPagination, 20 itens); siga `next`, não monte URLs de página manualmente.
- Cancele a requisição anterior ao disparar outra do mesmo tipo (busca/filtro) e aplique debounce (~300ms) em entrada de texto; `Promise.all` para chamadas independentes.
- Valide a forma dos dados recebidos antes de usar; estado de erro e de carregamento previsíveis (botão desabilitado + `aria-busy`).

## Segurança
- Nunca `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`, `$(...).html()` ou `$(htmlString)` com dado da API, da URL ou do usuário. Nada de `eval`, `new Function`, `setTimeout("string")`.
- `NioApp.Toast(msg)` e o toastr do tema montam HTML sem escape (`scripts.js:146`, `escapeHtml: false`): passe só texto fixo do código. Em SweetAlert2 use `text`, nunca `html` com dado externo. Em DataTables, `render` de dado externo com `DataTable.render.text()`.
- Dados do servidor para o JS via `{{ dados|json_script:"id" }}` + `JSON.parse(el.textContent)`; nunca interpolando em `<script>`.
- Sem segredo, token ou chave de API no JS; sem `localStorage`/`sessionStorage` para credenciais ou dados pessoais (a autenticação é por cookie de sessão).
- Validação no cliente é só UX: o backend valida e autoriza tudo. Esconder botão não é controle de acesso.
- Escreva código compatível com uma futura CSP restritiva: nenhum script/handler inline, nenhum `style` gerado com string.
- `console.*` sem dados pessoais ou respostas completas; remova logs de depuração antes de concluir.

## Performance
- Carregue só o JS da página; nunca adicione script ao `base.html` sem necessidade global.
- Evite layout thrashing (leia medidas antes de escrever estilos); `IntersectionObserver` para lazy/infinite scroll; trabalho pesado fora da thread principal só se medido.
- Não otimize sem medir; prefira a solução simples.
