"""Source-level contract checks for the iOS render-scale setting.

The actual slider and framebuffer behavior require an iOS build/device. These
checks keep the persisted setting, launch handoff, and GL41 validation wired
together in source.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_H = (ROOT / "ios/C0Harness/FAOTAccountStore.h").read_text(encoding="utf-8")
ACCOUNT_M = (ROOT / "ios/C0Harness/FAOTAccountStore.m").read_text(encoding="utf-8")
PAGES = (ROOT / "ios/C0Harness/FAOTLauncherPages.m").read_text(encoding="utf-8")
MAIN = (ROOT / "ios/C0Harness/main.m").read_text(encoding="utf-8")
GLFW = (ROOT / "ios/glfw-lite/glfw_lite.c").read_text(encoding="utf-8")


class RenderResolutionSettingTests(unittest.TestCase):
    def test_setting_is_persisted_and_quantized_to_tenths(self):
        self.assertIn("double renderScale", ACCOUNT_H)
        self.assertIn('FAOTRenderScaleKey = @"FAOTRenderScale"', ACCOUNT_M)
        self.assertIn("doubleForKey:FAOTRenderScaleKey", ACCOUNT_M)
        self.assertIn("setDouble:value forKey:FAOTRenderScaleKey", ACCOUNT_M)
        self.assertIn("round(value * 10.0) / 10.0", ACCOUNT_M)
        self.assertIn("value >= 0.1 && value <= 1.0", ACCOUNT_M)

    def test_settings_page_exposes_the_requested_range(self):
        self.assertIn("UISlider *renderScaleSlider", PAGES)
        self.assertIn("renderScale.minimumValue = 0.1f", PAGES)
        self.assertIn("renderScale.maximumValue = 1.0f", PAGES)
        self.assertIn("renderScaleChanged:", PAGES)
        self.assertIn("round((double)slider.value * 10.0) / 10.0", PAGES)

    def test_launch_passes_saved_scale_to_gl41(self):
        self.assertIn('setenv("FAOT_GL41_SCALE", renderScale.UTF8String, 1)', MAIN)
        self.assertIn('getenv("FAOT_GL41_SCALE")', GLFW)

    def test_gl41_accepts_point_one_and_rejects_out_of_range_values(self):
        self.assertIn("value >= 0.1 && value <= 1.0", GLFW)
        self.assertIn("value = 0.8", GLFW)


if __name__ == "__main__":
    unittest.main()
