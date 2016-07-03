from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from mixer.backend.django import mixer

import lessons.models as lessons
import products.models as products
from crm.models import Customer
from elk.utils.reflection import find_ancestors
from hub.exceptions import CannotBeScheduled, CannotBeUnscheduled
from hub.models import ActiveSubscription, Class
from timeline.models import Entry as TimelineEntry


class BuySubscriptionTestCase(TestCase):
    fixtures = ('crm', 'lessons', 'products')
    TEST_PRODUCT_ID = 1
    TEST_CUSTOMER_ID = 1

    def test_buy_a_single_subscription(self):
        """
        When buing a subscription, all lessons in it should become beeing
        available to the customer
        """

        def _get_lessons_count(product):
            cnt = 0
            for lesson_type in product.LESSONS:
                cnt += getattr(product, lesson_type).all().count()
            return cnt

        product = products.Product1.objects.get(pk=self.TEST_PRODUCT_ID)

        s = ActiveSubscription(
            customer=Customer.objects.get(pk=self.TEST_CUSTOMER_ID),
            product=product,
            buy_price=150,
        )
        s.save()

        active_lessons_count = Class.objects.filter(subscription_id=s.pk).count()
        active_lessons_in_product_count = _get_lessons_count(product)

        self.assertEqual(active_lessons_count, active_lessons_in_product_count, 'When buying a subscription should add all of its available lessons')  # two lessons with natives and four with curators

    test_second_time = test_buy_a_single_subscription  # let's test for the second time :-)

    def test_disabling_subscription(self):
        product = products.Product1.objects.get(pk=self.TEST_PRODUCT_ID)

        s = ActiveSubscription(
            customer=Customer.objects.get(pk=self.TEST_CUSTOMER_ID),
            product=product,
            buy_price=150,
        )
        s.save()

        for lesson in s.classes.all():
            self.assertEqual(lesson.active, 1)

        # now, disable the subscription for any reason
        s.active = 0
        s.save()
        for lesson in s.classes.all():
            self.assertEqual(lesson.active, 0, 'Every lesson in subscription should become inactive now')


class BuySingleLessonTestCase(TestCase):
    fixtures = ('crm', 'lessons', 'products')

    TEST_CUSTOMER_ID = 1

    def test_single_lesson(self):
        """
        Let's but ten lessons at a time
        """
        for lesson_type in find_ancestors(lessons, lessons.Lesson):
            already_bought_lessons = []
            for i in range(0, 10):
                try:
                    s = Class(
                        customer=Customer.objects.get(pk=self.TEST_CUSTOMER_ID),
                        lesson=lesson_type.get_default()  # this should be defined in every lesson
                    )
                    s.save()
                    self.assertTrue(s.pk)
                    self.assertNotIn(s.pk, already_bought_lessons)
                    already_bought_lessons.append(s.pk)
                except NotImplementedError:
                    """
                    Some lessons, ex master classes cannot be bought such way
                    """
                    pass


class ScheduleTestCase(TestCase):
    fixtures = ('crm', 'lessons')
    TEST_CUSTOMER_ID = 1

    def setUp(self):
        self.event_host = mixer.blend(User, is_staff=1)

    def _buy_a_lesson(self, lesson):
        bought_class = Class(
            customer=Customer.objects.get(pk=self.TEST_CUSTOMER_ID),
            lesson=lesson
        )
        bought_class.save()
        return bought_class

    def test_unschedule_of_non_scheduled_lesson(self):
        bought_class = self._buy_a_lesson(products.OrdinaryLesson.get_default())
        with self.assertRaises(CannotBeUnscheduled):
            bought_class.unschedule()

    def test_schedule_simple(self):
        """
        Generic test to schedule and unschedule a class
        """

        entry = mixer.blend(TimelineEntry, slots=1)
        bought_class = self._buy_a_lesson(products.OrdinaryLesson.get_default())

        self.assertFalse(bought_class.is_scheduled)
        self.assertTrue(entry.is_free)

        bought_class.schedule(entry)  # schedule a class
        bought_class.save()

        self.assertTrue(bought_class.is_scheduled)
        self.assertFalse(entry.is_free)

        bought_class.unschedule()
        self.assertFalse(bought_class.is_scheduled)
        self.assertTrue(entry.is_free)

    def test_schedule_master_class(self):
        """
        Buy a master class and then schedule it
        """
        event = mixer.blend(lessons.Event,
                            lesson_type=ContentType.objects.get(app_label='lessons', model='MasterClass'),
                            slots=5,
                            host=self.event_host,
                            )

        lesson = mixer.blend(lessons.MasterClass)

        entry = mixer.blend(TimelineEntry,
                            event=event,
                            teacher=self.event_host,
                            )

        entry.save()

        bought_class = self._buy_a_lesson(lesson=lesson)
        bought_class.save()

        bought_class.schedule(entry)
        bought_class.save()

        self.assertTrue(bought_class.is_scheduled)
        self.assertEqual(entry.taken_slots, 1)

        bought_class.unschedule()
        self.assertEqual(entry.taken_slots, 0)

    def test_schedule_lesson_of_a_wrong_type(self):
        """
        Try to schedule bought master class lesson to a paired lesson event
        """
        event = mixer.blend(lessons.Event,
                            lesson_type=ContentType.objects.get(app_label='lessons', model='PairedLesson'),
                            slots=2,
                            host=self.event_host,
                            )

        paired_lesson_entry = mixer.blend(TimelineEntry, event=event, teacher=self.event_host)

        paired_lesson_entry.save()

        bought_class = self._buy_a_lesson(mixer.blend(lessons.MasterClass))
        bought_class.save()

        with self.assertRaises(CannotBeScheduled):
            bought_class.schedule(paired_lesson_entry)

        self.assertFalse(bought_class.is_scheduled)