from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_app', '0007_lecturematerial_classroom'),
    ]

    operations = [
        migrations.AddField(
            model_name='lecturematerial',
            name='SourceFile',
            field=models.FileField(blank=True, null=True, upload_to='lecture_materials/'),
        ),
    ]
