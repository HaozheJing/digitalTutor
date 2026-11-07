import copy,unittest
import agent_view

class ProfileProjectionTests(unittest.TestCase):
    def test_known_enrollment_relationship_survives_nested_projection(self):
        for role in ('mother','father'):
            raw={'facts':{'learning_context':{'family':{'child_nickname':'测试昵称','guardian_role':role,'guardian_role_basis':'copied_from_selected_enrollment'}}}}
            original=copy.deepcopy(raw);projected=agent_view.project(raw);family=projected['facts']['learning_context']['family']
            self.assertEqual(family,{'child_nickname':'测试昵称','guardian_role':role})
            self.assertEqual(raw,original)

    def test_unknown_and_absent_relationship_stay_unknown_and_absent(self):
        self.assertEqual(agent_view.project({'guardian_role':'unknown','guardian_role_basis':'copied_from_selected_enrollment','sex':'male'}),{'guardian_role':'unknown','sex':'male'})
        self.assertEqual(agent_view.project({'child_nickname':'测试昵称','guardian_role_basis':'copied_from_selected_enrollment'}),{'child_nickname':'测试昵称'})

    def test_environment_metadata_still_removed(self):
        self.assertEqual(agent_view.project({'guardian_role':'mother','guardian_role_basis':'copied_from_selected_enrollment','nickname_basis':'user_requested_alias','simulation':True,'notice':'simulation','status':'mock_sent'}),{'guardian_role':'mother','status':'sent'})

if __name__=='__main__':unittest.main()
