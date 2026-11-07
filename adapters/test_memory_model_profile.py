import unittest
from pathlib import Path
import native_experiments as ex


class MemoryModelProfileTests(unittest.TestCase):
    def test_pro_budget_and_model_survive_native_profile_assembly(self):
        profile=ex.profile(Path('/isolated/workspace'),'persona','deepseek-v4-pro','https://example.invalid')
        plugins={item['id']:item for item in profile if 'id' in item}
        self.assertEqual(plugins['agent-default-model']['config']['model'],'deepseek-v4-pro')
        model=plugins['llm-pi-ai']['config']['providers']['experiment-provider']['models'][0]
        self.assertEqual(model['maxTokens'],32768)
        self.assertEqual(ex.model_output_limit('deepseek-v4.1-flash-economy'),8192)

    def test_explicit_budget_is_bounded_and_not_coerced(self):
        self.assertEqual(ex.model_output_limit('deepseek-v4-pro',16384),16384)
        for value in [True,'32768',0,100000]:
            with self.assertRaisesRegex(ValueError,'invalid_max_output_tokens'):
                ex.model_output_limit('deepseek-v4-pro',value)

    def test_pro_low_is_native_reasoning_not_a_model_substitution(self):
        profile=ex.profile(Path('/isolated'),'persona','deepseek-v4-pro','https://example.invalid',reasoning_effort='low')
        plugins={item['id']:item for item in profile if 'id' in item}
        self.assertEqual(plugins['agent-default-model']['config']['reasoningEffort'],'low')
        provider=plugins['llm-pi-ai']['config']['providers']['experiment-provider']
        self.assertEqual(provider['models'][0]['id'],'deepseek-v4-pro')
        self.assertEqual(provider['models'][0]['reasoningEfforts']['low'],'low')
        self.assertEqual(provider['compat']['thinkingFormat'],'deepseek')


if __name__=='__main__':unittest.main()
