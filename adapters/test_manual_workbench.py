import unittest,tempfile,json,sqlite3,hashlib
from pathlib import Path
from unittest.mock import patch
import sql_tools as s,manual_workbench as manual,tool_surface,clock_execution

class ManualScopeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'config').mkdir();(self.root/'runtime').mkdir()
  for p in [patch.object(s,'R',self.root),patch.object(s,'DB',self.root/'runtime/live.sqlite')]:p.start();self.addCleanup(p.stop)
  self.route={'orgId':'org','imBotId':'bot','chatId':'chat','imContactId':'contact'}
  self.provider={'allowed_routes':[self.route],'expected_org_id':'org','api_base_url':'https://test.invalid'}
  self.write('provider.json',self.provider)
  self.settings={'provider_config':str(self.root/'config/provider.json'),'route':self.route}
  self.write('test-wechat.json',self.settings)
  self.manifest={'version':1,'mode':'allowlisted_manual_test','workspace':str(self.root),'family_uid':'00001','route_sha256':hashlib.sha256(json.dumps(self.route,sort_keys=True).encode()).hexdigest(),'api_base_url':'https://test.invalid'}
  self.write('manual-workbench.json',self.manifest)
  with sqlite3.connect(s.DB) as c:c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)');c.execute('INSERT INTO metadata VALUES(?,?)',('test_mode',json.dumps({'uid':'00001','frozen':True,'auto_highlights':False})))
 def write(self,name,value):(self.root/'config'/name).write_text(json.dumps(value))
 def test_explicit_manual_scope_enables_latest_tools_without_simulated_transport(self):
  self.write('tool-surface.json',{'version':2,'identity':True,'host_steps':True})
  self.assertEqual(manual.verify()['delivery_mode'],'allowlisted_test_wechat');self.assertTrue(tool_surface.enabled('identity'))
  import experiment_runtime
  self.assertIsNone(experiment_runtime.manifest());self.assertIsNone(experiment_runtime.simulation_config())
  (self.root/'config/manual-workbench.json').unlink();self.assertIsNone(manual.verify());self.assertFalse(tool_surface.enabled('identity'))
 def test_wrong_root_family_route_or_provider_fails_closed(self):
  for key,value in [('workspace','/other'),('family_uid','other'),('route_sha256','changed'),('api_base_url','https://other.invalid')]:
   self.write('manual-workbench.json',{**self.manifest,key:value})
   with self.subTest(key=key),self.assertRaises(ValueError):manual.verify()
  self.write('manual-workbench.json',self.manifest);self.write('provider.json',{**self.provider,'allowed_routes':[]})
  with self.assertRaises(ValueError):manual.verify()
 def test_simulation_and_automatic_modes_are_rejected(self):
  self.write('test-wechat.json',{**self.settings,'simulation':True})
  with self.assertRaises(ValueError):manual.verify()
  self.write('test-wechat.json',self.settings);self.write('experiment.json',{})
  with self.assertRaises(ValueError):manual.verify()
  (self.root/'config/experiment.json').unlink()
  with sqlite3.connect(s.DB) as c:c.execute('UPDATE metadata SET value=?',(json.dumps({'uid':'00001','frozen':True,'auto_highlights':True}),))
  with self.assertRaises(ValueError):manual.verify()
 def test_ui_toggle_cannot_enable_automatic_delivery(self):
  import test_scheduler
  with self.assertRaisesRegex(ValueError,'手动'):test_scheduler.set_automatic(True)
 def test_clock_change_keeps_manual_mode(self):
  with patch('test_mode.activate',return_value={'auto_highlights':False}) as activate:
   result=clock_execution.apply_time('activate','2025-11-07T06:30:00+08:00')
   activate.assert_called_once_with('2025-11-07T06:30:00+08:00',False);self.assertEqual(result['execution'],'manual_only')
  with patch('test_mode.advance',return_value={'auto_highlights':False}),patch('console_control.idle'):
   self.assertFalse(clock_execution.apply_time('advance','2025-11-07T07:00:00+08:00')['auto_highlights'])

class AutomaticWeekScopeTests(ManualScopeTests):
 def configure_week(self):
  import datetime as dt
  now=dt.datetime.now(dt.timezone.utc)
  self.manifest.update(version=2,mode='allowlisted_automatic_test',run_id='weekly-run',starts_at='2025-10-13T06:30:00+08:00',ends_at='2025-10-19T23:59:00+08:00',authorized_at=(now-dt.timedelta(minutes=1)).isoformat(),authorization_expires_at=(now+dt.timedelta(hours=12)).isoformat(),authorization='user_requested_real_wechat_week_test')
  self.write('manual-workbench.json',self.manifest)
  with sqlite3.connect(s.DB) as c:c.execute('UPDATE metadata SET value=?',(json.dumps({'uid':'00001','frozen':True,'auto_highlights':True,'run_id':'weekly-run','as_of':'2025-10-13T06:30:00+08:00'}),))
 def test_explicit_week_allows_only_bound_clock_advance(self):
  self.configure_week();self.assertFalse(manual.verify()['manual_only']);self.assertTrue(manual.clock_authority('advance','2025-10-19T23:59:00+08:00'))
  for action,at in [('activate','2025-10-13T06:30:00+08:00'),('advance','2025-10-20T06:30:00+08:00')]:
   with self.subTest(action=action),self.assertRaises(ValueError):manual.clock_authority(action,at)
 def test_expired_wrong_run_or_unbounded_week_cannot_send(self):
  self.configure_week()
  for key,value in [('run_id','different'),('ends_at','2025-11-09T23:59:00+08:00'),('authorization',''),('authorization_expires_at','2026-01-01T00:00:00+00:00')]:
   self.write('manual-workbench.json',{**self.manifest,key:value})
   with self.subTest(key=key),self.assertRaises(ValueError):manual.verify()
 def test_clock_scope_failure_precedes_database_mutation(self):
  self.configure_week()
  with patch('test_mode.advance') as advance, self.assertRaises(ValueError):clock_execution.apply_time('advance','2025-10-20T06:30:00+08:00')
  advance.assert_not_called()

if __name__=='__main__':unittest.main()
