import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from PIL import Image
from reply_lab import Fault, digest
import test_reply_lab as fixtures


class GeneratedPreviewTests(unittest.TestCase):
    setUp = fixtures.ReplyLabTests.setUp
    scenario = fixtures.ReplyLabTests.scenario
    config = fixtures.ReplyLabTests.config
    run_fixture = fixtures.ReplyLabTests.run_fixture

    def prepare(self):
        run = self.run_fixture()
        stream = io.BytesIO(); Image.new('RGB', (2, 2), 'red').save(stream, 'PNG')
        raw = stream.getvalue(); sha = hashlib.sha256(raw).hexdigest()
        exp = 'exp-'+'a'*32
        w = self.lab.home/'runs'/run['id']/'source/runtime/experiments'/exp/'workspace'
        for name in ['runtime','config','artifacts/media']:(w/name).mkdir(parents=True, exist_ok=True)
        self.path = w/'artifacts/media'/('generated.png'); self.path.write_bytes(raw)
        db = w/'runtime/live.sqlite'
        self.db = db
        (w/'config/experiment.json').write_text(json.dumps({'experiment_run_id':exp,'business_run_id':'business-one'}))
        url = 'https://experiment.invalid/festival-media/'+exp+'/'+sha+'.png'
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE festival_images(asset_id,run_id,path,sha256,public_url,inspection)')
            c.execute('INSERT INTO festival_images VALUES(?,?,?,?,?,?)',('asset','business-one',str(self.path),sha,url,'Checked actual image'))
        z = run['result']; z.update(native_verified=True, simulation=True, parts=[{'part_id':'image-part','message_type':6,'body':url,'status':'unknown','simulation':True}], assembly={'writable_paths':{'business_db':str(db)}})
        self.ident = run['id']; self.result = z; self.persist()
        return raw, sha

    def persist(self):
        self.result['content_digest'] = digest({k:v for k,v in self.result.items() if k!='content_digest'})
        with self.lab.connect() as c:c.execute('UPDATE lab_runs SET result=? WHERE id=?',(json.dumps(self.result),self.ident))

    def preview(self, **data):
        return self.lab.dispatch(self.actor,'image-preview',{'id':self.ident,'part_id':'image-part',**data})

    def test_existing_image_preview_preserves_unknown_receipt(self):
        raw,sha=self.prepare();value=self.preview()
        import base64
        self.assertEqual(base64.b64decode(value['file_base64']),raw)
        self.assertEqual(value['sha256'],sha);self.assertEqual(value['status'],'unknown')
        self.assertEqual(self.lab.run(self.actor,self.ident)['result']['parts'][0]['status'],'unknown')

    def test_preview_rejects_other_owner_and_unbound_part(self):
        self.prepare()
        with self.assertRaises(Fault):self.lab.dispatch(self.other,'image-preview',{'id':self.ident,'part_id':'image-part'})
        with self.assertRaises(Fault):self.preview(part_id='another-part')

    def test_preview_rejects_tampered_image_and_foreign_path(self):
        self.prepare();self.path.write_bytes(b'wrong')
        with self.assertRaisesRegex(Fault,'哈希'):self.preview()
        with sqlite3.connect(self.db) as c:c.execute('UPDATE festival_images SET path=?',(str(self.studio.root/'private.png'),))
        with self.assertRaisesRegex(Fault,'素材目录'):self.preview()

    def test_preview_rejects_foreign_workspace_and_missing_inspection(self):
        self.prepare()
        with sqlite3.connect(self.db) as c:c.execute('UPDATE festival_images SET inspection=NULL')
        with self.assertRaisesRegex(Fault,'验图'):self.preview()
        self.result['assembly']['writable_paths']['business_db']='/production/runtime/live.sqlite';self.persist()
        with self.assertRaisesRegex(Fault,'业务库'):self.preview()

    def test_preview_does_not_fetch_remote_urls(self):
        self.prepare();self.result['parts'][0]['body']='https://example.com/image.png';self.persist()
        with self.assertRaisesRegex(Fault,'来源'):self.preview()


if __name__=='__main__':unittest.main()

