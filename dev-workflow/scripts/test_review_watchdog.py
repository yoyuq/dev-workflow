import json
from pathlib import Path
import sys
import tempfile
import unittest
import workflow_runtime as runtime

SCRIPT='''import json,sys,time
mode=sys.argv[1]
for n in range(12):
    if mode=='thinking':event={'type':'item.completed','item':{'id':str(n),'type':'reasoning','text':'thinking'}}
    else:event={'type':'item.completed','item':{'id':'same' if mode=='duplicate' else str(n),'type':'command_execution','status':'completed','exit_code':0}}
    print(json.dumps(event),flush=True)
    time.sleep(.3)
'''

class ReviewWatchdogTests(unittest.TestCase):
    def test_successful_completion_is_required_not_thinking_or_error(self):
        self.assertEqual(runtime.completed_read_tools({'type':'item.completed','item':{'id':'a','type':'command_execution','status':'completed','exit_code':0}}),[('codex','a')])
        for kind,status,code in (('reasoning','completed',0),('command_execution','started',0),('command_execution','failed',1),('command_execution','completed',False)):
            self.assertEqual(runtime.completed_read_tools({'type':'item.completed','item':{'id':'a','type':kind,'status':status,'exit_code':code}}),[])
        self.assertEqual(runtime.completed_read_tools({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'a','is_error':False}]}}),[('claude','a')])
        self.assertEqual(runtime.completed_read_tools({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'a','is_error':True}]}}),[])

    def run_stream(self, mode, policy, seconds=10):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();script=root/'stream.py';script.write_text(SCRIPT,encoding='utf-8')
            config={'format_version':1,'harness':{'kind':'command','executable':sys.executable,'args':['-B',str(script),mode]},
                'limits':{'attempt_seconds':seconds,'no_progress_seconds':1,'max_output_bytes':100000},'watchdog_policy':policy}
            return runtime.run_worker(config,root,root,'fixture',root/'output',lambda:'unchanged',seconds,root/'cancel')

    def test_real_tool_work_survives_read_only_idle_without_an_artifact(self):
        result=self.run_stream('tools','read-tools')
        self.assertEqual(result['reason'],'exited');self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['successful_tool_observations'],12)
        self.assertIsNone(result['first_artifact_seconds'])

    def test_worker_artifact_policy_still_stops_read_only_tool_chatter(self):
        result=self.run_stream('tools','artifact')
        self.assertEqual(result['reason'],'no_progress');self.assertEqual(result['successful_tool_observations'],0)

    def test_thinking_and_duplicate_tool_ids_do_not_keep_review_alive(self):
        for mode in ('thinking','duplicate'):
            with self.subTest(mode=mode):
                result=self.run_stream(mode,'read-tools')
                self.assertEqual(result['reason'],'no_progress')
                self.assertLessEqual(result['successful_tool_observations'],1)

    def test_hard_attempt_budget_still_wins_over_tool_activity(self):
        result=self.run_stream('tools','read-tools',seconds=2)
        self.assertEqual(result['reason'],'time_limit');self.assertGreater(result['successful_tool_observations'],0)

if __name__=='__main__':unittest.main()
