"""Alternate profiles must satisfy their declared environment/version contract."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
import workflow_runtime as runtime

class AlternatePreflight(unittest.TestCase):
 def case(self,version=False):
  with tempfile.TemporaryDirectory() as name:
   root=Path(name).resolve();primary=root/'primary.py';backup=root/'backup.py';marker=root/'backup-called'
   primary.write_text("import json\nprint(json.dumps({'type':'result','is_error':True,'errors':['insufficient_quota']}),flush=True)\nraise SystemExit(75)")
   backup.write_text("from pathlib import Path\nPath('backup-called').touch()\n")
   h={'kind':'command','executable':sys.executable,'args':['-B',str(primary)]}
   alternate={**h,'args':['-B',str(backup)]}
   cfg={'format_version':1,'provider_id':'one','harness':h,'quota_recovery':{'enabled':True},'limits':{'attempt_seconds':10,'no_progress_seconds':5,'max_output_bytes':10000},'fallbacks':[{'id':'two','harness':alternate,'preflight_checks':[{'id':'required-environment','argv':[sys.executable,'-c','raise SystemExit(1)'],'timeout_seconds':2}]}]}
   if version:
    cfg['fallbacks'][0].pop('preflight_checks');alternate['expected_version']='not-this-interpreter'
   result=runtime.run_worker(cfg,root,root,'fixed original task',root/'out',lambda:'',10,root/'cancel')
   self.assertFalse(marker.exists(),'Backup model must not launch after a failed preflight')
   self.assertEqual(result['reason'],'preflight_failed')
   self.assertEqual(result['environment_failure'],'preflight')
 def test_extra_fallback_probe_is_required(self):self.case()
 def test_fallback_expected_version_is_required(self):self.case(version=True)

if __name__=='__main__':unittest.main()
