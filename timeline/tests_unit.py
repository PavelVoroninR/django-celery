import json
from datetime import datetime

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from mixer.backend.django import mixer

import lessons.models as lessons
from elk.utils.testing import ClientTestCase, create_customer, create_teacher
from hub.models import Class
from teachers.models import WorkingHours
from timeline.models import Entry as TimelineEntry


class EntryTestCase(TestCase):
    fixtures = ('crm',)

    def setUp(self):
        self.teacher1 = create_teacher()
        self.teacher2 = create_teacher()

    def test_entry_naming(self):
        """
        Timeline entry with an assigned name should return the name of event.

        Without an event, timeline entry should return placeholder 'Usual lesson'.
        """
        lesson = mixer.blend(lessons.OrdinaryLesson, name='Test_Lesson_Name')
        entry = mixer.blend(TimelineEntry, teacher=self.teacher1, lesson=lesson)
        self.assertEqual(str(entry), 'Test_Lesson_Name')

    def test_default_scope(self):
        active_lesson = mixer.blend(lessons.OrdinaryLesson, name='Active_lesson')
        inactive_lesson = mixer.blend(lessons.OrdinaryLesson, name='Inactive_lesson')

        active_entry = mixer.blend(TimelineEntry, teacher=self.teacher1, lesson=active_lesson, active=1)
        inactive_entry = mixer.blend(TimelineEntry, teacher=self.teacher1, lesson=inactive_lesson, active=0)

        active_pk = active_entry.pk
        inactive_pk = inactive_entry.pk

        entries = TimelineEntry.objects.all().values_list('id', flat=True)
        self.assertIn(active_pk, entries)
        self.assertNotIn(inactive_pk, entries)

    def test_availabe_slot_count(self):
        event = mixer.blend(lessons.MasterClass, slots=10, host=self.teacher1)
        entry = mixer.blend(TimelineEntry, lesson=event, teacher=self.teacher1)
        entry.save()

        self.assertEqual(entry.slots, 10)

    def test_event_assigning(self):
        """
        Test if timeline entry takes all attributes from the event
        """
        lesson = mixer.blend(lessons.OrdinaryLesson)
        entry = mixer.blend(TimelineEntry, lesson=lesson, teacher=self.teacher1)

        self.assertEqual(entry.slots, lesson.slots)
        self.assertEqual(entry.end, entry.start + lesson.duration)

        self.assertEqual(entry.lesson_type, ContentType.objects.get(app_label='lessons', model='ordinarylesson'))

    def test_is_free(self):
        """
        Schedule a customer to a timeleine entry
        """
        lesson = mixer.blend(lessons.MasterClass, slots=10, host=self.teacher1)
        entry = mixer.blend(TimelineEntry, lesson=lesson, teacher=self.teacher1)
        entry.save()

        for i in range(0, 10):
            self.assertTrue(entry.is_free)
            self.assertEqual(entry.taken_slots, i)  # by the way let's test taken_slots count
            customer = create_customer()
            c = mixer.blend(Class, lesson_type=lesson.get_contenttype(), customer=customer)
            entry.classes.add(c)  # please don't use it in your code! use :model:`hub.Class`.assign_entry() instead
            entry.save()

        self.assertFalse(entry.is_free)

        """ Let's try to schedule more customers, then event allows """
        with self.assertRaises(ValidationError):
            customer = create_customer()
            c = mixer.blend(Class, lesson_type=lesson.get_contenttype(), customer=customer)
            entry.classes.add(c)  # please don't use it in your code! use :model:`hub.Class`.assign_entry() instead
            entry.save()

    def test_assign_entry_to_a_different_teacher(self):
        """
        We should not have possibility to assign an event with different host
        to someones timeline entry
        """
        lesson = mixer.blend(lessons.MasterClass, host=self.teacher1)

        with self.assertRaises(ValidationError):
            entry = mixer.blend(TimelineEntry, teacher=self.teacher2, lesson=lesson)
            entry.save()


class TestPermissions(ClientTestCase):
    def setUp(self):
        self.user = User.objects.create_user('user', 'te@ss.tt', '123')
        self.teacher = create_teacher()

        super().setUp()

    def test_create_form_permission(self):
        self.c.login(username='user', password='123')
        response = self.c.get('/timeline/%s/create/' % self.teacher.user.username)
        self.assertNotEqual(response.status_code, 200)

        self.c.logout()

        self.c.login(username=self.superuser_login, password=self.superuser_password)
        response = self.c.get('/timeline/%s/create/' % self.teacher.user.username)
        self.assertEqual(response.status_code, 200)


