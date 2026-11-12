import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import test_context as context

class ContextResetTests(unittest.TestCase):
    def test_isolated_database_native_sessions_and_late_messages(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(context,'STATE',Path(directory)):
            root=Path(directory)
            db=sqlite3.connect(root/'inbox.sqlite');db.execute('CREATE TABLE callbacks(id INTEGER)');db.execute('INSERT INTO callbacks VALUES(3)');db.commit();db.close()
            (root/'business.sqlite').write_bytes(b'old business and memory retained')
            (root/'runs').mkdir();(root/'runs/session').write_text('old native context')
            req=context.request(root/'inbox.sqlite','initial')
            self.assertEqual(context.current()['id'],'initial')
            self.assertEqual(context.request(root/'inbox.sqlite','initial'),req)
            self.assertTrue(context.apply())
            new=context.current();self.assertNotEqual(new['path'],str(root))
            self.assertFalse((Path(new['path'])/'business.sqlite').exists())
            self.assertFalse((Path(new['path'])/'runs').exists())
            self.assertEqual((root/'business.sqlite').read_bytes(),b'old business and memory retained')
            self.assertTrue(context.prior_message(new,3,{}))
            self.assertTrue(context.prior_message(new,4,{'timestamp':0}))
            self.assertFalse(context.prior_message(new,4,{'timestamp':9999999999999}))
            self.assertFalse(context.apply())
            with self.assertRaises(ValueError):context.request(root/'inbox.sqlite','initial')
            second=context.request(root/'inbox.sqlite',new['id']);context.apply()
            self.assertNotEqual(context.current()['path'],new['path'])
            self.assertTrue((root/'context-archives'/ (new['id']+'.json')).exists())

if __name__=='__main__':unittest.main()
