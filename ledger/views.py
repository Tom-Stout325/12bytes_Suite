from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from django.views.generic import (
        CreateView, 
        DeleteView, 
        DetailView,
        ListView, 
        UpdateView,
    )

from .forms import (
        ContactForm, 
        SubCategoryForm, 
        TransactionForm, 
        TeamForm,
        JobForm
    )


from .models import (
        Contact, 
        SubCategory, 
        Transaction, 
        Team, 
        Category,
        Job,
    )


class TransactionListView(LoginRequiredMixin, ListView):
    model = Transaction
    template_name = "ledger/transactions/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 25

    def get_queryset(self):
        qs = (
            Transaction.objects.filter(business=self.request.business)
            .select_related("category", "subcategory", "team", "contact", "job", "vehicle")
            .order_by("-date", "-id")
        )

        q = (self.request.GET.get("q") or "").strip()
        ttype = (self.request.GET.get("type") or "").strip()  # income|expense
        cat = (self.request.GET.get("category") or "").strip()
        subcat = (self.request.GET.get("subcategory") or "").strip()

        if q:
            qs = qs.filter(
                Q(description__icontains=q)
                | Q(notes__icontains=q)
                | Q(subcategory__name__icontains=q)
                | Q(category__name__icontains=q)

                | Q(team__name__icontains=q)
            )

        if ttype in ("income", "expense"):
            qs = qs.filter(trans_type=ttype)

        if cat:
            try:
                cat_id = int(cat)
            except ValueError:
                cat_id = None
            if cat_id:
                qs = qs.filter(category_id=cat_id)

        if subcat:
            try:
                subcat_id = int(subcat)
            except ValueError:
                subcat_id = None
            if subcat_id:
                qs = qs.filter(subcategory_id=subcat_id)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # current filters
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["type"] = (self.request.GET.get("type") or "").strip()
        ctx["category"] = (self.request.GET.get("category") or "").strip()
        ctx["subcategory"] = (self.request.GET.get("subcategory") or "").strip()

        # dropdown options
        ctx["categories"] = Category.objects.filter(business=self.request.business, is_active=True).order_by("name")
        ctx["subcategories"] = SubCategory.objects.filter(business=self.request.business, is_active=True).order_by("name")

        # for pagination links (preserve filters/search)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["qs"] = params.urlencode()

        return ctx


