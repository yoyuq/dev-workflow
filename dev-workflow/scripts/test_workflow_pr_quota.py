"""Actual isolated Git transactions with a simulated GitHub adapter, not live PRs."""
import sys,time,unittest
from pathlib import Path
import check_night_run as checks
import workflow_service as service
import workflow_coordinator as coord
import test_workflow_git_delivery as prtests
from test_workflow_review import REVIEWER

class PRQuotaTests(unittest.TestCase):
    setUp=prtests.FeaturePRTests.setUp
    tearDown=prtests.FeaturePRTests.tearDown
    initialize=prtests.FeaturePRTests.initialize
    state=prtests.FeaturePRTests.state
    def dispatch(self,runner):
        job=runner.claim()
        self.assertIsNotNone(job)
        runner.finish(job['id'],job['generation'],coord.run(self.root))
    def quota_case(self,backup=False):
        self.root=self.base/'runs'/'feature';self.root.parent.mkdir()
        ready=self.base/'provider-ready'
        program=self.base/'quota-reviewer.py'
        program.write_text("import json,sys\nfrom pathlib import Path\nif '--version' not in sys.argv and not Path(sys.argv[-1]).exists():\n print(json.dumps({'type':'result','is_error':True,'error':{'type':'insufficient_quota','retry_after':1}}),flush=True);raise SystemExit(75)\n"+REVIEWER)
        cfg=self.configs['reviewer']
        old=cfg['harness'].copy()
        cfg['harness']['args']=['-B',str(program),'approve',str(ready)]
        cfg['protected_files'].append(str(program))
        cfg.update(provider_id='review-main',quota_recovery={'enabled':True,'initial_seconds':1,'max_seconds':2})
        if backup:cfg['fallbacks']=[{'id':'review-spare','harness':old}]
        self.initialize();service.initialize(self.base/'service',self.base/'runs')
        runner=service.Service(self.base/'service');runner.submit({'id':'once','run_dir':str(self.root)})
        return ready,runner

    def test_published_pr_wait_resumes_without_duplicate_pr_or_merge(self):
        self.marker.write_text('disable planned fault')
        ready,runner=self.quota_case();pins=checks.digest(self.root/'pins.json')
        self.dispatch(runner);s=self.state()
        self.assertEqual(s['status'],'waiting_for_quota');self.assertEqual(len(self.api.prs),1)
        self.assertEqual(self.api.merges,0);first=s['tasks']['A']['commit'];pr=s['tasks']['A']['feature_prs'][0]['number']
        ready.touch();time.sleep(1.1);runner=service.Service(self.base/'service');runner.recover_jobs();self.dispatch(runner)
        final=self.state();self.assertEqual(final['status'],'completed')
        self.assertEqual(len(self.api.prs),3);self.assertEqual(self.api.merges,2)
        self.assertEqual(final['tasks']['A']['attempts'],1);self.assertEqual(final['tasks']['A']['commit'],first)
        self.assertEqual(final['tasks']['A']['feature_prs'][0]['number'],pr)
        self.assertEqual(checks.digest(self.root/'pins.json'),pins)

    def test_reviewer_backup_keeps_revert_repair_and_regression(self):
        _,runner=self.quota_case(backup=True);self.dispatch(runner);s=self.state()
        self.assertEqual(s['status'],'completed');self.assertEqual(self.api.merges,3)
        self.assertEqual([p['phase'] for p in s['remote_git']['prs']],['reverted','accepted','accepted'])
        self.assertEqual(len(s['remote_git']['rollbacks']),1)
        self.assertTrue(all(c['result']=='passed' for c in s['remote_git']['rollbacks'][0]['checks']))

if __name__=='__main__':
    import unittest
    unittest.main()
