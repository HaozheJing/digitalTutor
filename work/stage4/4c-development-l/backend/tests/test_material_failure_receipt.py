"""Native material failures keep diagnostics and model/trace receipts aligned."""
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from digital_tutor.application import Application
from digital_tutor.hermes_tools import register_tools
from digital_tutor.storage import digest
from digital_tutor.tools import ToolRegistry


class MaterialFailureReceiptTests(unittest.TestCase):
    def make_bridge(self, strict=True):
        app=Application(':memory:',strict_runtime=strict,mode='passive')
        self.addCleanup(app.close)
        app.create_family('synthetic-A','synthetic-uid-A','mock:A')
        token=app.open_session('synthetic-A','service')
        business=ToolRegistry(app,token);registered={};trace=[];callbacks=[]
        def register(**spec):registered[spec['name']]=spec['handler']
        def observed(value):callbacks.append(json.loads(json.dumps(value)))
        registry=SimpleNamespace(registry=SimpleNamespace(register=register))
        with patch.dict(sys.modules,{'tools.registry':registry}):
            register_tools(business,trace,on_tool=observed)
        return app,business,registered,trace,callbacks

    def invoke(self, bridge, name, arguments):
        app,business,registered,trace,callbacks=bridge
        output=json.loads(registered['dt_'+name.replace('.','_')](arguments))
        self.assertEqual(callbacks[-1],trace[-1])
        self.assertEqual(trace[-1]['ok'],output['ok'])
        self.assertEqual(trace[-1]['error'],output.get('error',{}).get('code'))
        self.assertEqual(trace[-1]['result_digest'],digest(output))
        self.assertEqual(trace[-1]['arguments_digest'],digest(arguments))
        if app.strict_runtime:self.assertEqual(trace[-1]['result'],output)
        else:
            self.assertNotIn('result',trace[-1]);self.assertNotIn('arguments',trace[-1])
        return output

    def assert_material_failure(self, output, code):
        self.assertIs(output['ok'],False)
        self.assertEqual(output['error']['code'],code)
        self.assertIsInstance(output['error']['message'],str)
        self.assertTrue(output['error']['message'])
        self.assertIsInstance(output.get('source_boundary'),str)
        self.assertTrue(output['source_boundary'].strip())
        self.assertNotIn('result',output)

    def test_real_host_faults_preserve_diagnostic_and_all_receipt_surfaces(self):
        bridge=self.make_bridge();app,business,*_=bridge
        for name,args,fault,code in [
            ('knowledge.read',{'document_id':'synthetic-document'},'knowledge_unavailable','KNOWLEDGE_UNAVAILABLE'),
            ('skills.read',{'skill_id':'synthetic-skill'},'required_guide_unavailable','SKILL_MISSING'),
        ]:
            with self.subTest(name=name):
                app.test_hooks={'before:'+name:{'fault':fault}}
                with patch.object(business,'call',wraps=business.call) as called:
                    output=self.invoke(bridge,name,args)
                    called.assert_not_called()  # The real pre-call hook raised BusinessError.
                self.assert_material_failure(output,code)

    def test_real_registry_without_material_loader_returns_failure_not_success(self):
        bridge=self.make_bridge()
        for name,args,code in [
            ('knowledge.read',{'document_id':'synthetic-document'},'KNOWLEDGE_UNAVAILABLE'),
            ('skills.read',{'skill_id':'synthetic-skill'},'SKILL_RUNTIME_UNAVAILABLE'),
        ]:
            with self.subTest(name=name):
                original=bridge[1].call(name,args)
                output=self.invoke(bridge,name,args)
                self.assert_material_failure(output,code)
                self.assertEqual(output['error'],original['error'])
                self.assertEqual({k:v for k,v in output.items() if k!='source_boundary'},original)

    def test_nonmaterial_failure_is_not_decorated(self):
        bridge=self.make_bridge()
        original=bridge[1].call('state.read',{'unexpected':True})
        output=self.invoke(bridge,'state.read',{'unexpected':True})
        self.assertFalse(output['ok']);self.assertEqual(output,original)
        self.assertNotIn('source_boundary',output)

    def test_material_list_failure_is_not_read_failure_decoration(self):
        bridge=self.make_bridge()
        for name in ('knowledge.list','skills.list'):
            with self.subTest(name=name):
                original=bridge[1].call(name,{})
                output=self.invoke(bridge,name,{})
                self.assertFalse(output['ok']);self.assertEqual(output,original)
                self.assertNotIn('source_boundary',output)

    def test_legacy_material_failure_preserves_payload_and_digest_only_trace(self):
        bridge=self.make_bridge(strict=False)
        for name,args in [('knowledge.read',{'document_id':'synthetic-document'}),
                          ('skills.read',{'skill_id':'synthetic-skill'})]:
            with self.subTest(name=name):
                original=bridge[1].call(name,args)
                output=self.invoke(bridge,name,args)
                self.assertFalse(output['ok']);self.assertEqual(output,original)
                self.assertNotIn('source_boundary',output)

    def test_successful_material_receipt_keeps_body_and_existing_boundary(self):
        bridge=self.make_bridge();app=bridge[0]
        # Only the external loader is stubbed. Dispatch, native bridge and auditing
        # remain real; no failure receipt is supplied by the test.
        app.knowledge=SimpleNamespace(dispatch=lambda *args:{'content':'synthetic-source-body','complete_body':True})
        app.knowledge_bindings={}
        args={'document_id':'synthetic-document'}
        original=bridge[1].call('knowledge.read',args)
        output=self.invoke(bridge,'knowledge.read',args)
        self.assertTrue(output['ok']);self.assertEqual(output,original)
        self.assertEqual(output['result']['content'],'synthetic-source-body')
        self.assertNotIn('source_boundary',output)


if __name__=='__main__':unittest.main()
