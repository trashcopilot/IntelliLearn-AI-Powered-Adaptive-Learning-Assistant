from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_app', '0005_summary_soft_delete'),
    ]

    operations = [
        migrations.AddField(
            model_name='summaryvalidation',
            name='QualityScore',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name='summaryvalidation',
            name='QualityStatus',
            field=models.CharField(default='low', max_length=16),
        ),
        migrations.AddField(
            model_name='summaryvalidation',
            name='QualityMetrics',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
