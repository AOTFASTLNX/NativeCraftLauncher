# iOS 按键布局

最后更新：2026-11-22。状态：编辑器与控制层已移植并在模拟器验证；真机游玩验证未做。

## 为什么移植 Pojav/Amethyst

原来的自研编辑器只有拖动、双指缩放和弹窗改键，而且与游戏内那套十三键布局格式不一致。
现在 Launcher 和游戏共用一份 Pojav v8 布局（`Documents/controlmap/<name>.json`），编辑器直接是
Amethyst-iOS 的 `CustomControlsViewController`，因此支持：

- 点按按键打开属性面板：名称、宽高、绑定（最多 4 个键码）、点按切换、鼠标穿透、滑动触发、
  背景/边框颜色、边框宽度、圆角、不透明度、显示位置。
- 拖动移动，右下角手柄缩放，长按空白处打开菜单：完成、保存、载入、安全区、添加按键、
  添加抽屉、添加摇杆。
- 抽屉与摇杆：抽屉可设置方向（FREE/上下左右），摇杆支持 WASD 与向前锁定。
- 撤销/重做：由 `NSUndoManager` 支持，移动、缩放、编辑、增删都可撤销。
- 布局管理：设置页列出 `controlmap` 下的所有布局，点按切换，长按编辑/复制/删除。

## 文件与职责

| 路径 | 作用 |
|---|---|
| `ios/C0Harness/Control/` | 移植的 Pojav/Amethyst 控制层，见 `NOTICE.md` 的来源表 |
| `ios/C0Harness/Control/FAOTControlBridge.*` | 布局目录、读写、默认布局、运行时 GLFW 输入 |
| `ios/C0Harness/Control/FAOTControlCompat.*` | 替换 Amethyst 的偏好设置/安全区/对话框/本地化 |
| `ios/C0Harness/Control/FAOTControlsView.*` | 游戏内控件表面，把布局按键变成 GLFW 事件 |
| `ios/C0Harness/FAOTControlEditorViewController.m` | 把移植编辑器嵌入 NCL 启动器 |
| `ios/C0Harness/FAOTLauncherPages.m` | 设置页的布局列表与切换/复制/删除 |

## 默认布局

`FAOTDefaultControlLayoutDictionary()` 生成 NCL 自己的默认布局：左侧移动/摇杆、右侧
Break/Place/Items、顶栏 Chat/Keys/More/Menu/GUI/Pause。首次启动时由
`FAOTControlEnsureDefaults()` 写入 `controlmap/default.json`。

Pojav/Amethyst 自带的默认布局没有直接沿用：它的顶栏按钮相互重叠，Pause 又压在 Exit Menu
下面，在 844x390 的横屏安全区上不可用。

## 导入与兼容

- 支持 Pojav v1–v8 的 `mControlDataList` / `mDrawerDataList` / `mJoystickDataList`，导入时按
  版本做就地转换。
- 支持 Amethyst/Pojav 的 `dynamicX`/`dynamicY` 表达式（`${margin}`、`${width}`、`px()`、`dp()`）。
- 无法解析的布局会明确报错，不会静默丢弃按键。

## 已知限制

- 编辑器与游戏内控件只在 iOS Simulator 中做过截图验证；真机触摸、横竖屏切换与
  `GCMouse` 同时使用的行为未验证。
- 虚拟鼠标（`SPECIALBTN_VIRTUALMOUSE`）在 NCL 中尚未实现指针渲染，按下不会显示虚拟指针。
- 抽屉里按钮的排列依赖 Pojav 的 drawer 方向逻辑；FREE 模式下需要手动摆放。
- 该移植使整个 App 以 GPL-3.0 分发；仓库根新增 `LICENSE`。

## 验证方式

```bash
# 编译（模拟器，无需 client-graph.dylib 的语法检查）
xcrun -sdk iphonesimulator clang -fobjc-arc -target arm64-apple-ios17.0-simulator \
  -isysroot "$(xcrun --sdk iphonesimulator --show-sdk-path)" \
  -I ios/C0Harness -I ios/C0Harness/Control -I ios/glfw-lite \
  -fsyntax-only ios/C0Harness/Control/FAOTControlsView.m
```

冒烟宿主 `tests/launcher_smoke_main.m` 支持 `FAOT_SMOKE_MODE=editor|settings|controlpanel`，
可在模拟器里直接打开编辑器、设置页和按键属性面板。