class TransactionCreateView(LoginRequiredMixin, CreateView):
    model = Transaction
    form_class = TransactionForm
    template_name = "ledger/transactions/transaction_form.html"
    success_url = reverse_lazy("ledger:transaction_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class TransactionUpdateView(LoginRequiredMixin, UpdateView):
    model = Transaction
    form_class = TransactionForm
    template_name = "ledger/transactions/transaction_form.html"
    success_url = reverse_lazy("ledger:transaction_list")

    def get_queryset(self):
        return Transaction.objects.filter(business=self.request.business)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class TransactionDetailView(LoginRequiredMixin, DetailView):
    model = Transaction
    template_name = "ledger/transactions/transaction_detail.html"
    context_object_name = "transaction"

    def get_queryset(self):
        return (
            Transaction.objects.filter(business=self.request.business)
            .select_related("category", "subcategory", "team", "contact", "job", "vehicle")
        )


class TransactionDeleteView(LoginRequiredMixin, DeleteView):
    model = Transaction
    template_name = "ledger/transactions/transaction_confirm_delete.html"
    success_url = reverse_lazy("ledger:transaction_list")

    def get_queryset(self):
        return Transaction.objects.filter(business=self.request.business)



# <----------------------------------    P A Y E E   V I E W S          ------------------------------>


class ContactListView(LoginRequiredMixin, ListView):
    model = Contact
    template_name = "ledger/contacts/contact_list.html"
    context_object_name = "contacts"
    paginate_by = 25

    def get_queryset(self):
        qs = Contact.objects.filter(business=self.request.business).order_by("display_name")
        q = (self.request.GET.get("q") or "").strip()
        role = (self.request.GET.get("role") or "").strip()

        if q:
            qs = qs.filter(
                Q(display_name__icontains=q)
                | Q(legal_name__icontains=q)
                | Q(business_name__icontains=q)
                | Q(email__icontains=q)
            )

        if role == "vendor":
            qs = qs.filter(is_vendor=True)
        elif role == "customer":
            qs = qs.filter(is_customer=True)
        elif role == "contractor":
            qs = qs.filter(is_contractor=True)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["role"] = (self.request.GET.get("role") or "").strip()
        return ctx


class ContactCreateView(LoginRequiredMixin, CreateView):
    model = Contact
    form_class = ContactForm
    template_name = "ledger/contacts/contact_form.html"
    success_url = reverse_lazy("ledger:contact_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class ContactUpdateView(LoginRequiredMixin, UpdateView):
    model = Contact
    form_class = ContactForm
    template_name = "ledger/contacts/contact_form.html"
    success_url = reverse_lazy("ledger:contact_list")

    def get_queryset(self):
        return Contact.objects.filter(business=self.request.business)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class ContactDeleteView(LoginRequiredMixin, DeleteView):
    model = Contact
    template_name = "ledger/contacts/contact_confirm_delete.html"
    success_url = reverse_lazy("ledger:contact_list")

    def get_queryset(self):
        return Contact.objects.filter(business=self.request.business)


# <-------------------------------  S U B C A T E G O R Y   V I E W S  ------------------------------>


class SubCategoryListView(LoginRequiredMixin, ListView):
    model = SubCategory
    template_name = "ledger/subcategories/subcategory_list.html"
    context_object_name = "subcategories"
    paginate_by = 50

    def get_queryset(self):
        qs = (
            SubCategory.objects.filter(business=self.request.business)
            .select_related("category")
            .order_by(
                "category__category_type",
                "category__sort_order",
                "category__name",
                "sort_order",
                "name",
            )
        )

        q = (self.request.GET.get("q") or "").strip()
        ctype = (self.request.GET.get("type") or "").strip()
        cat = (self.request.GET.get("category") or "").strip()

        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(category__name__icontains=q))

        if ctype in ("income", "expense"):
            qs = qs.filter(category__category_type=ctype)

        if cat:
            try:
                cat_id = int(cat)
            except ValueError:
                cat_id = None
            if cat_id:
                qs = qs.filter(category_id=cat_id)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["type"] = (self.request.GET.get("type") or "").strip()
        ctx["category"] = (self.request.GET.get("category") or "").strip()

        ctx["categories"] = Category.objects.filter(
            business=self.request.business,
            is_active=True,
        ).order_by(
            "category_type",
            "sort_order",
            "name",
        )

        return ctx



class SubCategoryCreateView(LoginRequiredMixin, CreateView):
    model = SubCategory
    form_class = SubCategoryForm
    template_name = "ledger/subcategories/subcategory_form.html"
    success_url = reverse_lazy("ledger:subcategory_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class SubCategoryUpdateView(LoginRequiredMixin, UpdateView):
    model = SubCategory
    form_class = SubCategoryForm
    template_name = "ledger/subcategories/subcategory_form.html"
    success_url = reverse_lazy("ledger:subcategory_list")

    def get_queryset(self):
        return SubCategory.objects.filter(business=self.request.business)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class SubCategoryDeleteView(LoginRequiredMixin, DeleteView):
    model = SubCategory
    template_name = "ledger/subcategories/subcategory_confirm_delete.html"
    success_url = reverse_lazy("ledger:subcategory_list")

    def get_queryset(self):
        return SubCategory.objects.filter(business=self.request.business)


# <----------------------------------    J O B   V I E W S          ------------------------------>


class JobListView(LoginRequiredMixin, ListView):
    model = Job
    template_name = "ledger/jobs/job_list.html"
    context_object_name = "jobs"
    paginate_by = 25

    def get_queryset(self):
        qs = Job.objects.filter(business=self.request.business).order_by("-is_active", "-job_year", "job_number", "label")
        q = (self.request.GET.get("q") or "").strip()
        jtype = (self.request.GET.get("job_type") or "").strip()
        active = (self.request.GET.get("active") or "").strip()

        if q:
            qs = qs.filter(
                Q(job_number__icontains=q)
                | Q(label__icontains=q)
                | Q(client__display_name__icontains=q)
                | Q(city__icontains=q)
                | Q(address__icontains=q)
                | Q(notes__icontains=q)
            )

        if jtype:
            qs = qs.filter(job_type=jtype)

        if active in ("1", "0"):
            qs = qs.filter(is_active=(active == "1"))

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["job_type"] = (self.request.GET.get("job_type") or "").strip()
        ctx["active"] = (self.request.GET.get("active") or "").strip()
        ctx["job_type_choices"] = Job.JobType.choices

        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["qs"] = params.urlencode()
        return ctx


class JobDetailView(LoginRequiredMixin, DetailView):
    model = Job
    template_name = "ledger/jobs/job_detail.html"
    context_object_name = "job"

    def get_queryset(self):
        return Job.objects.filter(business=self.request.business).select_related("client")


class JobCreateView(LoginRequiredMixin, CreateView):
    model = Job
    form_class = JobForm
    template_name = "ledger/jobs/job_form.html"

    def get_success_url(self):
        return reverse_lazy("ledger:job_detail", kwargs={"pk": self.object.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class JobUpdateView(LoginRequiredMixin, UpdateView):
    model = Job
    form_class = JobForm
    template_name = "ledger/jobs/job_form.html"

    def get_queryset(self):
        return Job.objects.filter(business=self.request.business)

    def get_success_url(self):
        return reverse_lazy("ledger:job_detail", kwargs={"pk": self.object.pk})

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class JobDeleteView(LoginRequiredMixin, DeleteView):
    model = Job
    template_name = "ledger/jobs/job_confirm_delete.html"
    success_url = reverse_lazy("ledger:job_list")

    def get_queryset(self):
        return Job.objects.filter(business=self.request.business)

# <----------------------------------    T E A M   V I E W S          ------------------------------>


class TeamListView(LoginRequiredMixin, ListView):
    model = Team
    template_name = "ledger/teams/team_list.html"
    context_object_name = "teams"
    paginate_by = 50

    def get_queryset(self):
        qs = Team.objects.filter(business=self.request.business).order_by("sort_order", "name")
        q = (self.request.GET.get("q") or "").strip()
        status = (self.request.GET.get("status") or "").strip()

        if q:
            qs = qs.filter(name__icontains=q)

        if status == "active":
            qs = qs.filter(is_active=True)
        elif status == "inactive":
            qs = qs.filter(is_active=False)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = (self.request.GET.get("q") or "").strip()
        ctx["status"] = (self.request.GET.get("status") or "").strip()
        return ctx


class TeamCreateView(LoginRequiredMixin, CreateView):
    model = Team
    form_class = TeamForm
    template_name = "ledger/teams/team_form.html"
    success_url = reverse_lazy("ledger:team_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class TeamUpdateView(LoginRequiredMixin, UpdateView):
    model = Team
    form_class = TeamForm
    template_name = "ledger/teams/team_form.html"
    success_url = reverse_lazy("ledger:team_list")

    def get_queryset(self):
        return Team.objects.filter(business=self.request.business)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["business"] = self.request.business
        return kwargs

    def form_valid(self, form):
        form.instance.business = self.request.business
        return super().form_valid(form)


class TeamDeleteView(LoginRequiredMixin, DeleteView):
    model = Team
    template_name = "ledger/teams/team_confirm_delete.html"
    success_url = reverse_lazy("ledger:team_list")

    def get_queryset(self):
        return Team.objects.filter(business=self.request.business)



from django.contrib.auth.decorators import login_required

@login_required

def subcategory_requirements(request, pk: int):
    """Return requirement flags + helper hints for a subcategory (JSON).

    Used by the transaction form to show/hide fields and surface "required" hints.
    Server-side validation remains the source of truth (Transaction.clean()).
    """
    sc = get_object_or_404(SubCategory, pk=pk, business=request.business)

    requires = {
        "description": True,
        "amount": True,
        "date": True,
        "contact": bool(sc.requires_contact),
        "team": bool(sc.requires_team),
        "job": bool(sc.requires_job),
        "invoice_number": bool(sc.requires_invoice_number),
        "receipt": bool(sc.requires_receipt),
        "asset": bool(sc.requires_asset),
        "transport": bool(sc.requires_transport),
        # vehicle is nuanced: see vehicle_rule below
        "vehicle": bool(sc.requires_vehicle) or bool(sc.requires_transport),
    }

    # Vehicle rule semantics for UI:
    # - if transport required: vehicle required only when transport_type == business_vehicle
    # - else if requires_vehicle: vehicle required always
    if sc.requires_transport:
        vehicle_rule = "business_vehicle"
    elif sc.requires_vehicle:
        vehicle_rule = "always"
    else:
        vehicle_rule = "none"

    hints: list[str] = []
    if sc.account_type:
        hints.append(f"Account type: {sc.get_account_type_display()}.")

    if sc.requires_transport:
        hints.append("Choose Business vehicle to select a Vehicle.")

    if sc.requires_contact:
        role = (sc.contact_role or "any")
        if role and role != "any":
            hints.append(f"Contact role needed: {role}.")

    payload = {
        "id": sc.pk,
        "name": sc.name,
        "account_type": sc.account_type,
        "category": {
            "id": sc.category_id,
            "name": sc.category.name,
            "type": sc.category.category_type,
        },
        "requires": requires,
        "contact_role": sc.contact_role,
        "vehicle_rule": vehicle_rule,
        "hints": hints,
    }
    return JsonResponse(payload)
