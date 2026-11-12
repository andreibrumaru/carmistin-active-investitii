import re

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from . import models as m

User = get_user_model()


class ProfilInline(admin.StackedInline):
    model = m.Profil
    fk_name = "user"
    can_delete = False


admin.site.unregister(User)


@admin.register(User)
class UtilizatorAdmin(UserAdmin):
    inlines = [ProfilInline]
    list_display = ("username", "first_name", "last_name", "email", "rol", "is_active")

    @admin.display(description="rol")
    def rol(self, obj):
        return getattr(getattr(obj, "profil", None), "get_rol_display", lambda: "")()


@admin.register(m.Locatie)
class LocatieAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        vechi = m.Locatie.objects.filter(pk=obj.pk).values_list("stare_activ", flat=True).first() if change else ""
        super().save_model(request, obj, form, change)
        if obj.stare_activ != vechi:  # schimbarea de stare rămâne în istoric și când se face din Administrare
            m.IstoricStareActiv.objects.create(locatie=obj, stare_veche=vechi or "", stare_noua=obj.stare_activ, user=request.user,
                                               motiv="Modificat din Administrare." if change else "Activ înregistrat în registru.")

    list_display = ("cod", "denumire", "stare_activ", "proprietar", "inchiriere_externa", "proprietar_extern", "linie", "status", "detinere")
    list_display_links = ("cod", "denumire")
    list_editable = ("proprietar",)  # firma din grup se poate schimba direct din listă (ex. la unirea duplicatelor)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "proprietar":
            kwargs["queryset"] = m.Societate.objects.filter(in_grup=True).order_by("denumire")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def lookup_allowed(self, lookup, value):
        return lookup in ("proprietar__id__exact", "proprietar_extern__id__exact") or super().lookup_allowed(lookup, value)

    list_filter = ("tip", "stare_activ", "inchiriere_externa", "linie", "status", "detinere", "grup")
    search_fields = ("cod", "cod_original", "denumire", "adresa", "carti_funciare")


class SocietateAdminForm(forms.ModelForm):
    class Meta:
        model = m.Societate
        fields = "__all__"

    def clean_prefix_cod(self):
        p = (self.cleaned_data.get("prefix_cod") or "").strip().upper()
        if p and not re.fullmatch(r"[A-Z]{2,5}", p):
            raise forms.ValidationError("Codul firmei are 2–5 litere (fără cifre, spații sau diacritice).")
        if p:
            alta = m.Societate.objects.filter(prefix_cod__iexact=p, in_grup=True).exclude(pk=self.instance.pk).first()
            if alta:
                raise forms.ValidationError(f"Codul {p} este deja al firmei {alta}.")
        return p


class PropunereFiltru(admin.SimpleListFilter):
    title = "propunere de clasificare"
    parameter_name = "propunere"

    def lookups(self, request, model_admin):
        return [("externa", "Pare proprietar extern (chirie)"), ("grup", "Pare firmă din grup")]

    def queryset(self, request, qs):
        if not self.value():
            return qs
        from .firme import propunere
        ids = [s.pk for s in qs.prefetch_related("locatii") if propunere(s)[0] == (self.value() == "externa")]
        return qs.filter(pk__in=ids)


class FaraCodFiltru(admin.SimpleListFilter):
    title = "cod firmă"
    parameter_name = "are_cod"

    def lookups(self, request, model_admin):
        return [("nu", "Fără cod"), ("da", "Cu cod")]

    def queryset(self, request, qs):
        if self.value() == "nu":
            return qs.filter(prefix_cod="")
        if self.value() == "da":
            return qs.exclude(prefix_cod="")
        return qs


