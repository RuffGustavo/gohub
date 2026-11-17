---
name: estilos-css
description: Cria, ajusta, audita ou refatora CSS do GoHub sobre o tema DashLite (Bootstrap 5.3) aplicando tokens, BEM, mobile first, acessibilidade WCAG 2.2 AA e performance, sem alterar a identidade visual. Use ao escrever ou revisar estilos.
---
# Estilos CSS

Padrões: `.claude/rules/css.md`. Arquivo de estilos próprios: `setup/static/assets/css/gohub.css`.

## 1. Entender
- O que precisa mudar visualmente e em quais telas/breakpoints. Mudança de identidade visual (cores, fontes, layout) exige pedido explícito.
- Verifique se utilitário ou componente do tema já resolve (busque a classe em `templates/exemplos/` e em `dashlite.css`). Se resolver, use no HTML e não escreva CSS.
- Para sobrescrever, encontre a regra exata do tema (`grep -n "\.classe" setup/static/assets/css/dashlite.css`) e sua especificidade.

## 2. Implementar
1. Na seção correta de `gohub.css` (tokens → base → componentes → utilitários → preferências).
2. Classe `gohub-*` em BEM; valores via `var(--bs-*)` ou token `--gohub-*` novo na seção de tokens.
3. Mobile first, com breakpoints do tema (576, 768, 992, 1200, 1540px).
4. Estados hover/focus-visible/active/disabled; animações só com `transform`/`opacity`.
5. Ajuste pontual: não reescreva blocos que já funcionam.

## 3. Auditar
Não há stylelint nem pipeline de build: a auditoria é manual. Não instale ferramentas sem aprovação.
- [ ] Sem regra duplicada ou já coberta pelo tema; nenhum reset extra.
- [ ] Especificidade mínima; `!important` só com comentário justificando.
- [ ] Nenhum ID, tag solta ou cadeia de `nk-*` como seletor; nomes `gohub-*` em BEM.
- [ ] Sem cor/raio/sombra literal que tenha `--bs-*` equivalente.
- [ ] Responsivo de 320px a 1540px+, sem rolagem horizontal; nenhuma altura fixa que corte conteúdo.
- [ ] Contraste e foco visível preservados; estado não depende só de cor; alvo ≥ 24×24px.
- [ ] `prefers-reduced-motion` respeitado; animações só em `transform`/`opacity`.
- [ ] Nenhuma propriedade obsoleta, prefixo desnecessário, `@import` ou recurso externo.
- [ ] Classes novas usadas em `templates/` (grep) e classes removidas sem uso restante.
- [ ] HTML não foi alterado só para acomodar estilo (estrutura segue `.claude/rules/html.md`).
- [ ] Se viável, confira no navegador (`python manage.py runserver`) em larguras móvel e desktop; se não for viável, declare.

Em seguida, aplique a skill `validar-entrega`.

## 4. Relatório
1. Arquivos alterados (o CSS completo fica no arquivo e no diff; cole-o na resposta só quando não houver arquivo ou se pedido).
2. Estrutura e organização adotadas.
3. Boas práticas aplicadas.
4. Problemas encontrados e correções.
5. Responsividade.
6. Acessibilidade.
7. Performance.
8. Recomendações e limitações (ex.: contraste não medido com ferramenta, sem teste visual).
