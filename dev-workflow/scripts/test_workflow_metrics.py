import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import workflow_metrics as metrics


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config('codex')

    def config(self, kind):
        (self.root/'runtime.json').write_text(json.dumps({'worker': {'harness': {'kind': kind}}}),encoding='utf-8')

    def log(self, name, events, record=None):
        path = self.root/'attempts'/name/'1'
        path.mkdir(parents=True)
        (path/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
        if record is not None:
            (path/'process.json').write_text(json.dumps(record),encoding='utf-8')
        return path

    def test_cached_inputs_are_not_double_counted_and_money_is_unknown(self):
        self.log('A',[{'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':12}}],
                 {'reason':'exited','duration_seconds':4,'exit_code':0})
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_totals_by_format']['codex']['input_tokens'],100)
        self.assertEqual(result['usage_totals_by_format']['codex']['cached_input_tokens'],80)
        self.assertIsNone(result['money']['actual_invoice_total'])
        self.assertIsNone(result['money']['known_provider_reported_sum'])

    def test_interrupted_process_missing_usage_and_duration_is_not_zero_cost(self):
        self.log('A',[{'type':'turn.started'}])
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_coverage']['unknown_model_processes'],1)
        self.assertEqual(result['duration_unknown_processes'],1)
        self.assertIsNone(result['processes'][0]['duration_seconds'])
        self.assertIsNone(result['money']['actual_invoice_total'])

    def test_claude_reported_cost_and_cache_fields_keep_their_format(self):
        self.config('claude')
        self.log('A',[{'type':'result','total_cost_usd':0.25,'usage':{'input_tokens':10,'cache_read_input_tokens':60,
                     'cache_creation_input_tokens':20,'output_tokens':4}}])
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_totals_by_format']['claude']['input_tokens'],10)
        self.assertEqual(result['usage_totals_by_format']['claude']['cache_read_input_tokens'],60)
        self.assertEqual(result['money']['known_provider_reported_sum'],0.25)
        self.assertFalse(result['money']['actual_invoice_available'])

    def test_simulated_adapter_is_not_a_real_model_cost_or_usage(self):
        self.config('command')
        self.log('A',[{'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':1}}])
        result=metrics.collect(self.root)
        self.assertEqual(result['configured_model_process_count'],0)
        self.assertEqual(result['simulated_or_unknown_process_count'],1)
        self.assertEqual(result['usage_totals_by_format'],{})

    def test_invalid_bool_negative_and_incomplete_values_do_not_inflate_usage(self):
        self.log('A',[{'type':'turn.completed','usage':{'input_tokens':True,'output_tokens':1}},
                     {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':-1}},
                     {'type':'result','total_cost_usd':True},
                     {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}}])
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_totals_by_format']['codex']['input_tokens'],10)
        self.assertIsNone(result['money']['known_provider_reported_sum'])
        self.assertEqual(result['processes'][0]['invalid_events'],2)

    def test_two_usage_events_are_aggregated_once_each_without_raw_content(self):
        p=self.log('A',[{'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':2}},
                     {'type':'turn.completed','usage':{'input_tokens':30,'output_tokens':3}},
                     {'type':'item.completed','item':{'text':'PRIVATE MESSAGE BODY'}}])
        with (p/'events.jsonl').open('a',encoding='utf-8') as stream:stream.write('\n{partial')
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_totals_by_format']['codex']['input_tokens'],50)
        self.assertEqual(result['usage_totals_by_format']['codex']['usage_event_count'],2)
        self.assertNotIn('PRIVATE MESSAGE BODY',json.dumps(result))

    def test_human_records_are_read_only_and_host_interventions_are_not_invented(self):
        db=sqlite3.connect(self.root/'coordination.sqlite3')
        db.execute('CREATE TABLE human_responses(id TEXT,status TEXT)')
        db.execute("INSERT INTO human_responses VALUES('r1','consumed')")
        db.commit();db.close()
        before=(self.root/'coordination.sqlite3').read_bytes()
        result=metrics.collect(self.root)
        self.assertEqual(result['human_response_records'],1)
        self.assertIsNone(result['host_rescue_actions'])
        self.assertEqual((self.root/'coordination.sqlite3').read_bytes(),before)

    def test_truncated_final_record_preserves_usage_with_unknown_duration(self):
        p=self.log('A',[{'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':1}}])
        (p/'process.json').write_text('{partial',encoding='utf-8')
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_totals_by_format']['codex']['input_tokens'],10)
        self.assertTrue(result['processes'][0]['record_parse_error'])
        self.assertIsNone(result['processes'][0]['duration_seconds'])

    def test_large_invalid_numbers_do_not_crash_or_invent_cost(self):
        self.log('A',[{'type':'turn.completed','usage':{'input_tokens':10**400,'output_tokens':1}},
                     {'type':'result','total_cost_usd':10**400}])
        result=metrics.collect(self.root)
        self.assertEqual(result['usage_totals_by_format'],{})
        self.assertIsNone(result['money']['known_provider_reported_sum'])


if __name__ == '__main__':unittest.main()
