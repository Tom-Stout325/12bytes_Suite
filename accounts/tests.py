from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    STORAGES={
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
)
class LoginPageTests(TestCase):
    def setUp(self):
        self.email = "pilot@example.com"
        self.password = "A-strong-test-password-123"
        self.user = get_user_model().objects.create_user(
            username="pilot",
            email=self.email,
            password=self.password,
        )
        self.login_url = reverse("account_login")

    def test_login_page_contains_existing_form_and_modal_trigger(self):
        response = self.client.get(self.login_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="suiteLoginModal"')
        self.assertContains(response, 'data-bs-target="#suiteLoginModal"')
        self.assertContains(response, 'name="login"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "/static/images/suites/suites-splash.png")
        self.assertContains(response, "/static/images/suites-splash-mobile.png")

    def test_csrf_enabled_login_authenticates_and_keeps_redirect(self):
        client = Client(enforce_csrf_checks=True)
        get_response = client.get(self.login_url)
        csrf_token = get_response.cookies["csrftoken"].value

        response = client.post(
            self.login_url,
            {
                "login": self.email,
                "password": self.password,
                "csrfmiddlewaretoken": csrf_token,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        self.assertEqual(int(client.session["_auth_user_id"]), self.user.pk)

    def test_invalid_login_shows_errors_and_reopens_modal(self):
        response = self.client.post(
            self.login_url,
            {"login": self.email, "password": "incorrect-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertContains(response, "bootstrap.Modal.getOrCreateInstance")
        self.assertContains(response, 'id="suiteLoginModal"')
