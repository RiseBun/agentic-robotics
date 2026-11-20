import unittest

from agentic_cap.feedback import feedback
from agentic_cap.interpreter import BoundaryError, Interpreter


class ProgramDiagnosticTests(unittest.TestCase):
    def test_unbound_name_diagnostic_reports_available_locals_before_any_action(self):
        actions = []
        with self.assertRaises(BoundaryError) as caught:
            Interpreter({'move':lambda p:actions.append(p)},lambda:None).run(
                'x=1\ny=2\nz=3\nmove([x,y,nz])')
        diagnostic = feedback(caught.exception)['evidence']
        self.assertEqual(diagnostic['kind'],'unbound_name')
        self.assertEqual(diagnostic['name'],'nz')
        self.assertEqual(diagnostic['line'],4)
        self.assertEqual(diagnostic['available_locals'],['x','y','z'])
        self.assertNotIn('replacement',diagnostic)
        self.assertEqual(actions,[])

    def test_state_dictionary_index_error_has_fields_line_and_no_values(self):
        actions = []
        interpreter = Interpreter(dict(get_execution_state=lambda: dict(tcp_xyz=[1, 2, 3],
            tcp_wxyz=[1, 0, 0, 0], evidence_ref='do-not-copy-values'),
            close_gripper=lambda: actions.append('close')), lambda: None)
        with self.assertRaises(BoundaryError) as caught:
            interpreter.run('state=get_execution_state()\np=state[0]\nclose_gripper()')
        report = feedback(caught.exception)
        self.assertEqual(report['category'], 'invalid_program')
        self.assertEqual(report['evidence']['line'], 2)
        self.assertEqual(report['evidence']['container_type'], 'dict')
        self.assertEqual(report['evidence']['requested_key'], 0)
        self.assertIn('tcp_xyz', report['evidence']['available_fields'])
        self.assertNotIn('do-not-copy-values', str(report))
        self.assertEqual(actions, [])
        scope = interpreter.run('state=get_execution_state()\np=state["tcp_xyz"]\nq=state["tcp_wxyz"]')
        self.assertEqual(scope['p'], [1, 2, 3])

    def test_sequence_bounds_and_wrong_key_type_are_program_errors(self):
        for code in ['p=[1,2,3]\nx=p[3]', 'p=[1,2,3]\nx=p["tcp_xyz"]', 'p=3\nx=p[0]']:
            with self.subTest(code=code), self.assertRaises(BoundaryError) as caught:
                Interpreter({}, lambda: None).run(code)
            self.assertEqual(feedback(caught.exception)['category'], 'invalid_program')
            self.assertEqual(caught.exception.evidence['line'], 2)

    def test_dictionary_numeric_keys_remain_valid(self):
        scope = Interpreter({}, lambda: None).run('state={0:12}\nx=state[0]')
        self.assertEqual(scope['x'], 12)

    def test_api_key_error_is_not_relabeled_as_generated_index_error(self):
        def broken():
            raise KeyError('sensor_internal_key')
        with self.assertRaises(KeyError) as caught:
            Interpreter({'get_execution_state': broken}, lambda: None).run('p=get_execution_state()[0]')
        self.assertEqual(feedback(caught.exception, api='get_execution_state')['category'], 'api_exception')

    def test_invalid_unhashable_key_has_bounded_diagnostic(self):
        with self.assertRaises(BoundaryError) as caught:
            Interpreter({}, lambda: None).run('p={"tcp_xyz":[0,0,0]}\nx=p[[0]]')
        self.assertEqual(caught.exception.evidence['requested_key'], 'list')
        self.assertLess(len(str(caught.exception)), 500)


if __name__ == '__main__':
    unittest.main()
