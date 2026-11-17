---
paths:
  - "**/serializers.py"
  - "**/serializers/**/*.py"
  - "**/views.py"
  - "**/views/**/*.py"
  - "**/permissions.py"
  - "**/filters.py"
  - "**/urls.py"
---
# API (DRF)

Defaults em `REST_FRAMEWORK` (`setup/settings.py`): Basic + Session auth, `DjangoModelPermissions`, `PageNumberPagination` (20). Não altere globalmente; sobrescreva por view com justificativa.

## Permissões
- `DjangoModelPermissions` exige atributo `queryset` na view (mesmo sobrescrevendo `get_queryset`).
- Ele só checa `add/change/delete_<model>`; GET/HEAD/OPTIONS passam para qualquer autenticado. Recurso com dono ou sensível: filtre em `get_queryset()` e/ou use permissão de objeto (`has_object_permission`) em `<app>/permissions.py`. Objeto alheio deve retornar 404.
- Nunca `AllowAny` ou `permission_classes = []` sem aprovação. Endpoints anônimos (ex.: `/health/`) não retornam dados internos.
- Exponha só as ações necessárias (mixins / `ReadOnlyModelViewSet`); não deixe `destroy`/`update` "de brinde".

## Serializers
- `fields` explícito; nunca `"__all__"` nem `exclude`.
- Dono, status, flags, timestamps em `read_only_fields`, definidos no servidor: `serializer.save(owner=self.request.user)` em `perform_create`. Nunca aceite `user`/`owner`/`is_staff` do payload.
- Validação em `validate_<campo>`/`validate`; regra de negócio compartilhada fica num único lugar da app (model ou módulo de serviço), não duplicada em serializer, view e admin.

## Filtros, rotas, erros
- Não há `DEFAULT_FILTER_BACKENDS`: declare `filter_backends = [DjangoFilterBackend]` (e `OrderingFilter` se preciso) na view, senão `filterset_fields` é ignorado em silêncio. `ordering_fields` explícito.
- Registre no `router` de `setup/urls.py` (`router.register("recursos", RecursoViewSet, basename="recurso")`) → `/api/recursos/`. Não crie outro prefixo de API.
- Erros via exceções do DRF (`ValidationError`, `PermissionDenied`, `NotFound`); nunca devolva `str(exc)` ou traceback.
- `select_related`/`prefetch_related` em `get_queryset` para evitar N+1.
- Swagger é público (`public=True`) e lista tudo que está no router: nada de detalhes internos em docstrings/`help_text`.
