import unittest,json
from unittest.mock import patch
import delivery_capability as d
import test_host_receipt as receipts
import observability as audit,sql_tools as s

class DeliveryRouteTests(unittest.TestCase):
 def test_s01_is_host_bound_and_keeps_original_tokens(self):
  with patch.object(d.ap,'active',return_value={'kind':'s01','day':'2025-10-13'}):
   name,args=d.route({'body':'正文','execution_token':'execution','generation_token':'generation'})
   self.assertEqual(name,'deliver_message');self.assertEqual(args,{'body':'正文','day':'2025-10-13','execution_token':'execution','generation_token':'generation'})
   for bad in [{'asset_id':'external'},{'candidate_ids':['other']},{'event_id':'foreign'},{'recipient':'someone'}]:
    with self.assertRaises(ValueError):d.route({'body':'正文',**bad})
 def test_passive_can_only_address_current_event(self):
  with patch.object(d.ap,'active',return_value=None),patch.object(s,'read',return_value=[{'id':'bound-event'}]),patch.object(d.business,'token',side_effect=lambda x:x):
   name,args=d.route({'body':'正文','highlight_ids':['h'],'generation_token':'g'})
   self.assertEqual(name,'reply_to_message');self.assertEqual(args['event_id'],'bound-event');self.assertEqual(args['highlight_ids'],['h'])
   for bad in [{'asset_id':'x'},{'candidate_ids':['c']},{'execution_token':'x'}]:
    with self.assertRaises(ValueError):d.route({'body':'正文',**bad})
  with patch.object(d.ap,'active',return_value=None),patch.object(s,'read',return_value=[{'id':'a'},{'id':'b'}]):
   with self.assertRaises(ValueError):d.route({'body':'正文'})
 def test_internal_task_and_wrong_media_are_blocked(self):
  with patch.object(d.ap,'active',return_value={'kind':'plan'}):
   with self.assertRaisesRegex(ValueError,'internal_task'):d.route({'body':'正文'})
  task={'kind':'action','skill':'celebrate-lesson-completion','candidate_id':'c'}
  with patch.object(d.ap,'active',return_value=task),patch.object(d.business,'token',side_effect=lambda x:x):
   name,args=d.route({'body':'正文','execution_token':'e'});self.assertEqual(name,'deliver_proactive');self.assertEqual(args['candidate_ids'],['c'])
   with self.assertRaises(ValueError):d.route({'body':'正文','asset_id':'other-service-asset'})
 def test_festival_still_uses_guarded_image_delivery(self):
  task={'kind':'festival','skill':'send-festival-wishes','candidate_id':'c'}
  with patch.object(d.ap,'active',return_value=task),patch.object(d.business,'token',side_effect=lambda x:x):
   name,args=d.route({'body':'祝福','asset_id':'image','execution_token':'e','generation_token':'g'})
   self.assertEqual(name,'send_image');self.assertEqual(args['candidate_id'],'c');self.assertEqual(args['asset_id'],'image')
   with self.assertRaises(ValueError):d.route({'body':'祝福'})

class UnifiedReceiptTests(receipts.HostReceiptTests):
 def deliver(self):
  name,args=d.route({'body':'侨侨，来读今天的课吧。'})
  return audit.invoke('send_message',{'body':args['body']},lambda:audit.invoke(name,args,lambda:s.deliver(args['day'],args['body']),origin='adapter'))

if __name__=='__main__':unittest.main()
