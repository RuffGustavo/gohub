from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from rest_framework import routers
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from app.users.views import UsuarioViewSet

schema_view = get_schema_view(
   openapi.Info(
      title="Documentação da API - GoHub",
      default_version='v1',
      description="Documentação da API GoHub",
      terms_of_service="https://www.google.com/policies/terms/",
      contact=openapi.Contact(email="email@email.com"),
      license=openapi.License(name="BSD License"),
   ),
   public=True,
)

router = routers.DefaultRouter()
router.register('usuarios', UsuarioViewSet, basename='usuario')

def health_check(request):
   return JsonResponse({
      "status": "healthy",
      "service": "Django API"
   })

urlpatterns = [
   path('admin/', admin.site.urls),
   path('contas/', include('app.users.urls')),
   path('health/', health_check, name='health'),
   path('api/', include(router.urls)),
   path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
   path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc')
]
