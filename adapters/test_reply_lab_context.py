import unittest
import test_reply_lab as base
from reply_lab_context import dispatch, passive_fixture
from s07_service import weekly_learning


class ContextTests(unittest.TestCase):
    setUp = base.ReplyLabTests.setUp
    scenario = base.ReplyLabTests.scenario

    def test_frozen_bundle_can_be_edited_without_retired_references(self):
        folder = self.studio.skills / 'service-policy'
        (folder / 'references').mkdir(parents=True)
        (folder / 'SKILL.md').write_text('---\nname: service-policy\ndescription: test\n---\nUse current facts and respect requests. [old](references/supervision.md)\n')
        (folder / 'references/supervision.md').write_text('Retired reference, never load this content.')
        first = self.lab.freeze_skills('service-policy', {})
        self.assertNotIn('references/supervision.md', first['service-policy'])
        self.assertEqual(self.lab.freeze_skills('service-policy', first), first)
    def test_real_context_hides_reference_and_is_owner_scoped(self):
        row = self.scenario(origin='real_transformed', transformations=[], source_refs=[{'file_sha256':'a'*64,'sheet':'chat','visible_rows':[2]}])
        listed = dispatch(self.lab, self.actor, 'contexts', {})
        self.assertEqual([x['id'] for x in listed['items']], [row['id']])
        self.assertEqual(dispatch(self.lab, self.other, 'contexts', {})['items'], [])
        loaded = dispatch(self.lab, self.actor, 'context-load', {'id': row['id']})
        self.assertNotIn('reference', loaded['payload'])
        self.assertEqual(loaded['payload']['history'], row['payload']['history'])

    def test_unknown_learning_remains_unknown(self):
        s = self.scenario()['payload']
        f = passive_fixture(s)
        self.assertEqual(f['learning_daily'], [])
        self.assertEqual(f['enrollments'], [])
        summary = weekly_learning({'as_of': s['as_of'], 'enrollments': [], 'lessons_as_of': []}, f['course_calendar'])
        for key in ['current_week', 'previous_week']:
            self.assertFalse(summary[key]['coverage_complete'])
            self.assertIsNone(summary[key]['completed'])

    def test_fixture_not_in_real_context_selector(self):
        self.scenario()
        self.assertEqual(dispatch(self.lab, self.actor, 'contexts', {})['items'], [])


if __name__ == '__main__': unittest.main()
