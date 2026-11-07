import unittest
from unittest.mock import patch
import delivery_capability as d
class BodyGuardTests(unittest.TestCase):
 def test_authority_leak_is_rejected_before_routing(self):
  for body in ['正文 generation_token=internal-value','正文 EXECUTION_TOKEN : internal-value','正文 internal-value']:
   with patch.object(d.ap,'active',side_effect=AssertionError('must not route')):
    with self.assertRaisesRegex(d.ap.ServiceValidationError,'internal_authority_in_body'):
     d.route({'body':body,'generation_token':'internal-value'})
 def test_plain_body_keeps_authority_in_tool_arguments(self):
  with patch.object(d.ap,'active',return_value={'kind':'s01','day':'2025-10-13'}):
   name,args=d.route({'body':'正常回复','generation_token':'internal-value'})
  self.assertEqual(name,'deliver_message');self.assertEqual(args['body'],'正常回复');self.assertEqual(args['generation_token'],'internal-value')
