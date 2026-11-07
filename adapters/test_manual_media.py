import unittest,tempfile,hashlib,json
from pathlib import Path
from unittest.mock import patch,MagicMock
import manual_media as m,sql_tools as s

class ManualMediaTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'config').mkdir();self.public=self.root/'public'
  self.raw=b'%PDF-1.4 test';self.asset={'path':'assets/weekly-review/original.pdf','sha256':hashlib.sha256(self.raw).hexdigest(),'size':len(self.raw)}
  (self.root/'config/festival-media.json').write_text(json.dumps({'public_base_url':m.BASE,'public_directory':str(self.public)}))
  self.response=MagicMock();self.response.__enter__.return_value=self.response;self.response.geturl.return_value=m.url(self.asset);self.response.read.return_value=self.raw
  for p in [patch.object(s,'R',self.root),patch.object(m,'ROOT',self.public),patch('manual_workbench.verify',return_value={'enabled':True}),patch.object(m,'urlopen',return_value=self.response)]:p.start();self.addCleanup(p.stop)
 def test_registered_file_publication_is_immutable_and_byte_checked(self):
  self.assertEqual(m.publish(self.asset,self.raw),m.url(self.asset));self.assertEqual(m.publish(self.asset,self.raw),m.url(self.asset));self.assertEqual(len(list(self.public.iterdir())),1)
 def test_changed_bytes_origin_and_redirect_fail(self):
  with self.assertRaises(ValueError):m.publish(self.asset,self.raw+b'x')
  self.response.geturl.return_value='https://other.invalid/a'
  with self.assertRaises(ValueError):m.publish(self.asset,self.raw)
  (self.root/'config/festival-media.json').write_text('{}')
  with self.assertRaises(ValueError):m.publish(self.asset,self.raw)
 def test_unregistered_extensions_and_unbound_transport_fail(self):
  with self.assertRaises(ValueError):m.url({**self.asset,'path':'evil.html'})
  with patch('manual_workbench.verify',return_value=None),self.assertRaises(ValueError):m.publish(self.asset,self.raw)
 def test_remote_bytes_must_match_and_no_symlinks(self):
  self.response.read.return_value=b'wrong'
  with self.assertRaises(ValueError):m.publish(self.asset,self.raw)
  dest=self.public/m.url(self.asset).split('/')[-1];dest.unlink();dest.symlink_to(self.root/'other')
  with self.assertRaises(ValueError):m.publish(self.asset,self.raw)
 def test_manual_saturday_does_not_send_without_operator_selection(self):
  import weekly_materials as w
  with patch('test_mode.state',return_value={'run_id':'r','auto_highlights':False}),patch.object(w,'queue') as queue:
   self.assertEqual(w.tick()['status'],'manual');queue.assert_not_called()
 def test_manual_s10_selection_invokes_host_publication(self):
  import weekly_materials as w
  with patch('test_mode.state',return_value={'run_id':'r','auto_highlights':False,'manual_service_task_id':'t'}),patch.object(s,'read',return_value=[{'found':1}]),patch.object(w,'queue',return_value={'status':'queued'}) as queue,patch('observability.invoke',side_effect=lambda n,a,fn,**kw:fn()):
   self.assertEqual(w.tick()['status'],'queued');queue.assert_called_once()
if __name__=='__main__':unittest.main()