@admin.register(m.Societate)
class SocietateAdmin(admin.ModelAdmin):
    """Firmele grupului, fiecare cu codul ei (ex. Avicarvil SRL → AVI). Proprietarii din afara grupului se mută
    cu acțiunea de mai jos în baza separată „proprietari externi (închirieri externe)”."""
    form = SocietateAdminForm
    list_display = ("denumire", "prefix_cod", "cui", "nr_active", "active_fara_cod", "coduri_folosite", "propunere_afisata", "stergere")
    list_display_links = ("denumire",)
    list_editable = ("prefix_cod",)
    list_filter = (PropunereFiltru, FaraCodFiltru)
    search_fields = ("denumire", "cui", "prefix_cod")
    list_per_page = 200
    actions = ["sterge_fara_active", "muta_externi", "aloca_coduri", "uneste_firme"]

    @staticmethod
    def _legaturi(obj):
        """Ce împiedică ștergerea firmei: active, proiecte (beneficiar) sau facturi emise."""
        return [t for t, n in (("active", obj.locatii.count()), ("proiecte", obj.proiecte.count()),
                               ("facturi", obj.facturi_emise.count())) if n]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and self._legaturi(obj):
            return False  # întâi se mută activele pe firma corectă, apoi se poate șterge
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        a = super().get_actions(request)
        a.pop("delete_selected", None)  # înlocuit de „Șterge firmele bifate fără active”
        return a

    @admin.display(description="ștergere")
    def stergere(self, obj):
        leg = self._legaturi(obj)
        if not leg:
            return format_html('<a class="deletelink" href="{}">Șterge</a>', reverse("admin:platforma_societate_delete", args=[obj.pk]))
        if "active" in leg:
            return format_html('<span style="color:#888" title="Mutați activele pe firma corectă, apoi butonul devine activ">'
                               '🔒 are active – <a href="{}?proprietar__id__exact={}">mută-le</a></span>',
                               reverse("admin:platforma_locatie_changelist"), obj.pk)
        return format_html('<span style="color:#888">🔒 are {}</span>', ", ".join(leg))

    @admin.action(description="Șterge firmele bifate (doar cele fără active, proiecte sau facturi)")
    def sterge_fara_active(self, request, queryset):
        sterse, blocate = [], []
        for s in list(queryset):
            if self._legaturi(s):
                blocate.append(s.denumire)
            else:
                sterse.append(s.denumire)
                s.delete()
        if sterse:
            self.message_user(request, f"Șterse: {', '.join(sterse)}.", messages.SUCCESS)
        if blocate:
            self.message_user(request, "Nu s-au șters (au încă active, proiecte sau facturi – mutați-le întâi pe firma corectă): "
                                       + ", ".join(blocate), messages.WARNING)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(in_grup=True).annotate(
            _n=Count("locatii", distinct=True),
            _fc=Count("locatii", filter=Q(locatii__cod__isnull=True) | Q(locatii__cod=""), distinct=True),
        ).prefetch_related("locatii")

    @admin.display(description="active", ordering="_n")
    def nr_active(self, obj):
        if not obj._n:
            return "0"
        return format_html('<a href="{}?proprietar__id__exact={}" title="vezi și mută activele">{}</a>',
                           reverse("admin:platforma_locatie_changelist"), obj.pk, obj._n)

    @admin.display(description="active fără cod", ordering="_fc")
    def active_fara_cod(self, obj):
        return obj._fc or "—"

    @admin.display(description="coduri folosite în registru")
    def coduri_folosite(self, obj):
        pref = sorted({l.cod.split(".")[0] for l in obj.locatii.all() if l.cod})
        return ", ".join(pref) or "—"

    @admin.display(description="propunere")
    def propunere_afisata(self, obj):
        from .firme import propunere
        ext, motiv = propunere(obj)
        return format_html('<span style="color:#b45309">⚠ pare externă – {}</span>', motiv) if ext else "—"

    @admin.action(description="Mută firmele bifate în baza proprietarilor externi (închirieri)")
    def muta_externi(self, request, queryset):
        from .firme import muta_in_externi
        ok, n = [], 0
        for s in list(queryset):
            try:
                _, k = muta_in_externi(s, request.user)
                ok.append(s.denumire)
                n += k
            except ValueError as e:
                self.message_user(request, str(e), messages.ERROR)
        if ok:
            self.message_user(request, f"{len(ok)} firme mutate în proprietari externi; {n} active marcate ca închirieri externe "
                                       "(chiriașul din grup a fost preluat din coloana „utilizator”).", messages.SUCCESS)

    @admin.action(description="Alocă coduri activelor fără cod (după codul firmei)")
    def aloca_coduri(self, request, queryset):
        from .firme import aloca_coduri
        noi, fara = [], []
        for s in queryset:
            if not s.prefix_cod:
                fara.append(s.denumire)
                continue
            noi += aloca_coduri(s)
        if noi:
            self.message_user(request, f"{len(noi)} active au primit cod: {', '.join(noi[:15])}{' …' if len(noi) > 15 else ''}", messages.SUCCESS)
        if fara:
            self.message_user(request, "Fără cod de firmă (completați-l întâi): " + ", ".join(fara), messages.WARNING)
        if not noi and not fara:
            self.message_user(request, "Toate activele firmelor bifate au deja cod.")

    @admin.action(description="Unește firmele bifate (duplicate) – rămâne cea cu cod / cu cele mai multe active")
    def uneste_firme(self, request, queryset):
        from .firme import uneste
        firme = sorted(queryset, key=lambda s: (not s.prefix_cod, -s._n, len(s.denumire)))
        if len(firme) < 2:
            self.message_user(request, "Bifați cel puțin două firme.", messages.WARNING)
            return
        n = uneste(firme[0], firme[1:])
        self.message_user(request, f"Unite în „{firme[0]}”: {', '.join(s.denumire for s in firme[1:])} ({n} active mutate).", messages.SUCCESS)


