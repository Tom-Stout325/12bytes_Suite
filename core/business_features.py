from django.db import models


class BusinessFeature(models.Model):
    business = models.ForeignKey(
        "core.Business",
        on_delete=models.CASCADE,
        related_name="features",
    )
    code = models.CharField(max_length=100)

    class Meta:
        unique_together = ("business", "code")
        ordering = ["business", "code"]

    def __str__(self):
        return f"{self.business} - {self.code}"