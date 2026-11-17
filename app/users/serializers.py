from rest_framework import serializers

from .models import Usuario


class UsuarioSerializer(serializers.ModelSerializer):
    ativo = serializers.BooleanField(source="is_active", read_only=True)
    ultimo_login_em = serializers.DateTimeField(source="last_login", read_only=True)

    class Meta:
        model = Usuario
        fields = ["id", "nome", "username", "email", "ativo", "ultimo_login_em", "created_at"]
        read_only_fields = fields
