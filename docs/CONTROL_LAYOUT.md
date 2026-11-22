# iOS 按键布局

最后更新：2026-11-22。状态：编辑器与控制层已移植并在模拟器验证；真机游玩验证未做。

## 为什么移植 Pojav/Amethyst

原来的自研编辑器只有拖动、双指缩放和弹窗改键，而且与游戏内那套十三键布局格式不一致。
现在 Launcher 和游戏共用一份 Pojav v8 布局（`Documents/controlmap/<name>.json`），编辑器直接是
Amethyst-iOS 的 `CustomControlsViewController`，因此支持：

- 点按按键打开属性面板：名称、宽高、绑定（最多 4 个键码）、点按切换、鼠标穿透、滑动触发、
  背景/边框颜色、边框宽度、圆角、不透明度、显示位置。
- 顶部工具栏提供完成、保存、添加和更多操作；点按控件可编辑或删除，拖动移动，右下角手柄缩放。
- 安全区可选择无、默认或自定义；自定义可拖动画布和右下角手柄，确认后游戏控件也使用该范围。
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
- 旧 NCL 的 `controls` 数组在读取时转换为 Pojav 格式；打开布局不会改写原文件，保存时才写入转换结果。
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

### 输入链路排查（2026-11-22）

设备日志显示 Menu 触发 `FAOTMetalView showGameMenu` 未识别的 selector 并终止。游戏输入桥原先从 UIKit 主线程读取 GLFW 的线程局部上下文；游戏上下文属于渲染线程，导致输入入队失败。现在由游戏视图提供窗口和入队函数，Menu 发送 Escape，捕获状态同步给摇杆，窗口销毁时清除输入目标。模拟器冒烟覆盖按键与 Menu 的按下、释放以及编辑器操作；游戏真机和摇杆实际触摸仍需验证。

### 拖动松手排查（2026-11-22）

编辑器在拖动结束时调用的撤销分类源文件未包含在两个 Xcode 目标中，也影响增删和编辑操作。现已将该源文件加入两个目标。使用 WorldSimulator 工程编译的对象文件组成独立冒烟宿主，验证了移动、撤销和重做；正式模拟器游戏包因缺少 `client-graph.dylib` 未完成打包，实际手势与真机验证仍待执行。
