from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView as DjangoLoginView,
    LogoutView as DjangoLogoutView,
)
from django.contrib.staticfiles import finders
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from common import nav
from common.roles import es_admin, puede_config_puertas


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "common/dashboard.html"
    login_url = "common:login"
    redirect_field_name = "next"

    def dispatch(self, request, *args, **kwargs):
        # El resumen es solo para administradores; los demás roles van directo
        # a la primera pantalla que tienen en el menú.
        if request.user.is_authenticated and not es_admin(request.user):
            # Puertas sigue entrando a molinetes, como siempre: es su pantalla
            # de trabajo aunque el menú ponga antes las fichas de socios.
            if puede_config_puertas(request.user):
                return redirect("xsys_molinetes_config")
            destino = nav.inicio(request.user)
            if destino:
                return redirect(destino)
            # Sin ninguna pantalla asignada: una página con el menú vacío y el
            # botón de salir, en vez de un error sin salida.
            return render(request, "common/sin_pantallas.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Valores por defecto para evitar plantillas vacías
        context.setdefault("total_entities", 0)
        context.setdefault("last_update_text", "Última actualización: sin datos")
        context.setdefault("active_users", 0)
        context.setdefault("active_users_delta", "Variación semanal")
        context.setdefault("alerts", 0)
        context.setdefault("alerts_delta", "Sin cambios")
        context.setdefault("visits", 0)
        context.setdefault("visits_delta", "Resumen mensual")
        return context


class LoginView(DjangoLoginView):
    template_name = "common/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        # Solo el destino por defecto: pisar get_success_url() descartaba el
        # ?next= y mandaba al dashboard a quien venía de una página protegida
        # (p.ej. /avisos/ desde el monitor). Django ya valida que el next sea
        # del mismo host, así que no habilita redirecciones a sitios externos.
        return reverse_lazy("common:dashboard")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        username_field = getattr(form, "username_field", "username")
        if not isinstance(username_field, str):
            username_field = username_field.name

        username_form_field = form.fields.get(username_field)
        if username_form_field is not None:
            username_form_field.widget.attrs.update(
                {"class": "form-input", "placeholder": "Ingresá tu usuario"}
            )
        form.fields["password"].widget.attrs.update(
            {"class": "form-input", "placeholder": "Ingresá tu contraseña"}
        )
        return form


class LogoutView(DjangoLogoutView):
    next_page = reverse_lazy("common:login")


class ServiceWorkerView(View):
    def get(self, request, *args, **kwargs):
        service_worker_path = finders.find("common/js/service-worker.js")
        if not service_worker_path:
            raise Http404("Service worker no encontrado")

        with open(service_worker_path, encoding="utf-8") as service_worker_file:
            response = HttpResponse(
                service_worker_file.read(),
                content_type="application/javascript; charset=utf-8",
            )
        response["Service-Worker-Allowed"] = "/"
        response["Cache-Control"] = "no-cache"
        return response
