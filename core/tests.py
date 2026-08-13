from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from django.template.loader import render_to_string
from django.urls import resolve, reverse


class FlightPlanSidebarTests(SimpleTestCase):
    routes = {
        "flightlogs": "flightlogs:flightlog_list",
        "documents": "documents:documents_portal",
        "pilot": "pilot:profile",
        "assets": "assets:asset_list",
        "operations": "operations:ops_plan_index",
    }

    def render_sidebar(self, route_name):
        path = reverse(route_name)
        request = RequestFactory().get(path)
        request.user = AnonymousUser()
        request.resolver_match = resolve(path)
        return render_to_string("partials/sidebar.html", request=request)

    def test_flightplan_contains_existing_child_routes_on_desktop_and_mobile(self):
        html = self.render_sidebar("flightlogs:flightlog_list")

        self.assertEqual(html.count("> FlightPlan"), 2)
        self.assertIn('id="suiteFlightPlanMenu"', html)
        self.assertIn('id="suiteFlightPlanMenuMobile"', html)
        for label, route_name in (
            ("Flight Logs", "flightlogs:flightlog_list"),
            ("Documents", "documents:documents_portal"),
            ("Pilots", "pilot:profile"),
            ("Equipment", "assets:asset_list"),
            ("Operations", "operations:ops_plan_index"),
        ):
            self.assertEqual(html.count(f'href="{reverse(route_name)}"'), 2)
            self.assertEqual(html.count(f"> {label}"), 2)
        self.assertNotIn('class="suite-nav-link active" href="/flightlogs/"', html)

    def test_flightplan_and_current_child_are_active_for_each_app(self):
        for namespace, route_name in self.routes.items():
            with self.subTest(namespace=namespace):
                html = self.render_sidebar(route_name)
                self.assertEqual(html.count('aria-controls="suiteFlightPlanMenu"'), 1)
                self.assertIn(
                    'data-bs-target="#suiteFlightPlanMenu" aria-expanded="true"',
                    " ".join(html.split()),
                )
                child_url = reverse(route_name)
                self.assertEqual(
                    html.count(f'class="suite-subnav-link active" href="{child_url}"'),
                    2,
                )
