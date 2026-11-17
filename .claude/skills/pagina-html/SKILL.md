---
name: pagina-html
description: Cria, converte (a partir de templates/exemplos do tema DashLite) ou revisa templates HTML do GoHub aplicando semântica HTML5, acessibilidade WCAG 2.2 AA, SEO, performance e segurança. Use ao criar uma tela, portar uma página do tema ou analisar/refatorar HTML.
---
# Página HTML

Padrões: `.claude/rules/html.md` e `.claude/rules/templates.md`.

## 1. Entender
- Objetivo da página e público (autenticado ou público — define o nível de SEO); dados exibidos e quem pode vê-los (a view controla o acesso, não o template).
- Telas do painel estendem `templates/layouts/painel.html`; reutilize `templates/partials/`; não duplique layout. Novo layout (ex.: telas de login com `pg-auth`) estende `base.html` e fornece seu próprio `<main id="conteudo">` — confirme antes de criar.
- Conversão do tema: localize a página em `templates/exemplos/` e copie só o necessário.
- Código existente: preserve funcionalidade e layout; não invente requisitos.

## 2. Implementar
1. Caminhos do tema → `{% static %}`; links → `{% url %}`; remova `<base href>`, metadados e textos de demonstração.
2. Documento, headings e landmarks conforme `html.md`.
3. Formulários com Django Forms, labels, tipos, `autocomplete`, erros acessíveis e `{% csrf_token %}`.
4. Imagens com `alt` correto, `width`/`height` e `loading="lazy"` fora da dobra.
5. CSS/JS específicos via `{% block %}`, na ordem do tema.
6. Dados de usuário só por autoescape ou `json_script`.

## 3. Validar
Não há validador HTML nem ferramenta de acessibilidade no projeto: a validação é revisão manual. Não envie código a validadores online nem instale ferramentas sem aprovação.
- [ ] Doctype, `lang="pt-BR"`, charset, viewport; title e description únicos.
- [ ] Um `<h1>`, hierarquia sem saltos, landmarks com um único `<main>`.
- [ ] IDs únicos (inclusive loops/includes); nenhum elemento/atributo obsoleto.
- [ ] Teclado: tudo alcançável e operável (Tab, Enter, Espaço, Esc), foco visível, skip link.
- [ ] `alt`, `aria-hidden`, `aria-label` corretos; contraste verificado nas cores alteradas.
- [ ] Formulários: labels, fieldsets, `autocomplete`, erros ligados aos campos.
- [ ] Performance: `width`/`height`, lazy, nenhum JS/CSS de outra página, nenhum script bloqueante novo no `<head>`.
- [ ] Segurança: sem `|safe` com dado de usuário, sem `on*=`, `target="_blank"` com `rel`, só `https://`, sem chave de API.
- [ ] Todo `{% static %}` aponta para arquivo existente; `python manage.py check` sem erros.
- [ ] Se viável, renderize com `python manage.py runserver` e confira desktop e mobile; se não for viável, declare no relatório.

Em seguida, aplique a skill `validar-entrega`.

## 4. Relatório
1. Arquivos criados/alterados (o código fica no arquivo e no diff; cole o HTML completo na resposta só quando não houver arquivo ou se pedido).
2. Boas práticas aplicadas.
3. Problemas encontrados e respectivas correções.
4. Melhorias de acessibilidade.
5. Melhorias de SEO e performance.
6. Recomendações e limitações (ex.: contraste não medido com ferramenta, sem teste com leitor de tela).
