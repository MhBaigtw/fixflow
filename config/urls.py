from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.views import EmailTokenObtainPairView, MeView
from apps.core.views import HealthView
from apps.maintenance.views import MaintenanceRequestViewSet
from apps.properties.views import (
    EndTenancyView,
    PropertyViewSet,
    UnitListCreateView,
    UnitTenancyView,
)

router = SimpleRouter()
router.register("properties", PropertyViewSet, basename="property")
router.register("requests", MaintenanceRequestViewSet, basename="request")

api_patterns = [
    path("auth/token/", EmailTokenObtainPairView.as_view(), name="token_obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("health/", HealthView.as_view(), name="health"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("properties/<int:property_id>/units/", UnitListCreateView.as_view(), name="unit-list"),
    path("units/<int:unit_id>/tenancies/", UnitTenancyView.as_view(), name="unit-tenancies"),
    path("tenancies/<int:tenancy_id>/end/", EndTenancyView.as_view(), name="tenancy-end"),
    *router.urls,
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
]

handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
