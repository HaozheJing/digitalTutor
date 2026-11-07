import unittest
from reply_lab_native import s07_delivery_pending

class S08ReceiptWaitTests(unittest.TestCase):
    def snapshot(self, delivery, task):
        return {'deliveries':[{'status':delivery}], 'tasks':[{'skill':'close-week-reading-gap','status':task}]}

    def test_accepted_is_not_final(self):
        self.assertTrue(s07_delivery_pending(self.snapshot('accepted','awaiting_delivery')))
        self.assertTrue(s07_delivery_pending(self.snapshot('sent','awaiting_delivery')))
        self.assertFalse(s07_delivery_pending(self.snapshot('sent','done')))

    def test_failures_and_no_contact_are_not_retried(self):
        for status in ['unknown','failed','partial']:
            self.assertFalse(s07_delivery_pending(self.snapshot(status,'blocked')))
        self.assertFalse(s07_delivery_pending({'tasks':[{'skill':'close-week-reading-gap','status':'no_contact'}]}))

if __name__=='__main__':unittest.main()
