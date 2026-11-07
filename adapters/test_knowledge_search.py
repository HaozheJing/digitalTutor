import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import knowledge_search as k
import service_content


class KnowledgeSearchTests(unittest.TestCase):
    def setUp(self):
        self.doc = k.catalog()['documents'][0]
        self.uri = self.doc['uri']

    def test_search_reads_only_pinned_cloud_body_with_status(self):
        import hashlib
        body='来自知识库的正文'
        doc={**self.doc,'sha256':hashlib.sha256(body.encode()).hexdigest()}
        data=k.catalog();data['documents']=[doc]
        with patch.object(k,'catalog',return_value=data),patch.object(k,'cli',side_effect=[
            {'resources':[{'uri':self.uri,'score':.5,'abstract':'摘要不是答案'}]},body]) as cli:
            result=k.search('周末怎么补课')
        self.assertEqual(result['matches'][0]['body'],body)
        self.assertEqual(result['matches'][0]['business_status'],'unverified')
        self.assertFalse(result['matches'][0]['can_answer_directly'])
        self.assertTrue(result['matches'][0]['live_read'])
        self.assertEqual(cli.call_args_list[1].args[0],['read',self.uri])
        self.assertEqual(result['approved_business_answers'],0)

    def test_changed_body_is_not_returned_as_answer(self):
        with patch.object(k,'cli',return_value='wrong revision'):
            result=k.read_document(self.doc['id'])
        self.assertEqual(result['status'],'changed')
        self.assertNotIn('body',result)

    def test_outside_root_and_sidecar_hits_cannot_be_read(self):
        hits=[{'uri':'viking://user/private/content.md'},
              {'uri':self.uri.replace('content.md','.overview.md')},
              {'uri':self.uri.replace('8a790143','old')}]
        with patch.object(k,'cli',return_value={'resources':hits}) as cli:
            result=k.search('补课')
        self.assertEqual(result['status'],'no_match')
        self.assertEqual(cli.call_count,1)

    def test_duplicate_root_and_file_hits_read_once(self):
        with patch.object(k,'cli',return_value={'resources':[
            {'uri':self.uri.rsplit('/',1)[0]},{'uri':self.uri}]}),patch.object(k,'read_document',return_value={'status':'ok'}) as read:
            result=k.search('补课')
        self.assertEqual(len(result['matches']),1)
        self.assertEqual(read.call_count,1)

    def test_no_match_and_failure_do_not_fall_back_to_skill(self):
        with patch.object(k,'cli',side_effect=RuntimeError('secret token')):
            result=k.search('退款')
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('secret',str(result))
        with self.assertRaisesRegex(ValueError,'knowledge_search'):
            service_content.reference('answer-family-question','references/faq-source.md')

    def test_bad_query_scope_and_limits_do_not_call_cli(self):
        for args in [('',),('a'*501,),('q','all'),('q','faq',True),('q','faq',6)]:
            with patch.object(k,'cli') as cli,self.assertRaises(ValueError):
                k.search(*args)
            cli.assert_not_called()

    def test_cli_ignores_banner_but_rejects_errors_and_redacts_stderr(self):
        output='cmd: ov read ...\n'+json.dumps({'ok':True,'result':'body'})+'\n'
        with patch.object(k.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=output)):
            self.assertEqual(k.cli(['read',self.uri]),'body')
        for response in [SimpleNamespace(returncode=1,stdout='',stderr='password=secret'),
                         SimpleNamespace(returncode=0,stdout='{"ok":false,"error":"secret"}'),
                         SimpleNamespace(returncode=0,stdout='invalid')]:
            with patch.object(k.subprocess,'run',return_value=response):
                with self.assertRaisesRegex(RuntimeError,'service unavailable') as error:
                    k.cli(['read',self.uri])
                self.assertNotIn('secret',str(error.exception))

    def test_cli_timeout_is_controlled(self):
        with patch.object(k.subprocess,'run',side_effect=subprocess.TimeoutExpired('ov',45)):
            with self.assertRaises(RuntimeError):
                k.cli(['read',self.uri])

    def test_product_search_does_not_surface_historical_reference(self):
        item=next(x for x in k.catalog()['documents'] if x['retrieval_scope']=='reference')
        with patch.object(k,'cli',return_value={'resources':[{'uri':item['uri']}]}):
            self.assertEqual(k.search('原价','product')['status'],'no_match')


if __name__=='__main__':
    unittest.main()
