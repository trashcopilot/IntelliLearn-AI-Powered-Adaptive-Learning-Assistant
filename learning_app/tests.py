from django.test import TestCase
from django.urls import reverse

from content_app.models import LectureMaterial, Summary
from users_app.models import Role, User

from .models import Classroom, ClassroomEnrollment, Concept, Question


class StudentClassroomGateTests(TestCase):
    def setUp(self):
        self.educator_role = Role.objects.create(RoleName='Educator')
        self.student_role = Role.objects.create(RoleName='Student')

        self.educator = User.objects.create_user(
            username='educator1',
            password='StrongPass123!',
            Role=self.educator_role,
        )
        self.student = User.objects.create_user(
            username='student1',
            password='StrongPass123!',
            Role=self.student_role,
        )

        self.class_a = Classroom.objects.create(Name='Biology 101', CreatedBy=self.educator)
        self.class_b = Classroom.objects.create(Name='Chemistry 101', CreatedBy=self.educator)

    def test_student_is_sent_to_classroom_gate_from_dashboard_when_not_selected(self):
        self.client.force_login(self.student)

        response = self.client.get(reverse('learning:student_dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('learning:student_classrooms'), fetch_redirect_response=False)

    def test_continue_button_is_disabled_until_class_selected(self):
        ClassroomEnrollment.objects.create(Classroom=self.class_a, Student=self.student, IsActive=True)

        self.client.force_login(self.student)
        response = self.client.get(reverse('learning:student_classrooms'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Dashboard')
        self.assertContains(response, 'disabled aria-disabled="true"')

    def test_student_can_join_multiple_classes_and_return_to_gate(self):
        self.client.force_login(self.student)
        join_url = reverse('learning:join_classroom')

        response_a = self.client.post(join_url, {'join_code': self.class_a.JoinCode})
        response_b = self.client.post(join_url, {'join_code': self.class_b.JoinCode})

        self.assertRedirects(response_a, reverse('learning:student_classrooms'), fetch_redirect_response=False)
        self.assertRedirects(response_b, reverse('learning:student_classrooms'), fetch_redirect_response=False)

        enrollments = ClassroomEnrollment.objects.filter(Student=self.student, IsActive=True)
        self.assertEqual(enrollments.count(), 2)
        self.assertSetEqual(
            set(enrollments.values_list('Classroom_id', flat=True)),
            {self.class_a.pk, self.class_b.pk},
        )

        session = self.client.session
        self.assertEqual(session.get('student_active_classroom_id'), self.class_b.pk)

    def test_student_can_select_class_and_open_dashboard(self):
        ClassroomEnrollment.objects.create(Classroom=self.class_a, Student=self.student, IsActive=True)
        ClassroomEnrollment.objects.create(Classroom=self.class_b, Student=self.student, IsActive=True)

        self.client.force_login(self.student)
        select_url = reverse('learning:select_classroom', args=[self.class_a.pk])

        select_response = self.client.post(select_url)
        self.assertRedirects(select_response, reverse('learning:student_classrooms'), fetch_redirect_response=False)

        session = self.client.session
        self.assertEqual(session.get('student_active_classroom_id'), self.class_a.pk)

        gate_response = self.client.get(reverse('learning:student_classrooms'))
        self.assertEqual(gate_response.status_code, 200)
        self.assertContains(gate_response, 'Open Dashboard')
        self.assertNotContains(gate_response, 'disabled aria-disabled="true"')
        self.assertContains(gate_response, f'href="{reverse("learning:student_dashboard")}"')

        dashboard_response = self.client.get(reverse('learning:student_dashboard'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertTemplateUsed(dashboard_response, 'student_dashboard.html')
        self.assertContains(dashboard_response, 'Selected classroom:')
        self.assertContains(dashboard_response, 'Biology 101')


class SelectedClassScopingTests(TestCase):
    def setUp(self):
        self.educator_role = Role.objects.create(RoleName='Educator')
        self.student_role = Role.objects.create(RoleName='Student')

        self.educator_a = User.objects.create_user(
            username='educator_a',
            password='StrongPass123!',
            Role=self.educator_role,
        )
        self.educator_b = User.objects.create_user(
            username='educator_b',
            password='StrongPass123!',
            Role=self.educator_role,
        )
        self.student = User.objects.create_user(
            username='student_scope',
            password='StrongPass123!',
            Role=self.student_role,
        )

        self.class_a = Classroom.objects.create(Name='Class A', CreatedBy=self.educator_a)
        self.class_b = Classroom.objects.create(Name='Class B', CreatedBy=self.educator_b)
        ClassroomEnrollment.objects.create(Classroom=self.class_a, Student=self.student, IsActive=True)
        ClassroomEnrollment.objects.create(Classroom=self.class_b, Student=self.student, IsActive=True)

        self.concept_a = Concept.objects.create(ConceptName='Concept A', Description='A')
        self.concept_b = Concept.objects.create(ConceptName='Concept B', Description='B')

        self.lecture_a = LectureMaterial.objects.create(
            Title='Lecture A',
            OriginalFileName='a.txt',
            MimeType='text/plain',
            FileSize=1,
            FileData=b'a',
            UploadedBy=self.educator_a,
        )
        self.lecture_b = LectureMaterial.objects.create(
            Title='Lecture B',
            OriginalFileName='b.txt',
            MimeType='text/plain',
            FileSize=1,
            FileData=b'b',
            UploadedBy=self.educator_b,
        )

        Summary.objects.create(Lecture=self.lecture_a, SummaryText='Summary A', IsVerified=True, IsArchived=False)
        Summary.objects.create(Lecture=self.lecture_b, SummaryText='Summary B', IsVerified=True, IsArchived=False)

        Question.objects.create(
            Lecture=self.lecture_a,
            Concept=self.concept_a,
            QuestionText='Question A',
            DifficultyLevel='Medium',
            CorrectAnswerText='A',
            IsPublished=True,
        )
        Question.objects.create(
            Lecture=self.lecture_b,
            Concept=self.concept_b,
            QuestionText='Question B',
            DifficultyLevel='Medium',
            CorrectAnswerText='B',
            IsPublished=True,
        )

    def test_dashboard_shows_only_selected_class_content(self):
        self.client.force_login(self.student)
        session = self.client.session
        session['student_active_classroom_id'] = self.class_a.ClassroomID
        session.save()

        response = self.client.get(reverse('learning:student_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Concept A')
        self.assertContains(response, 'Lecture A')
        self.assertNotContains(response, 'Concept B')
        self.assertNotContains(response, 'Lecture B')

    def test_start_quiz_blocks_concept_from_non_selected_class(self):
        self.client.force_login(self.student)
        session = self.client.session
        session['student_active_classroom_id'] = self.class_a.ClassroomID
        session.save()

        blocked = self.client.get(reverse('learning:start_quiz', args=[self.concept_b.ConceptID]))
        self.assertEqual(blocked.status_code, 403)

        allowed = self.client.get(reverse('learning:start_quiz', args=[self.concept_a.ConceptID]))
        self.assertEqual(allowed.status_code, 302)
        self.assertIn('/learning/quiz/', allowed.url)
