from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "Carmistin – Active & Investiții (administrare)"
admin.site.site_title = "Active & Investiții"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("autentificare/", auth_views.LoginView.as_view(), name="login"),
    path("iesire/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("platforma.urls")),
]
