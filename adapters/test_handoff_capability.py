import unittest
from unittest.mock import patch
from types import SimpleNamespace
import handoff_capability as h,capability_surface as cap

class HandoffBindingTests(unittest.TestCase):
    def test_current_event_is_bound_without_model_id(self):
        with patch.object(h.ap,'active',return_value=None),patch.object(h.s,'read',return_value=[{'id':'bound-event'}]):
            self.assertEqual(h.route({'reason':'核实资料'}),{'reason':'核实资料','event_id':'bound-event'})
            for key in ['event_id','task_id','recipient','run_id']:
                with self.assertRaises(ValueError):h.route({'reason':'核实资料',key:'foreign'})

    def test_current_task_is_bound_without_reading_other_events(self):
        with patch.object(h.ap,'active',return_value={'id':'current-task'}),patch.object(h.s,'read',side_effect=AssertionError('not an event')):
            self.assertEqual(h.route({'reason':'核实资料'})['task_id'],'current-task')

    def test_idle_and_ambiguous_event_ownership_fail_closed(self):
        for events in [[],[{'id':'a'},{'id':'b'}]]:
            with patch.object(h.ap,'active',return_value=None),patch.object(h.s,'read',return_value=events):
                with self.assertRaises(ValueError):h.route({'reason':'核实资料'})

    def test_installed_surface_has_no_target_fields_and_preserves_domain_handler(self):
        schema=lambda p=None,r=():{'type':'object','properties':p or {},'required':list(r),'additionalProperties':False}
        seen=[]
        def original(name,args):seen.append((name,args));return {'status':'human_confirmation_required'}
        server=SimpleNamespace(schema=schema,base_mcp=SimpleNamespace(TOOLS=[('request_human_confirmation','old',schema({'event_id':{}}))]),business_call=original,agent_plan=SimpleNamespace(ServiceValidationError=ValueError))
        with patch.object(cap,'enabled',return_value=True),patch('delivery_capability.enabled',return_value=False),patch.object(h.ap,'active',return_value=None),patch.object(h.s,'read',return_value=[{'id':'bound-event'}]):
            cap.install(server)
            fields=next(x[2]['properties'] for x in server.base_mcp.TOOLS if x[0]=='request_human_confirmation')
            self.assertFalse({'task_id','event_id','recipient'} & fields.keys())
            self.assertEqual(server.business_call('request_human_confirmation',{'reason':'核实资料'})['status'],'human_confirmation_required')
        self.assertEqual(seen,[('request_human_confirmation',{'reason':'核实资料','event_id':'bound-event'})])

if __name__=='__main__':unittest.main()
