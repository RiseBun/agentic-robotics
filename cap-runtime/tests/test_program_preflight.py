import tempfile
import unittest

from agentic_cap.interpreter import Interpreter, BoundaryError
from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend
from test_runtime import until


class ProgramPreflightTests(unittest.TestCase):
    def interpreter(self):
        calls = []
        return Interpreter({'move': lambda *a, **k: calls.append((a,k)),
                            'observe': lambda: {'visible': True}}, lambda: None), calls

    def test_late_undefined_variable_rejects_entire_segment_before_any_motion(self):
        i, calls = self.interpreter()
        with self.assertRaisesRegex(BoundaryError, "Unbound local name 'nz'.*line 3"):
            i.run('move([0,0,1])\nz=0.1\nmove([0,0,nz])')
        self.assertEqual(calls, [])
        self.assertEqual(i.scope, {})

    def test_unknown_function_or_api_overwrite_is_rejected_before_prior_actions(self):
        for source in ['move(1)\nmissing_tool()', 'move(1)\nmove=2', 'move(1)\nfor move in [1]:\n    pass']:
            i, calls = self.interpreter()
            with self.assertRaises(BoundaryError): i.run(source)
            self.assertEqual(calls, [])

    def test_branch_local_names_cannot_escape_without_an_assignment_on_each_path(self):
        i, calls = self.interpreter()
        with self.assertRaisesRegex(BoundaryError, 'Unbound local name'):
            i.run('move(0)\ns=observe()\nif s["visible"]:\n    p=[1,2,3]\nmove(p)')
        self.assertEqual(calls, [])
        i.run('s=observe()\nif s["visible"]:\n    p=[1,2,3]\nelse:\n    p=[3,2,1]\nmove(p)')
        self.assertEqual(len(calls), 1)

    def test_loop_local_after_possible_empty_iteration_is_rejected(self):
        i, calls = self.interpreter()
        with self.assertRaises(BoundaryError):
            i.run('move(0)\nvalues=[]\nfor x in values:\n    p=x\nmove(p)')
        self.assertEqual(calls, [])
        i.run('for x in range(2):\n    p=x\nmove(p)')
        self.assertEqual(calls, [((1,), {})])

    def test_mutable_vectors_unpack_and_nested_builtin_calls_still_work(self):
        i, calls = self.interpreter()
        result = i.run('p,q=([1,2,3],[0,0,1,0])\np[2]=p[2]+0.1\n'
                       's=observe()\nif s["visible"]:\n    move(p,q,height=max([0.1,0.2]))')
        self.assertAlmostEqual(result['p'][2], 3.1)
        self.assertEqual(calls[0][1], {'height': .2})

    def test_index_assignment_and_expansion_need_valid_bound_inputs(self):
        for source in ['move(0)\np[0]=1', 'move(0)\np=[1]\np[bad]=0', 'move(0)\nd={**{}}']:
            i, calls = self.interpreter()
            with self.assertRaises(BoundaryError): i.run(source)
            self.assertEqual(calls, [])

    def test_validate_does_not_call_tools_or_claim_physical_safety(self):
        i, calls = self.interpreter()
        i.validate('s=observe()\nmove([999,999,999])')
        self.assertEqual(calls, [])
        # Values/geometric feasibility remain Runtime responsibilities.
        self.assertEqual(i.scope, {})

    def test_real_episode_rejects_typo_without_charging_an_api_or_executing_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(directory, FixtureBackend)
            state = manager.create('preflight', 'cube_lifting', 1, {'holding':'red cube'})
            e = manager.episodes[state['id']]
            try:
                until(lambda: e.status=='ready' and e.snapshot)
                sid = e.execute('bad-code', e.revision, 'typo', 'wait_motion(100)\np=nz')
                until(lambda: e.active is None)
                self.assertEqual(e.segments[sid]['execution'], 'failed')
                self.assertEqual(e.segments[sid]['feedback']['category'], 'invalid_program')
                self.assertEqual(e.used['calls'], 0)
                self.assertEqual(e.used['motion_steps'], 0)
                self.assertFalse(any(x['kind']=='api_started' for x in e.events))
                self.assertIn('line 2', e.segments[sid]['error'])
            finally: e.close()


if __name__ == '__main__': unittest.main()
