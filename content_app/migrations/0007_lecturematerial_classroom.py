from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('learning_app', '0005_question_type'),
        ('content_app', '0006_summaryvalidation_quality_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='lecturematerial',
            name='Classroom',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lectures', to='learning_app.classroom'),
        ),
    ]
