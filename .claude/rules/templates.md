---
paths:
  - "templates/**/*.html"
  - "**/templates/**/*.html"
  - "setup/static/assets/js/**/*.js"
---
# Templates e frontend (DashLite)

Padrões de HTML5, acessibilidade, SEO e performance: `html.md`. Procedimento para criar/converter/revisar telas: skill `pagina-html`.

- `templates/exemplos/` é referência do tema: HTML estático com `<base href="../">` e caminhos `./assets/...`, sem tags Django, não servido. Não edite nem sirva; copie o trecho necessário para um template real fora de `exemplos/`.
- Conversão: `{% load static %}` + `{% static 'assets/css/dashlite.css' %}` (arquivos de `setup/static/`); layout comum via `{% extends %}`/`{% block %}`, sem duplicar; links com `{% url %}`.
- `{% block %}` dentro de `{% include %}` não pode ser sobrescrito pela página: por isso `layouts/painel.html` envolve cada partial num bloco (`barra_apps`, `cabecalho`, `menu_lateral`, `rodape`). Itens de menu globais são editados em `partials/painel/_menu_lateral.html`/`_barra_apps.html` (exemplo comentado lá).
- Ajustes visuais/acessibilidade sobre o tema vão em `setup/static/assets/css/gohub.css`; onde o tema usa `<a href="#">` para ação, use `<button type="button" class="… gohub-btn-reset">`.
- Todo arquivo citado em `{% static %}` deve existir (manifest do WhiteNoise → 500 com DEBUG=False).
- XSS: autoescape sempre ativo; nada de `|safe`, `mark_safe` ou `{% autoescape off %}` com dado de usuário/Markdown; dados para JS via `{{ dados|json_script:"id" }}`.
- Todo `<form method="post">` com `{% csrf_token %}`; requisições JS POST/PUT/PATCH/DELETE enviam `X-CSRFToken`.
- Esconder botão/menu não é autorização: a view/endpoint valida permissão.
- Autenticação web é por sessão (cookie): não guarde tokens/credenciais em `localStorage`, `sessionStorage` ou JS.
- Não inclua chaves de API de terceiros em template/JS; não reutilize a chave presente em `exemplos/components/misc/map.html`.
- `setup/static/assets/js/bundle.js`, `scripts.js`, `libs/` e `charts/` são do tema: não edite; JS próprio em `setup/static/assets/js/gohub/` (ver `js.md`).
- Recursos de CDN externa só com aprovação (o tema é servido localmente).
