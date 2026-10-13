"""Observed SDK control-event replay; no real model and no acceptance bypass."""
from pathlib import Path
import json,sys,tempfile,unittest
import workflow_runtime as runtime

RETRY={'type':'error','message':'Reconnecting... 2/5 (stream disconnected before completion: tls handshake eof)'}
COMPLETE={'type':'turn.completed','usage':{'input_tokens':7,'output_tokens':3}}

class StreamRecoveryTests(unittest.TestCase):
    def replay(self,events):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();script=root/'events.py'
            script.write_text('import json\nfor e in '+repr(events)+': print(json.dumps(e),flush=True)\n',encoding='utf-8')
            config={'format_version':1,'harness':{'kind':'command','executable':sys.executable,'args':['-B',str(script)]},
                'limits':{'attempt_seconds':5,'no_progress_seconds':3,'max_output_bytes':100000}}
            return runtime.run_worker(config,root,root,'replay',root/'output',lambda:'unchanged',5,root/'cancel')

    def test_successful_sdk_completion_resolves_only_the_observed_retry_event(self):
        result=self.replay([RETRY,COMPLETE])
        self.assertEqual(result['exit_code'],0);self.assertFalse(result['harness_error'])
        self.assertEqual(result['recovered_stream_retry_events'],1)

    def test_unfinished_or_malformed_completion_is_still_failure(self):
        for events in ([RETRY],[RETRY,{'type':'turn.completed','usage':{'input_tokens':False,'output_tokens':0}}]):
            with self.subTest(events=events):self.assertTrue(self.replay(events)['harness_error'])

    def test_terminal_authentication_and_other_errors_are_never_cleared_by_completion(self):
        for event in ({'type':'turn.failed','error':{'message':'terminal failure'}},
                      {'type':'error','message':'authentication_error'},
                      {'type':'error','message':'a different failure'}):
            with self.subTest(event=event):
                self.assertTrue(self.replay([RETRY,event,COMPLETE])['harness_error'])

if __name__=='__main__':unittest.main()