class TestCancellation(TestCase):
    """
    Actions with timeline entries with attached classes should
    influence the classes too, and vice-versa
    """
    def setUp(self):
        self.teacher = create_teacher()
        self.customer = create_customer()
        self.lesson = mixer.blend(lessons.MasterClass, host=self.teacher)

    def _buy_a_lesson(self, lesson):
        c = Class(
            customer=self.customer,
            lesson=lesson
        )
        c.save()
        return c

    def _create_entry(self, start=datetime(2032, 5, 3, 13, 30), end=datetime(2032, 5, 3, 14, 0)):
        return mixer.blend(
            TimelineEntry,
            teacher=self.teacher,
            lesson=self.lesson,
            start=start,
            end=end,
        )

    def test_cancel_entry(self):
        """
        When deleting a timeline entry, the class should become unscheduled.
        """
        c = self._buy_a_lesson(self.lesson)
        entry = self._create_entry()
        c.assign_entry(entry)
        c.save()

        self.assertTrue(c.is_scheduled)

        entry = TimelineEntry.objects.get(pk=entry.id)
        entry.delete()
        c.refresh_from_db()
        self.assertFalse(c.is_scheduled)

    def test_entry_autodelete(self):
        """
        Entry without taken slots with a lesson, that does not require an entry should delete itself.
        """
        lesson = mixer.blend(lessons.OrdinaryLesson)  # blend a lesson, that does not require a timeline entry
        c = self._buy_a_lesson(lesson)
        c.schedule(  # go through the simple scheduling process, without a special-crafted timeline entry
            teacher=self.teacher,
            date=datetime(2032, 5, 3, 12, 30),
            allow_besides_working_hours=True
        )
        c.save()
        entry = c.timeline
        self.assertIsInstance(entry, TimelineEntry)
        c.delete()
        self.assertFalse(TimelineEntry.objects.filter(pk=entry.pk).exists())  # during the class saving, entry should have deleted itself

    def test_entry_no_autodelete_on_lessons_that_require_a_timeline_entry(self):
        """
        Timeline entries for lessons, that require it, should not be deleted event when the class cancles.

        The difference with the previous test is that we buy a master class,
        which requires a timeline entry.
        """
        c = self._buy_a_lesson(self.lesson)  # self.lesson is a master class, so it requires an entry
        entry = self._create_entry()
        c.assign_entry(entry)
        c.save()
        entry = c.timeline
        self.assertIsInstance(entry, TimelineEntry)

        c.delete()
        self.assertTrue(TimelineEntry.objects.filter(pk=entry.pk).exists())  # auto-deletion mechanism should not be engaged


class TestFormContext(ClientTestCase):
    """
    Check for a bug: timeline entry edit form should edit entry for the owner
    of the entry, not for the current logged in user.
    """

    def setUp(self):
        self.teacher = create_teacher()
        super().setUp()

    def test_create_context(self):
        """
        Get create form and check for hidden field 'teacher',
        see template timeline/forms/entry_create.html
        """
        response = self.c.get('/timeline/%s/create/' % self.teacher.user.username)

        with self.assertHTML(response, 'form.form #teacher') as (input,):
            self.assertEquals(input.value, str(self.teacher.pk))

    def test_update_context(self):
        """
        Get update form and check for hidden field 'teacher',
        see template timeline/forms/entry_update.html
        """
        entry = mixer.blend(TimelineEntry, teacher=self.teacher)

        response = self.c.get('/timeline/%s/%d/update/' % (self.teacher.user.username, entry.pk))
        with self.assertHTML(response, 'form.form #teacher') as (input,):
            self.assertEquals(input.value, str(self.teacher.pk))


class TestCheckEntry(ClientTestCase):
    """
    :view:`timeline.check_entry` is a helper for the timeline creating form
    which checks entry validity — working hours and overlaping
    """
    def setUp(self):
        self.teacher = create_teacher()
        self.lesson = mixer.blend(lessons.MasterClass, host=self.teacher)
        self.entry = TimelineEntry(
            teacher=self.teacher,
            lesson=self.lesson,
            start=datetime(2016, 1, 18, 14, 10),
            end=datetime(2016, 1, 18, 14, 40),
            allow_overlap=False,
        )

        mixer.blend(WorkingHours, teacher=self.teacher, weekday=0, start='13:00', end='15:00')

        self.entry.save()
        super().setUp()

    def test_check_overlap_true(self):
        overlaps = self.__check_entry(
            username=self.teacher.user.username,
            start='2016-01-18 14:30',
            end='2016-01-18 15:00',
        )
        self.assertTrue(overlaps['is_overlapping'])

    def test_check_overlap_false(self):
        not_overlaps = self.__check_entry(
            username=self.teacher.user.username,
            start='2016-01-18 14:45',
            end='2016-01-18 15:15'
        )
        self.assertFalse(not_overlaps['is_overlapping'])

    def test_check_hours_true(self):
        fits = self.__check_entry(
            username=self.teacher.user.username,
            start='2032-05-03 14:00',  # monday
            end='2032-05-03 14:30',
        )
        self.assertTrue(fits['is_fitting_hours'])

    def test_check_hours_false(self):
        fits = self.__check_entry(
            username=self.teacher.user.username,
            start='2032-05-03 14:00',  # monday
            end='2032-05-03 15:30',  # half-hour late
        )
        self.assertFalse(fits['is_fitting_hours'])

    def __check_entry(self, username, start, end):
        response = self.c.get(
            '/timeline/%s/check_entry/%s/%s/' % (username, start, end)
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content.decode('utf-8'))
        return result
