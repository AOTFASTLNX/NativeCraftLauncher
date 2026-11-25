"""Regression checks for the ported iOS control layout and editor.

These are source-level contracts. Interactive behavior was checked in the iOS Simulator
(editor, settings list and the button property sheet); physical-device touch input is not
covered here.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "ios/C0Harness/Control"
BRIDGE_M = (CONTROL / "FAOTControlBridge.m").read_text(encoding="utf-8")
BRIDGE_H = (CONTROL / "FAOTControlBridge.h").read_text(encoding="utf-8")
UTILS = (CONTROL / "CustomControlsUtils.m").read_text(encoding="utf-8")
VIEW_M = (CONTROL / "FAOTControlsView.m").read_text(encoding="utf-8")
COMPAT = (CONTROL / "FAOTControlCompat.m").read_text(encoding="utf-8")
SURFACE = (ROOT / "ios/glfw-lite/uikit_surface.m").read_text(encoding="utf-8")
PAGES = (ROOT / "ios/C0Harness/FAOTLauncherPages.m").read_text(encoding="utf-8")
EDITOR = (ROOT / "ios/C0Harness/FAOTControlEditorViewController.m").read_text(encoding="utf-8")


class ControlLayoutStorageTests(unittest.TestCase):
    def test_layouts_use_the_shared_controlmap_directory(self):
        self.assertIn('stringByAppendingPathComponent:@"controlmap"', BRIDGE_M)
        # The editor loads through the bridge so a fresh install also gets the shipped default.
        self.assertIn("FAOTControlLoadLayout(", (CONTROL / "ControlLayout.m").read_text(encoding="utf-8"))

    def test_layout_names_cannot_escape_the_directory(self):
        self.assertIn("![name isEqualToString:name.lastPathComponent]", BRIDGE_M)
        self.assertIn('[name isEqualToString:@".."]', BRIDGE_M)

    def test_default_layout_is_written_before_first_use(self):
        self.assertIn("FAOTControlEnsureDefaults", BRIDGE_H)
        self.assertIn("FAOTControlEnsureDefaults();", (ROOT / "ios/C0Harness/FAOTLauncherViewController.m").read_text(encoding="utf-8"))

    def test_default_layout_is_pojav_v8(self):
        self.assertIn('dict[@"version"] = @(8)', UTILS)
        for key in ('mControlDataList', 'mDrawerDataList', 'mJoystickDataList'):
            self.assertIn(key, UTILS)

    def test_default_layout_does_not_overlap_top_row_buttons(self):
        # Evaluate the dynamic expressions on a 844x390 landscape safe area and assert that
        # no two top-row buttons overlap. Amethyst's shipped layout failed exactly this.
        margin = 2.0
        screen_w, screen_h = 844.0, 390.0
        boxes = []
        rows = []
        for line in UTILS.splitlines():
            if not line.startswith('        {"') or '$' + '{top} + $' + '{margin}' not in line:
                continue
            fields = re.findall(r'"([^"]*)"|(\b\d+(?:\.\d+)?\b)', line)
            strings = [f[0] for f in fields if f[0]]
            numbers = [f[1] for f in fields if f[1]]
            # strings: name, dx, dy ; numbers: width, height
            name, dx = strings[0], strings[1]
            width = float(numbers[0])
            x = eval(dx.replace('$' + '{', '').replace('}', ''), {'__builtins__': {}},
                     {'left': 0.0, 'top': 0.0, 'right': screen_w - width, 'bottom': screen_h - width,
                      'width': width, 'height': 30.0, 'margin': margin})
            boxes.append((name, x, x + width))
        self.assertGreaterEqual(len(boxes), 4)
        boxes.sort(key=lambda item: item[1])
        for left, right in zip(boxes, boxes[1:]):
            self.assertLessEqual(left[2], right[1], (left, right))


class ControlInputTests(unittest.TestCase):
    def test_input_target_is_supplied_by_surface(self):
        self.assertIn('[controls setInputWindow:self.handle enqueue:(void *)faot_glfw_enqueue]', SURFACE)
        self.assertNotIn('glfwGetCurrentContext', BRIDGE_M)

    def test_input_without_a_game_surface_is_a_no_op(self):
        self.assertIn("if (!window || !inputEnqueue) return NO;", BRIDGE_M)

    def test_held_keys_are_released_once(self):
        self.assertIn("[self.heldKeys removeAllObjects]", VIEW_M)
        self.assertIn("- (void)releaseAllInput", VIEW_M)

    def test_special_buttons_map_to_mouse_and_scroll(self):
        for token in ('SPECIALBTN_MOUSEPRI', 'SPECIALBTN_MOUSESEC', 'SPECIALBTN_SCROLLUP'):
            self.assertIn(token, VIEW_M)

    def test_game_surface_uses_the_shared_control_view(self):
        self.assertIn('NSClassFromString(@"FAOTControlsView")', SURFACE)
        self.assertIn("[controls loadLayoutNamed:name error:&error]", SURFACE)
        self.assertNotIn("FAOTBuiltinControlModels", SURFACE)


class SafeAreaTests(unittest.TestCase):
    """The game HUD lives along the top and bottom edges; controls must keep clear of it."""

    def test_default_safe_area_reserves_the_top_and_bottom_strips(self):
        body = COMPAT.split("UIEdgeInsets getDefaultSafeArea(void)")[1].split("CGRect getSafeArea")[0]
        self.assertIn("FAOTControlVerticalSafeArea()", body)
        self.assertIn("safeArea.top = MAX(0, safeArea.top) + clearance;", body)
        self.assertIn("safeArea.bottom = MAX(0, safeArea.bottom) + clearance;", body)
        # The old code zeroed the vertical insets outright.
        self.assertNotIn("safeArea.top = safeArea.bottom = 0;", body)

    def test_landscape_insets_are_not_rotated_away(self):
        body = COMPAT.split("UIEdgeInsets getDefaultSafeArea(void)")[1].split("CGRect getSafeArea")[0]
        # Only a portrait window needs its vertical insets moved onto the landscape edges.
        self.assertIn("window.bounds.size.width < window.bounds.size.height", body)
        self.assertNotIn("UIScreen.mainScreen.bounds.size", body)
        self.assertIn("CGFloat top = MAX(0, safeArea.top);", body)

    def test_custom_safe_area_cannot_cover_the_hud(self):
        body = COMPAT.split("CGRect getSafeArea(CGRect screenBounds)")[1].split("void setSafeArea")[0]
        self.assertIn("safeArea.top = MAX(safeArea.top, clearance);", body)
        self.assertIn("safeArea.bottom = MAX(safeArea.bottom, clearance);", body)

    def test_children_outside_the_canvas_cannot_take_touches(self):
        # The reference layout's bottom row extrapolates a few points below its canvas, so the
        # hit test has to reject touches outside the safe rectangle while playing.
        layout = (CONTROL / "ControlLayout.m").read_text(encoding="utf-8")
        self.assertIn("CGRectContainsPoint(self.bounds, point)", layout)

    def test_editor_and_game_share_the_same_default(self):
        editor = (CONTROL / "CustomControlsViewController.m").read_text(encoding="utf-8")
        self.assertIn("FAOTControlFullBleedRect(self.view.bounds)", editor)
        self.assertNotIn("self.ctrlView.frame = self.view.bounds;", editor)
        self.assertIn("FAOTControlVerticalSafeArea()", PAGES)

    def test_vertical_clearance_is_configurable(self):
        for token in ("FAOTControlSetVerticalSafeArea", "FAOTControlHasCustomSafeArea",
                      "FAOTControlResetSafeArea"):
            self.assertIn(token, (CONTROL / "FAOTControlCompat.h").read_text(encoding="utf-8"))
        self.assertIn("verticalSafeAreaChanged:", PAGES)
        self.assertIn("resetSafeAreaTapped", PAGES)

    def test_smoke_host_asserts_the_strip(self):
        smoke = (ROOT / "tests/launcher_smoke_main.m").read_text(encoding="utf-8")
        self.assertIn("CONTROL_VERTICAL_SAFE_AREA_PASS", smoke)
        self.assertIn("FAOTControlFullBleedRect(editor.view.bounds)", smoke)


class ControlEditorTests(unittest.TestCase):
    def test_undo_implementation_is_in_both_xcode_targets(self):
        for project_path in ("ios/C0Harness/C0Harness.xcodeproj/project.pbxproj",
                             "ios/NCLDemo/NCLDemo.xcodeproj/project.pbxproj"):
            project = (ROOT / project_path).read_text(encoding="utf-8")
            for prefix in ("E", "F"):
                build_id = prefix + "000000000000000000000E"
                self.assertRegex(project, rf"{build_id} /\* CustomControlsViewController\+UndoManager.m in Sources \*/ = ")
                self.assertRegex(project, rf"{build_id} /\* CustomControlsViewController\+UndoManager.m in Sources \*/,")

    def test_editor_is_the_ported_amethyst_controller(self):
        self.assertIn("@interface FAOTControlEditorViewController : CustomControlsViewController",
                      (ROOT / "ios/C0Harness/FAOTLauncherViewController.h").read_text(encoding="utf-8"))
        self.assertIn("getDefaultCtrl", EDITOR)

    def test_key_picker_is_searchable_not_four_wheels(self):
        editor = (CONTROL / "CustomControlsViewController.m").read_text(encoding="utf-8")
        self.assertNotIn("numberOfComponentsInPickerView", editor)
        self.assertIn("promptForMappingSearch", editor)
        self.assertIn("applyModifier", editor)

    def test_settings_page_manages_layouts(self):
        for token in ("FAOTControlMapEntries()", "FAOTControlDuplicateLayout", "FAOTControlDeleteLayout",
                      "pageRequestedLayoutEditorNamed:"):
            self.assertIn(token, PAGES)

    def test_chinese_strings_cover_the_editor(self):
        for key in ('custom_controls.hint', 'custom_controls.button_edit.mapping',
                    'custom_controls.control_menu.add_joystick'):
            self.assertIn(key, COMPAT)

    def test_gpl_notice_ships_with_the_control_layer(self):
        notice = (CONTROL / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("GPL-3.0", notice)
        self.assertIn("Amethyst-iOS", notice)
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((CONTROL / "LICENSES/Amethyst-iOS-GPL-3.0.txt").is_file())


if __name__ == "__main__":
    unittest.main()
