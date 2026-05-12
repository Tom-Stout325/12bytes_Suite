from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contractor', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contractorw9submission',
            name='certification_accepted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='contractorw9submission',
            name='review_notes',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='contractorw9submission',
            name='review_status',
            field=models.CharField(choices=[('pending', 'Pending Review'), ('verified', 'Verified'), ('needs_update', 'Needs Update')], default='pending', max_length=20),
        ),
        migrations.AddField(
            model_name='contractorw9submission',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contractorw9submission',
            name='reviewed_by_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='contractorw9submission',
            name='signature_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contractorw9submission',
            name='uploaded_w9_document',
            field=models.FileField(blank=True, null=True, upload_to='w9/submissions/'),
        ),
        migrations.AddIndex(
            model_name='contractorw9submission',
            index=models.Index(fields=['business', 'review_status'], name='ctr_w9_bus_review_idx'),
        ),
    ]
