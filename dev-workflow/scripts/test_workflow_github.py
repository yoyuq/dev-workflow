"""Provider read/write reconciliation and CI binding, no credentials or network."""
import unittest
from unittest.mock import Mock
from workflow_github import GitHub,Blocked

class GitHubTests(unittest.TestCase):
    def api(self, values):
        api=object.__new__(GitHub);api.repository='example/test';api.remaining=lambda:100
        api.request=Mock(side_effect=values);return api
    def test_unknown_merge_outcome_reconciles_exact_head(self):
        api=self.api([{'merged':False},Blocked('connection ended'),{'merged':True,'head':{'sha':'approved'},'merge_commit_sha':'merged'}])
        self.assertEqual(api.merge(2,'approved','Feature'),'merged')
        self.assertEqual([c.args[1] for c in api.request.call_args_list if len(c.args)>1],['PUT'])
    def test_other_head_does_not_resolve_unknown_write(self):
        api=self.api([{'merged':False},Blocked('connection ended'),{'merged':True,'head':{'sha':'other'},'merge_commit_sha':'merged'}])
        with self.assertRaises(Blocked):api.merge(2,'approved','Feature')
    def test_same_named_failed_check_cannot_be_hidden_by_other_app_success(self):
        api=self.api([{'statuses':[]},{'check_runs':[{'name':'build','status':'completed','conclusion':'failure','app':{'id':1}},
            {'name':'build','status':'completed','conclusion':'success','app':{'id':2}}]}])
        self.assertEqual(api.ci('head',['build'])['state'],'failed')
    def test_required_app_success_must_match(self):
        api=self.api([{'statuses':[]},{'check_runs':[{'name':'build','status':'completed','conclusion':'success','app':{'id':2}}]}])
        api.required_apps={'build':{1}}
        result=api.ci('head',['build']);self.assertEqual(result['state'],'pending');self.assertEqual(result['missing'],['build'])
    def test_inaccessible_protection_cannot_be_assumed_absent(self):
        api=self.api([None,{'protected':True}])
        with self.assertRaises(Blocked):api.required_checks('workflow/run/integration')
    def test_truncated_ci_fails_closed(self):
        api=self.api([{'total_count':101},{'check_runs':[]}])
        with self.assertRaises(Blocked):api.ci('head',[])

if __name__=='__main__':unittest.main()
