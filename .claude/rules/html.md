---
paths:
  - "templates/**/*.html"
  - "**/templates/**/*.html"
---
# HTML5 — padrões do projeto

Complementa `templates.md` (tags Django, tema, XSS/CSRF). Os HTMLs de `templates/exemplos/` NÃO seguem estes padrões (`lang="zxx"`, sem landmarks nem `<h1>`, `<img>` sem `width`/`height`, `target="_blank"` sem `rel`): ao converter, corrija — não copie os defeitos. Preserve funcionalidade e layout de código existente; mudança visual só se pedida.

## Documento
- `templates/base.html` já define doctype, `lang="pt-BR"`, charset, viewport, CSS/JS do tema, `gohub.css` e o skip link; `layouts/painel.html` fornece header, navs, `<main id="conteudo">`, mensagens e footer. Não repita isso nas páginas: estenda `layouts/painel.html` e preencha os blocos `title`, `description`, `content` (e `css_extra`/`js_extra`/`meta_extra` se preciso).
- `{% block title %}` único e descritivo por página (`Página | GoHub`); `{% block description %}` com meta description real. Ao converter do tema, remova `<base href>`, `meta author` e textos de demonstração.
- Um `<h1>` por página; hierarquia sem pular níveis; nível pelo significado, tamanho pela classe do tema.

## Semântica
- Landmarks: `<header>`, `<nav aria-label="…">`, um `<main id="conteudo">`, `<aside>`, `<footer>`. Troque o `div` equivalente mantendo as classes `nk-*` (o CSS do tema seleciona por classe) e confira o layout.
- `<section>` só com heading; `<article>` para conteúdo autônomo; listas em `<ul>/<ol>`; dados tabulares em `<table>` com `<caption>` e `<th scope>`.
- Ação → `<button type="button|submit">`; navegação → `<a href>`. Nunca `<a href="#">` ou `div` clicável para ação.

## Acessibilidade (WCAG 2.2 AA)
- Link "Pular para o conteúdo" como primeiro foco do base, apontando para `#conteudo`.
- Imagem informativa: `alt` descritivo; decorativa: `alt=""`. Ícones `<em class="icon ni …">` decorativos com `aria-hidden="true"`; botão/link só com ícone exige `aria-label`.
- Foco visível: não remova `outline` sem substituto; ordem de tabulação = ordem visual; nada de `tabindex` > 0.
- ARIA só sem equivalente nativo. Dropdowns/modais/abas do tema (Bootstrap) já gerenciam estado: preserve `data-bs-*` e `aria-*` existentes.
- Contraste ≥ 4.5:1 (texto) e ≥ 3:1 (texto grande, bordas de controles, foco) ao alterar cores. Informação nunca só por cor (status com texto).
- Mensagens do framework `messages` em região `role="status"` (ou `role="alert"` para erros críticos).

## Formulários
- Prefira Django Forms: validação do servidor é a fonte da verdade; atributos HTML só ajudam o usuário e devem ser coerentes com o form/serializer.
- Todo campo com `<label for>` (ou `{{ field.label_tag }}`); placeholder não é label; radios/checkboxes agrupados em `<fieldset><legend>`.
- `type` correto (`email`, `tel`, `number`, `date`, `url`, `password`), `autocomplete` adequado (`name`, `email`, `current-password`, `new-password`…), `required`/`min`/`max`/`maxlength`/`pattern` quando houver a regra.
- Erros: mensagem em português dizendo como corrigir, ligada ao campo por `aria-describedby` + `aria-invalid="true"`; em forms longos, resumo de erros no topo.

## Performance
- `<img>` com `width` e `height`; `loading="lazy"` fora da primeira dobra; `fetchpriority="high"` só no elemento LCP, se houver.
- Scripts no fim do `<body>` como no tema, na ordem `bundle.js` → `scripts.js` → scripts da página. Se usar `defer`, use em todos (mantém a ordem); nunca `async` em script que depende do bundle.
- CSS/JS específicos (editors, charts, libs) só nas páginas que os usam, via `{% block %}`; nunca no base. Não acrescente `?ver=`: o manifest do WhiteNoise já versiona.

## SEO
- Áreas autenticadas: basta title e description corretos. Open Graph e Schema.org somente em páginas públicas indexáveis, quando existirem.
- URLs amigáveis vêm das rotas Django: português, kebab-case, `slug` quando fizer sentido.

## Segurança e qualidade
- `target="_blank"` sempre com `rel="noopener noreferrer"`; URLs externas sempre `https://`.
- Sem handlers `on*=` e sem `<script>` inline com lógica (JS em arquivo de `setup/static/`, dados via `json_script`); `style=""` só para valor dinâmico pontual.
- Nada obsoleto: `<center>`, `<font>`, `align`, `bgcolor`, `border` em tabela, `frameborder`. `<iframe>` com `title` e `loading="lazy"`.
- IDs únicos por página, inclusive em loops/includes (`id="item-{{ obj.pk }}"`).
- Indentação de 4 espaços; classes próprias seguem `.claude/rules/css.md` (prefixo `gohub-`, BEM); prefira utilitários do tema a classes novas.
