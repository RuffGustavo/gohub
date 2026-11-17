---
paths:
  - "setup/static/**/*.css"
  - "**/static/**/*.css"
---
# CSS — padrões do projeto

## Arquitetura
- O tema DashLite (`setup/static/assets/css/dashlite.css`, `theme.css`, `libs/`, `editors/`) é Bootstrap 5.3 compilado: **não edite**. Todo CSS próprio fica em `setup/static/assets/css/gohub.css`, carregado por `templates/base.html` depois do tema. Não crie outro arquivo global; CSS de uma única página/lib vai em arquivo próprio carregado via `{% block css_extra %}`.
- Antes de escrever CSS, procure classe/utilitário do tema que resolva (`d-flex`, `gap-*`, `p-*`, `text-*`, `card`, `nk-block`…). Só crie estilo quando o tema não cobrir.
- Preserve a identidade visual (cores, fontes, espaçamentos e layout do tema); mudança visual só com pedido explícito.
- `gohub.css` segue esta ordem de seções: tokens → base → componentes → utilitários → acessibilidade/preferências do usuário.

## Nomenclatura e especificidade
- Classes próprias com prefixo `gohub-` em BEM: `gohub-bloco`, `gohub-bloco__elemento`, `gohub-bloco--modificador`. Não estilize por ID, por tag solta ou por classes `nk-*` encadeadas à estrutura do DOM.
- Seletores com no máximo 3 níveis; prefira uma classe. Use `:where()` para manter especificidade baixa e `:is()` para agrupar.
- Para sobrescrever o tema, iguale a especificidade (o `gohub.css` vem depois). `!important` só contra regra do tema que também use `!important` (utilitários) ou que seja replicada em muitos seletores, sempre com comentário do motivo.

## Tokens
- Reutilize as variáveis do tema: `--bs-primary` (#6576ff), `--bs-secondary` (#364a63), `--bs-body-color`, `--bs-body-bg`, `--bs-border-color`, `--bs-border-radius[-sm|-lg|-xl]`, `--bs-box-shadow[-sm|-lg]`, `--bs-body-font-family`, `--bs-body-font-size` (0.875rem), `--bs-body-line-height` (1.65).
- Token próprio (`--gohub-*`) só quando não houver equivalente `--bs-*`, declarado na seção de tokens de `gohub.css`. Nada de cor/raio/sombra literal repetido.
- Custom properties não funcionam em `@media`: use os breakpoints literais do tema (576, 768, 992, 1200, 1540px).

## Layout e responsividade
- Mobile first: estilos base para telas pequenas e `@media (min-width: …)` para ampliar.
- Grid para layouts bidimensionais, Flexbox para alinhamento em uma dimensão; nada de `float` ou `position: absolute` como layout.
- Unidades relativas (`rem`, `%`, `ch`) e `clamp()`/`min()`/`max()`; evite `width`/`height` fixos onde o conteúdo cresce. Texto corrido com `max-inline-size: 70ch`.
- Sem rolagem horizontal involuntária: tabelas largas em `.table-responsive`, mídia com `max-width: 100%`.
- `z-index`: o tema já usa camadas (header fixo, sidebar, dropdown, modal). Não invente valores altos; posicione relativo à camada existente e comente.

## Base já fornecida pelo tema (não duplique)
- O reboot do Bootstrap já define `box-sizing: border-box`, `body { margin: 0 }`, fonte, `line-height` e `scroll-behavior: smooth` só com `prefers-reduced-motion: no-preference`. Não adicione outro reset/normalize.
- Fontes: Roboto e Nioicon locais (`assets/fonts/`). Não adicione fontes externas/CDN nem `@import`.

## Acessibilidade (WCAG 2.2 AA)
- Nunca remova `outline`/indicador de foco sem substituto; o foco visível do projeto está em `gohub.css` (`:focus-visible`).
- Contraste ≥ 4.5:1 para texto e ≥ 3:1 para texto grande, componentes e foco. Estado nunca só por cor.
- Estados `:hover`, `:focus-visible`, `:active` e `:disabled`/`[aria-disabled="true"]` distinguíveis.
- Área clicável mínima de 24×24 px (WCAG 2.5.8).
- Animação/transição nova respeita `prefers-reduced-motion` (já há regra global em `gohub.css`; não a contorne).

## Performance e animação
- Anime apenas `transform` e `opacity`; transições curtas (150–300ms) e coerentes com o tema.
- Sem seletores universais ou profundos fora da seção de preferências; sem hacks de navegador sem justificativa.
- Não há pipeline de build/purge: não tente remover CSS do tema; mantenha `gohub.css` enxuto e remova regras próprias sem uso (verifique com grep em `templates/` antes).
- Alvo: navegadores evergreen. `:is()`, `:where()`, `:focus-visible`, `clamp()`, `aspect-ratio`, container queries são permitidos; recurso mais novo que isso, com `@supports`.
