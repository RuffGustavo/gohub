---
name: novo-endpoint
description: Implementa um recurso REST no GoHub com DRF — serializer, ViewSet, permissões, filtros, registro no router e testes de acesso. Use ao criar ou ampliar endpoints em /api/.
---
# Novo endpoint DRF

1. **Planejar**: recurso, ações necessárias, quem acessa cada ação, se há dono por objeto, campos que nunca podem sair na resposta. Procure serializer/viewset reutilizável antes de criar outro.
2. **Serializer** (`app/<app>/serializers.py`): `ModelSerializer`, `fields` explícito, `read_only_fields` para id, dono, status e timestamps; validações em `validate_*`.
3. **ViewSet** (`app/<app>/views.py`):
   ```python
   from django_filters.rest_framework import DjangoFilterBackend
   from rest_framework import viewsets

   class RecursoViewSet(viewsets.ModelViewSet):
       queryset = Recurso.objects.all()  # exigido por DjangoModelPermissions
       serializer_class = RecursoSerializer
       filter_backends = [DjangoFilterBackend]
       filterset_fields = ["status"]

       def get_queryset(self):
           # isolamento por dono, se o recurso tiver dono
           return super().get_queryset().filter(owner=self.request.user)

       def perform_create(self, serializer):
           serializer.save(owner=self.request.user)
   ```
   Use mixins se nem todas as ações forem necessárias. Mantenha `DjangoModelPermissions`; regra por objeto em `app/<app>/permissions.py`.
4. **Rota**: em `setup/urls.py`, `router.register("recursos", RecursoViewSet, basename="recurso")` → `/api/recursos/`.
5. **Permissões Django**: informe quais grupos/usuários precisam de `add/change/delete_<model>`; não conceda via código ou migration sem aprovação.
6. **Testes** (`app/<app>/tests/test_api.py`): cenários de `.claude/rules/tests.md`.
7. **Swagger**: confira `/swagger/`; nada interno em docstrings.
8. Conclua com a skill `validar-entrega`; endpoint com dados sensíveis → ofereça o agent `security-auditor`.
