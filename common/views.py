from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView, LogoutView as DjangoLogoutView
from django.urls import reverse_lazy
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "common/dashboard.html"
    login_url = "common:login"
    redirect_field_name = "next"

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

    def get_success_url(self):
        return reverse_lazy("common:dashboard")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields[form.username_field].widget.attrs.update(
            {"class": "form-input", "placeholder": "Ingresá tu usuario"}
        )
        form.fields["password"].widget.attrs.update(
            {"class": "form-input", "placeholder": "Ingresá tu contraseña"}
        )
        return form


class LogoutView(DjangoLogoutView):
    next_page = reverse_lazy("common:login")
