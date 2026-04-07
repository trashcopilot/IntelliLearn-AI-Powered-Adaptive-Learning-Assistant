from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_app', '0004_store_materials_in_database'),
    ]

    operations = [
        migrations.AddField(
            model_name='summary',
            name='ArchivedAt',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='summary',
            name='IsArchived',
            field=models.BooleanField(default=False),
        ),
    ]
