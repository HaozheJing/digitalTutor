"""Terminal byte decoding and rejection before any business effects."""
import io,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout,redirect_stderr
sys.path.append(str(Path(__file__).resolve().parents[1]/'scripts'))
from demo_passive import terminal_input,main
from manual_demo_runtime import ManualDemo,InputEncodingError

class TerminalEncodingTests(unittest.TestCase):
    def read(self,raw,encoding='utf-8'):
        with patch('sys.stdin',SimpleNamespace(buffer=io.BytesIO(raw))),redirect_stdout(io.StringIO()):
            return terminal_input('Parent> ',encoding)
    def test_utf8_chinese_roundtrip(self):self.assertEqual(self.read('老师你好啊\n'.encode()),'老师你好啊')
    def test_explicit_gbk_roundtrip(self):self.assertEqual(self.read('老师你好啊\r\n'.encode('gb18030'),'gb18030'),'老师你好啊')
    def test_wrong_encoding_not_guessed(self):
        with self.assertRaises(InputEncodingError):self.read('老师你好啊\n'.encode('gb18030'))
    def test_invalid_byte_not_replaced(self):
        with self.assertRaises(InputEncodingError):self.read(b'\xff\n')
    def test_eof(self):
        with self.assertRaises(EOFError):self.read(b'')
    def test_surrogate_rejected_before_accessing_storage(self):
        # This object deliberately has no storage/app: validation must happen first.
        with self.assertRaises(InputEncodingError):ManualDemo.turn(object(),'bad\udcff')
    def test_invalid_interactive_line_keeps_session_and_accepts_next(self):
        accepted=[]
        class Demo:
            def __init__(self,*a,**kw):pass
            def close(self):pass
            def turn(self,message=None):
                accepted.append(message)
                return {'status':'idle','model_runs':0,'channel_replies':[],'unanswered':[], 'waiting':[],'failures':[],'evidence':'test-only','exit_code':0}
        lines=iter(['bad\udcff','老师你好啊','/quit'])
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()) as err:
            code=main(['--run','test'],demo_factory=Demo,input_fn=lambda _:next(lines),is_interactive=True)
        self.assertEqual(accepted,[None,'老师你好啊']);self.assertEqual(code,1)
        self.assertIn('INPUT_ENCODING_INVALID',err.getvalue());self.assertNotIn('bad',err.getvalue())

if __name__=='__main__':unittest.main()