@admin.register(m.ProprietarExtern)
class ProprietarExternAdmin(admin.ModelAdmin):
    """Baza separată a proprietarilor din afara grupului (închirieri externe)."""
    list_display = ("denumire", "tip", "cui", "persoana_contact", "telefon", "email", "nr_active", "activ")
    list_filter = ("tip", "activ")
    search_fields = ("denumire", "cui", "persoana_contact", "adresa")
    list_editable = ("activ",)
    actions = ["muta_in_grup"]

    @admin.action(description="Mută înapoi în firmele grupului (greșit clasificat)")
    def muta_in_grup(self, request, queryset):
        from .firme import muta_in_grup
        n = 0
        for pe in list(queryset):
            n += muta_in_grup(pe)[1]
        self.message_user(request, f"Mutate în firmele grupului; {n} active nu mai sunt închirieri externe.", messages.SUCCESS)

    @admin.display(description="active închiriate")
    def nr_active(self, obj):
        return obj.locatii.count()


@admin.register(m.Nomenclator)
class NomenclatorAdmin(admin.ModelAdmin):
    list_display = ("tip", "valoare", "ordine", "activ")
    list_filter = ("tip",)
    list_editable = ("ordine", "activ")


@admin.register(m.Proiect)
class ProiectAdmin(admin.ModelAdmin):
    list_display = ("cod", "denumire", "beneficiar", "stare_rz", "faza", "status", "pm", "coordonator")
    list_filter = ("stare_rz", "faza", "status", "linie")
    search_fields = ("cod", "denumire", "nr_inregistrare")


@admin.register(m.JurnalAudit)
class JurnalAdmin(admin.ModelAdmin):
    list_display = ("moment", "user", "actiune", "model", "obiect", "proiect")
    list_filter = ("actiune", "model")
    search_fields = ("obiect",)

    def has_change_permission(self, *a):
        return False

    def has_delete_permission(self, *a):
        return False

    def has_add_permission(self, *a):
        return False


for model in (m.CursValutar, m.AccesProiect, m.RevizieRaportZero, m.LinieBuget, m.Contract, m.ActAditional, m.Factura, m.Plata,
              m.CertificatUrbanism, m.Aviz, m.Finantare, m.Obligatie, m.Activitate, m.ExtragereAI, m.ConditieContract):
    admin.site.register(model)


@admin.register(m.Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("titlu", "categorie", "proiect", "locatie", "versiune", "arhivat", "incarcat_de", "incarcat_la")
    list_filter = ("categorie", "arhivat")

    def has_delete_permission(self, *a):
        return False


@admin.register(m.TipAutorizatie)
class TipAutorizatieAdmin(admin.ModelAdmin):
    list_display = ("scurt", "denumire", "emitent", "responsabil", "ordine", "activ")
    list_editable = ("responsabil", "ordine", "activ")


admin.site.register(m.CerintaAutorizatie)
admin.site.register(m.StandardCertificat)


@admin.register(m.EmailAvizRZ)
class EmailAvizRZAdmin(admin.ModelAdmin):
    list_display = ("proiect", "revizie", "catre", "trimis_la", "salvat_local", "eroare", "raspuns", "raspuns_la", "raspuns_ip")
    readonly_fields = [f.name for f in m.EmailAvizRZ._meta.fields]


@admin.register(m.IstoricStareActiv)
class IstoricStareActivAdmin(admin.ModelAdmin):
    """Doar consultare: istoricul stărilor nu se modifică și nu se șterge."""
    list_display = ("locatie", "stare_veche", "stare_noua", "data", "user", "motiv")
    list_filter = ("stare_noua",)
    search_fields = ("locatie__cod", "locatie__denumire", "motiv")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
