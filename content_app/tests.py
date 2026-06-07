from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from learning_app.models import Classroom
from users_app.models import Role, User

from .models import LectureMaterial, Summary


class EducatorClassroomGateTests(TestCase):
    def setUp(self):
        educator_role = Role.objects.create(RoleName='Educator')

        self.educator = User.objects.create_user(
            username='educator_gate',
            password='StrongPass123!',
            Role=educator_role,
        )

        self.class_a = Classroom.objects.create(Name='Class A', CreatedBy=self.educator)
        self.class_b = Classroom.objects.create(Name='Class B', CreatedBy=self.educator)

        lecture_a = LectureMaterial.objects.create(
            Title='Lecture A',
            OriginalFileName='a.txt',
            MimeType='text/plain',
            FileSize=1,
            FileData=b'a',
            UploadedBy=self.educator,
            Classroom=self.class_a,
        )
        lecture_b = LectureMaterial.objects.create(
            Title='Lecture B',
            OriginalFileName='b.txt',
            MimeType='text/plain',
            FileSize=1,
            FileData=b'b',
            UploadedBy=self.educator,
            Classroom=self.class_b,
        )

        Summary.objects.create(Lecture=lecture_a, SummaryText='Summary A', IsVerified=True)
        Summary.objects.create(Lecture=lecture_b, SummaryText='Summary B', IsVerified=True)

    def test_users_dashboard_routes_educator_to_gate(self):
        self.client.force_login(self.educator)

        response = self.client.get(reverse('users:dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('content:educator_classrooms'), fetch_redirect_response=False)

    def test_educator_dashboard_requires_selected_classroom(self):
        self.client.force_login(self.educator)

        response = self.client.get(reverse('content:educator_dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('content:educator_classrooms'), fetch_redirect_response=False)

    def test_dashboard_shows_only_selected_classroom_summaries(self):
        self.client.force_login(self.educator)
        session = self.client.session
        session['educator_active_classroom_id'] = self.class_a.ClassroomID
        session.save()

        response = self.client.get(reverse('content:educator_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Selected classroom:')
        self.assertContains(response, 'Class A')
        self.assertContains(response, 'Lecture A')
        self.assertNotContains(response, 'Lecture B')

    def test_deactivating_selected_classroom_clears_educator_session_selection(self):
        self.client.force_login(self.educator)
        session = self.client.session
        session['educator_active_classroom_id'] = self.class_a.ClassroomID
        session.save()

        response = self.client.post(
            reverse('learning:set_classroom_status', args=[self.class_a.ClassroomID]),
            {'is_active': '0'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('content:educator_classrooms'), fetch_redirect_response=False)

        session = self.client.session
        self.assertNotIn('educator_active_classroom_id', session)

        dashboard_response = self.client.get(reverse('content:educator_dashboard'))
        self.assertRedirects(dashboard_response, reverse('content:educator_classrooms'), fetch_redirect_response=False)

    @patch('content_app.views.run_background')
    def test_multi_file_upload_queues_one_batch_job(self, mock_run_background):
        self.client.force_login(self.educator)
        session = self.client.session
        session['educator_active_classroom_id'] = self.class_a.ClassroomID
        session.save()

        upload_one = SimpleUploadedFile('alpha.txt', b'alpha notes', content_type='text/plain')
        upload_two = SimpleUploadedFile('beta.txt', b'beta notes', content_type='text/plain')

        response = self.client.post(
            reverse('content:educator_dashboard'),
            {
                'Title': 'Batch Lecture',
                'SummaryMode': 'brief',
                'UploadFile': [upload_one, upload_two],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_run_background.call_count, 1)

        task, created_material_pks, educator_pk, summary_mode = mock_run_background.call_args.args
        self.assertEqual(task.__name__, '_process_material_ai_batch')
        self.assertEqual(educator_pk, self.educator.pk)
        self.assertEqual(summary_mode, 'brief')

        uploaded_materials = list(
            LectureMaterial.objects.filter(UploadedBy=self.educator, Classroom=self.class_a).order_by('pk').values_list('pk', flat=True)
        )
        self.assertEqual(created_material_pks, uploaded_materials[-2:])
        self.assertEqual(len(created_material_pks), 2)
