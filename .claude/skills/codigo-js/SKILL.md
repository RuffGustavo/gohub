---
name: codigo-js
description: Cria, revisa ou refatora JavaScript do GoHub (ES modules sem build, sobre jQuery/Bootstrap/NioApp do tema DashLite) com segurança contra XSS, CSRF no fetch, acessibilidade e performance. Use ao escrever ou revisar JS de telas.
---
# Código JavaScript

Padrões: `.claude/rules/js.md`. Estrutura: `setup/static/assets/js/gohub/{core,paginas}/`.

## 1. Entender
- Qual comportamento a tela precisa e se ele exige JS (formulário Django com POST normal resolve muitos casos sem JS).
- Quais endpoints `/api/` são usados e que permissões exigem — a autorização é do backend; o JS nunca decide acesso.
- Reutilize: verifique `core/` (ex.: `core/api.js`) e componentes do tema (Bootstrap, select2, DataTables, SweetAlert2) antes de escrever algo novo.
- Código existente: preserve funcionalidade e contratos públicos; ajuste pontual, sem reescrever.

## 2. Implementar
1. Ponto de entrada `paginas/<pagina>.js`, carregado com `<script type="module">` no `{% block js_extra %}` da página.
2. Acesso a dados pelo cliente de `core/api.js` (CSRF, timeout, `response.ok`, erro tipado). Se ainda não existir, crie-o conforme `js.md` e reutilize nas próximas telas.
3. Lógica pura separada da manipulação do DOM; DOM via `data-gohub-*`, `textContent`, `<template>`.
4. Eventos com `addEventListener`/delegação; limpeza com `AbortController`.
5. Estados de carregamento/erro acessíveis (`aria-busy`, `role="status"`, foco).

## 3. Validar
Não há ESLint nem testes JS no projeto; não instale sem aprovação.
- [ ] `node --check <arquivo>.js` sem erro (Node existe localmente via nvm, não na imagem Docker; se indisponível, declare).
- [ ] Nenhum `var`, `==`, global própria, `eval`/`new Function`, handler inline.
- [ ] Nenhum dado externo em `innerHTML`/`.html()`/`NioApp.Toast`/SweetAlert2 `html`.
- [ ] Requisições mutáveis enviam `X-CSRFToken`; `response.ok` verificado; timeout e cancelamento de buscas repetidas.
- [ ] Nenhum segredo, token ou dado pessoal em código, `localStorage` ou `console`.
- [ ] Listeners/observers removidos quando o elemento some; sem registro duplicado.
- [ ] Teclado, foco e `aria-*` atualizados; mensagens anunciadas.
- [ ] Só o JS desta página carregado; nada novo em `base.html`.
- [ ] O backend cobre o que o JS valida: testes de API em `.claude/rules/tests.md` (401/403/400) existem para os endpoints usados.
- [ ] Se viável, teste no navegador (`python manage.py runserver`): fluxo feliz, erro de validação, erro de rede/timeout e sem permissão; console sem erros. Se não for viável, declare.

Em seguida, aplique a skill `validar-entrega`.

## 4. Relatório
1. Arquivos criados/alterados (o código fica no arquivo e no diff; cole-o na resposta só quando não houver arquivo ou se pedido).
2. Principais melhorias e decisões.
3. Boas práticas aplicadas.
4. Problemas encontrados e correções.
5. Segurança.
6. Performance.
7. Arquitetura e organização.
8. Testes realizados e recomendados (o que não foi verificável).
