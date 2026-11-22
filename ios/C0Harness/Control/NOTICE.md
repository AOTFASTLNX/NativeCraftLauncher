# Control layer provenance

ios/C0Harness/Control/ is adapted from the Pojav/Amethyst iOS launcher control system.
The app that links it is distributed under GPL-3.0 (see the repository LICENSE).

| File | Upstream | Commit | License |
|---|---|---|---|
| ControlButton.h/.m, ControlLayout.h/.m, ControlDrawer.h/.m, ControlSubButton.h/.m, ControlJoystick.h/.m, CustomControlsUtils.h/.m, CustomControlsViewController.h/.m, CustomControlsViewController+UndoManager.m | [AngelAuraMC/Amethyst-iOS](https://github.com/AngelAuraMC/Amethyst-iOS) Natives/customcontrols/ and Natives/CustomControlsViewController* | main, 2026-10-17 | GPL-3.0 |
| NSPredicateUtilitiesExternal.h/.m | same repository, Natives/customcontrols/ | main, 2026-10-17 | GPL-3.0 |
| FileListViewController.h/.m | same repository, Natives/ | main, 2026-10-17 | GPL-3.0 |
| DBNumberedSlider.h/.m | [khanhduytran0/DBNumberedSlider](https://github.com/khanhduytran0/DBNumberedSlider) | 4eddc68b5f92247f8afc0f6114d1b92b338749f4 | Apache-2.0 |
| glfw_keycodes.h | [glfw/glfw](https://github.com/glfw/glfw) include/GLFW/glfw3.h | GLFW 3.4 | zlib |

Amethyst-iOS is itself derived from PojavLauncher_iOS, also GPL-3.0. The full GPL-3.0 text is
in LICENSES/Amethyst-iOS-GPL-3.0.txt; DBNumberedSlider's notice is in
LICENSES/DBNumberedSlider-APACHE-2.0.txt.

## What changed for NCL

- Amethyst's launcher helpers (LauncherPreferences, utils, ios_uikit_bridge, ControllerInput)
  were replaced by FAOTControlCompat.*, which reads NCL settings, safe area, dialogs and logging.
- Layout files live in Documents/controlmap, the same location NCL's launcher and game read.
- Input is sent through FAOTControlBridge, which looks up the running GLFW surface at runtime
  instead of linking the standalone GLFW dylib.
- The four-wheel key picker in the button editor was replaced with a searchable key list; the
  property sheet is now larger and scrolls.
- The default layout is NCL's own, because Amethyst's shipped layout overlapped its top row and
  placed Pause under another button.
- Chinese strings are supplied by FAOTControlCompat.m; Amethyst's .strings files are not copied.
