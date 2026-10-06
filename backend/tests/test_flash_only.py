import unittest
from digital_tutor.domain import BusinessError
from digital_tutor.hermes_runtime import configured_model

class FlashOnlyTests(unittest.TestCase):
    def test_legacy_large_setting_never_selects_max(self):
        self.assertEqual(configured_model({'small_llm_name':'qwen3.8-flash','large_llm_name':'qwen3.8-max'}),'qwen3.8-flash')
    def test_max_in_small_slot_rejected_without_fallback(self):
        with self.assertRaises(BusinessError) as error:configured_model({'small_llm_name':'qwen3.8-max','large_llm_name':'qwen3.8-flash'})
        self.assertEqual(error.exception.code,'MODEL_NOT_ALLOWED')
    def test_missing_small_setting_rejected_without_fallback(self):
        with self.assertRaises(BusinessError) as error:configured_model({'large_llm_name':'qwen3.8-flash'})
        self.assertEqual(error.exception.code,'MODEL_NOT_ALLOWED')

if __name__=='__main__':unittest.main()
