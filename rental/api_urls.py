from django.urls import path
from . import api

urlpatterns = [
    path("me", api.api_me, name="api_me"),
    path("properties/", api.api_properties, name="api_properties"),
    path("tenants/", api.api_tenants, name="api_tenants"),
    path("leases/", api.api_leases, name="api_leases"),
    path("rents/", api.api_rents, name="api_rents"),
]
