# Minecraft 1.21.1 适配状态

Last reviewed: 2026-11-22

最新测试包为 `MC1211-r7-20261122`：已构建、签名、覆盖安装并启动，用户手动确认已进入世界。设备日志显示解码 609 个区块、28 秒时渲染 tick 增至 2270，并持续处理实体包，未复现 r6 的聊天公钥等待。保存退出后重进、触摸与声音暂未测试；世界内持续游玩与存档仍未验收，`gateC0: BLOCKED`。

1.21.1 / Java 21 是与 26.1.2 / Java 25 并行的第二条构建线。本页只记录当前有效证据：原版与 Fabric 两条线都已生成签名 IPA，Fabric 最新为 `MC1211-r7-20261122`，覆盖 50 个模块，构建、签名、安装与容器启动都已通过，用户实测已进入主菜单。r2 越过了 Log4j 反射失败、停在 JMX；r3 用 iOS 运行时启动参数关闭 JMX 后越过了这一段，并在设备上建立了真实的 GL41/Zink 上下文（Mesa 25.0.7 over MoltenVK，`GL_VERSION` 4.1），随后在 `org.lwjgl.opengl.GL.<clinit>` 失败退出；r4 针对该入口做构建期定点改写，设备上已进入主菜单（`deviceRun: MAIN_MENU_REACHED_WORLD_CREATION_FAILED`），创建世界失败于 iOS AOT 没有 JFR；r5 在构建期选择 Minecraft 自带 `NoOpProfiler`，设备上点击进入世界后闪退在 Netty 反射（`deviceRun: WORLD_CONNECTION_FAILED_NETTY_REFLECTION`）；r6 补齐 Netty 反射元数据后，设备上用户手动点击进入世界停在 `Loading terrain`（`deviceRun: LOADING_TERRAIN_STALLED_USER_REPORTED`）。r7 修复聊天公钥等待后，用户已确认进入世界，日志记录区块解码与持续渲染；存档重进仍待测试，`gateC0` 仍为 `BLOCKED`。

证据根目录 `/Volumes/K/AOTC/builds/version-adaptation-1211/`（外接盘上的 `._*` 是 macOS 元数据，不是构建输入）。

## 状态

| 环节 | 状态 | 依据与边界 |
| --- | --- | --- |
| 官方客户端输入 | PASS | `cache/owned-client/1.21.1/client-inputs.json` 为 `OWNED_CLIENT_INPUTS_VERIFIED`：73 个库、版本元数据、mappings 与 assetIndex 全部按哈希核验。该记录自身声明 `gateA0: BLOCKED`。 |
| 命名空间映射 | PASS | 正式入口 `tools/prepare_game_version.py` 已通过 Fabric 管线执行，结果在确定性缓存 `cache/version-adaptation/1.21.1/remapped/a016fdd0bfd76e5f`：8,269 类，intermediary 未匹配类与成员均为 0。 |
| PREPARED 输入 | PASS（B0 夹具） | `prepared/result.json` 为 `PREPARED`，73 个参考库、7 个引擎库，Loader 0.19.3 与 0.19.5 已验证；`scope` 为 first-party fixture，`gateB0` / `gateB1` 仍 `BLOCKED`。 |
| GL41 客户端补丁 | PASS（构建期） | 早期 `gl41-client/result.json` 含 6 个改动条目，r2 至 r4 为 7 个。r5 源码与 `world-evidence-r5/gl41-client/result.json` 为 8 个，新增 `JvmProfiler.class` 的 JFR 选择修复；隔离 JVM 与原生探针均通过。r6 的 `fabric-aot-world-r6/gl41-patch` 结果同为 8 个条目，未再改动补丁集合。 |
| GLFW 原生子集 | PASS（原生层） | `glfw-r2/result.json` 为 `NATIVE_SUBSET_BUILT`，`missingCExports` 为空，`hostStateFixture` 为 `PASS`。 |
| GL41 运行时 | PASS（构建期） | `gl41-runtime-current/result.json` 为 `GL41_RUNTIME_FETCHED`，源码构建的固定 Mesa 25.0.7 与固定 MoltenVK；记录声明设备验收另行进行。 |
| OpenGL JNI shim | PASS（构建期） | `lwjgl-opengl/result.json` 为 `LWJGL_OPENGL_SHIM_BUILT`，`arm64-apple-ios17.0`，2,228 个 native 方法中 1,227 可用。 |
| LWJGL 静态 JNI 桥 | PASS（构建期） | 1.21.1 `lwjgl-r5`（修正 STB）与 26.1.2 `lwjgl-26-regression-r4` 均为 `STATIC_JNI_BRIDGE_BUILT`；26 线宿主 Window/Image 夹具 `PASS`。 |
| Fabric API 导出 | PASS（构建期） | `fabric-aot-r5`、`fabric-aot-startup-r2`、`r3` 与 `r4` 的 fabric 阶段均为 `FABRIC_AOT_INPUT_PREPARED`：50 个模块，`excludedMods` 为空，命名空间 official，Loader 0.19.3。见下节限制。 |
| 原版 AOT | PASS（未执行） | `vanilla-aot-r2`：真实游戏代码生成可重定位对象，`targetExecution` 为 `NOT RUN`，`gateC1` / `gateC2` 为 `NOT RUN`。范围是真实 `net.minecraft.client.main.Main` 可达性，不含游戏执行、认证参数或渲染。 |
| 运行库 / 链接 / 暂存 | PASS（含未支持入口点） | `vanilla-aot-r2` 的 runtime、link、stage 均成功：`arm64-apple-ios17.0`，产物为 Mach-O arm64、`platform IOS`、`minos 17.0`；stage 为 `WORLD_DEVICE_XCODE_INPUT_STAGED`。link 状态是 `LINKED_WITH_UNSUPPORTED_ENTRYPOINTS`，未解析符号为空，`_Java_com_sun_management_internal_OperatingSystemImpl_initialize0` 以 stub 收尾。 |
| Fabric 签名 IPA | 已生成 | 最新为 fabric-aot-world-r7，MC1211-r7-20261122；841,066,436 字节，SHA-256 96ca4b15097d5a47dae96ec41246880d2adda560fadfbd4006297ca088c8569d。游戏镜像 UUID DDA1DFC0-D5AA-3D5F-9CB4-4A54B8A948D6。codesign、设备覆盖、测试标识一致性均 PASS，无动态代码权限。 |
| 设备安装与启动 | PASS | r7 通过同一 bundle org.fabricaot.research.mc1211.kexin 覆盖安装并拉起，记录 world-evidence-r7/device-r7-install.json 与 device-r7-launch.json。保留原数据容器；未卸载应用。 |
| 设备游戏执行 | 已进入世界 | r7 用户确认“进世界了”，run-1795340290282-MC1211-r7-20261122.log 记录 chunkDecoded=609、28 秒 ticks=2270、持续实体包处理与 GL41 渲染。r6 现场公钥等待未复现。存档重进、声音、触摸与持续稳定性尚未验收，gateC0 BLOCKED。 |
| 启动器失败界面 | PASS（模拟器） | XCTest `testLauncherReportsRuntimeFailure` 在 `LauncherFailure-r2.xcresult` 中 1 通过 / 0 失败，覆盖失败文案、重开按钮禁用、超 256 KiB 日志头尾保留与 `Done`。只证明失败被如实呈现。 |
| 日志修复 | 已越过反射与 JMX，设备验证到 GL 层 | r2 越过反射失败、r3 越过 JMX 失败，两段都有设备日志：r3 的 argv 里确实带了 `-Dlog4j2.disable.jmx=true`，日志栈初始化后没有再触发 `OperatingSystemImpl.initialize0`。这只能说明这两处阻断在设备上被绕过，不代表完整格式器在设备上验证通过——r3 在 `GL.<clinit>` 就退出了，没有走到稳定的日志输出。`compiler/reflection-config.json` 的三个无参构造与 7 个 `PatternConverter.newInstance`、`compiler/logging-tools` + `tools/prepare_logging_aot.py` 的哈希守卫静态改写保持不变；关闭 JMX 的做法是 iOS 运行时启动参数，日志库字节码与 AOT 配置都不变。相关源码已随 1.21.1 支持提交。 |
| 隔离日志探针 | PASS（原生） | `r10` 的 `native-run` 与 `native-run-r2` 均 exit 0，拿到真实 core `LoggerContext`，输出 `[main/ERROR] logging-probe: formatted-message`，无 `StatusLogger` 错误；`runtime-control` 为 `CONTROL_PASS`，`sandbox-probe` 为 `B0_SANDBOX_PASS`。证据副本在 `startup-evidence-r2`。 |

## 映射

正式路线由 `prepare_game_version.py` 走 Fabric 管线完成，结果按输入哈希缓存在 `remapped/a016fdd0bfd76e5f`；`clientSHA256` 为 `1f55363a982fb4a93a8c1c323aa5fa378bf3864cdac2c15992270c718ee95d99`。

`remap-manual/` 是同一 `GameRemapper` 的直接调用记录，其 `client.jar`（SHA-256 `cba79a7e…`）与正式缓存逐条目字节一致，仅 ZIP 时间戳不同，故哈希不同。它只作交叉核对，不是产出路径。

两种方式都是 obfuscated → intermediary → official 三命名空间：8,269 类，未匹配 0，输出 official 客户端，以及仅供 Fabric 运行时 Mod remap classpath 使用的 intermediary 客户端。`mappings.tiny` SHA-256 为 `f70b09b40d8cf9a29d9dcee148b9b5367e21c5d8fe8dcef491c84b3b0c5fc781`。intermediary 制品在 `compiler/game-versions.lock.json` 按大小与 SHA-256 固定；Mojang 客户端与 mappings 是用户本地输入，不入库。

## 补丁与原生层

早期 `gl41-client` 产物的补丁改动 `Minecraft`、`SystemReport`、`ClientChunkCache`、`Connection`、`GLX`、`ClientPacketListener` 六个条目，该产物从未装机。r2 至 r4 增加 `net/minecraft/client/main/Main.class` 的启动异常打印，共 7 个条目。r5 增加 `JvmProfiler.class`，共 8 个条目。1.21.1 走原生 legacy OpenGL 路径、uniform buffer 沿用原版分配、Iris 实体属性补丁未应用；这些补丁在构建期改写字节码。

GLFW 子集以 `iphoneos` 编译 `ios/glfw-lite`，`libglfw.dylib` SHA-256 `8cf1a0b3462d7e94d64466990973091aab533c8f0785cb5e6c9dcf5c1095c45e`，渲染器 ABI 为 `GLFW_RENDERER_ABI_VERIFIED`（`NO_API`、`OPENGL_OSMESA`）。`hostStateFixture` 覆盖 char-mods 投递与注销、`glfwWaitEventsTimeout` 正常唤醒与非法值拒绝、跨线程唤醒、按键/鼠标轮询、失焦释放、resize 后拒绝陈旧事件。

`lwjgl-r5` 修正了 STB 部分，生成 1,605 个 JNI 适配器、19 类回调 ABI × 32 槽位，链接的 `libglfw.dylib` 与 `glfw-r2` 产物哈希一致。ABI 与状态夹具在 macOS 宿主验证；r4 另有 iOS 主菜单渲染与用户交互证据，世界内回调和渲染仍待验证。

## 配置生成

桌面构建配置用 `gl41-runtime-current` 的源码构建固定 Mesa 与固定 MoltenVK，加上 `lwjgl-r5`，通过 `versionProfiles` 承载 1.21.1 的输入路径；26.1.2 的根键保持不变。配置的加载与校验由 `mcfao.load_config` 与 `native_dependencies.config_paths` 按版本解析。

## Fabric API 导出

`fabric-aot-r5`、`fabric-aot-startup-r2`、`r3` 与 `r4` 都对 `fabric-api-0.116.18+1.21.1.jar` 完成构建期转换，输出 official 命名空间客户端，50 个模块全部进入导出结果，`excludedMods` 为空。沙箱文件与网络拒绝控制通过，Loader 残渣报告为 `NO_RESIDUE_REPORTED`，但该报告自身声明不构成功能等价证明，`runtimeVerified` 为 false。

限制必须明说：

- 输入仍是单一 Fabric API JAR。早期轮次曾因 `EventFactoryImpl.buildEmptyInvoker` 的 `Proxy.newProxyInstance` 与未审计的 Indigo 插件查询排除模块；这两条路径现已按审计哈希静态收敛，导出覆盖全部 50 个模块。
- 构建期导出不等于功能等价：动态代理与 Indigo 的收敛结果只在构建期验证，尚未在 iOS 上运行。
- 转换报告未包含 MDAF 数据（`policy: REPORT_ONLY`）。
- `fabric-aot-r5`、`fabric-aot-startup-r2`、`r3`、`r4`、`fabric-aot-world-r5` 与 `fabric-aot-world-r6` 都完成了 Native Image 编译并签出 IPA；r2 停在 JMX，r3 停在 `GL.<clinit>`，r4 到达主菜单但创建世界失败于 JFR，r5 到达主菜单但世界未进入，退出于 Netty 反射，r6 到达主菜单但卡在 `Loading terrain`。

## 未覆盖

- r7 已有用户进世界反馈及区块解码、持续渲染日志；用户暂时无法继续操作，保存重进、触摸和声音未测试。此前 r4 的 JFR、r5 的 Netty 反射和 r6 的聊天公钥等待已分别修复，但仍不能认定完整可玩验收。gateC0 保持 BLOCKED。
- 无 1.21.1 与 26.1.2 的运行期行为对照。
- `xcode-sim/` 下只有宿主工程构建的 `FabricAOTWorld.app`（iPhoneSimulator），`Frameworks` 为空，不含游戏镜像。
- 桌面前端 102 个用例通过（13 个文件）、GUI 4 项通过，属于 Builder 自身的界面与桥接测试，不改变上述构建链结论。
- 全量 Python 回归 832 项通过、跳过 12 项（`/tmp/mcfao1211-startup-regression-full-r2.log`）。初次 18 项失败全部来自默认 `prepared` 指向失效缓存，改用真实 26 环境重跑后通过。

## 资产索引修复

`ios/C0Harness/embed_graphics_libraries.sh` 原先只接受 `indexes/30.json`，1.21.1 的 assetIndex 是 `17`，导致 package 阶段在 `xcodebuild` 的 Embed Graphics Libraries 步骤失败。脚本现在读取 `FAOT_ASSET_INDEX`，仅在未提供且版本为 26.1.2 时回退到 `30`，非数字值直接报错；`mcfao` 与 `build_c0_harness` 在构建设置里传入 `FAOT_MINECRAFT_VERSION` 与 `FAOT_ASSET_INDEX`。

`vanilla-aot-r1` 的 `inputs.json` 已固定其输入指纹，其中 `assetIndex` 为 `17`。该轮 package 产物受修复前的脚本影响，不能据它得出打包结论；`vanilla-aot-r2` 在新目录重跑，沿用同一 `testVersion`。

## 设备执行

`MC1211-r1-20261122` 的原版签名探针（日志 `run-1795331832212-MC1211-r1-20261122.log`）镜像与原生库加载正常，虚拟地址探针 256 MiB–4096 MiB 通过、32768 MiB 按预期失败，参数为 `--version 1.21.1 --assetIndex 17`。`Main.main` 随后在 `CrashReport.<clinit>` 处失败：`NoClassDefFoundError: Could not initialize class com.mojang.logging.LogUtils`，而 `CrashReport` 会再次触发 `LogUtils`，原始原因被覆盖，`run_main` 返回 1。

### 隔离日志探针

用 1.21.1 实际依赖的日志组合做隔离探针（`com.mojang:logging` 1.2.7、`log4j-slf4j2-impl` 2.22.1、SLF4J 2.0.9、Log4j 2.22.1），把根因拆成三层：

- 同一组合在 JVM 上 `LOGGING_PROBE_PASS`，原生 AOT 则复现 `NoSuchMethodException: DefaultFlowMessageFactory.<init>()`，调用链为 `LogUtils.<clinit>` → `MarkerFactory.<clinit>` → `Log4jMarkerFactory.<clinit>` → `StatusLogger.<clinit>` → `LoaderUtil.newInstanceOf`。缺的是反射元数据，不是日志库版本。
- 补上构造器后服务查找仍回退到 `SimpleLoggerContext`：Log4j 2.22.1 的 `ServiceLoaderUtil.callServiceLoader` 用 `LambdaMetafactory` 动态生成工厂，Native Image 下不可用。
- 拿到真实 core 上下文后又报 `Unrecognized conversion specifier [level]/[logger]/[msg]/[n]`，因为格式插件（`PatternConverter.newInstance` 与 `Log4j2Plugins.dat`）同样依赖构建期元数据与资源。

对应三处修复：`compiler/reflection-config.json` 补 `DefaultFlowMessageFactory`、`ParameterizedMessageFactory` 与 `core.impl.Log4jContextFactory` 的无参构造和 7 个 `PatternConverter.newInstance`（`tests/test_logging_reflection.py` 固定）；新增 `compiler/logging-tools` 与 `tools/prepare_logging_aot.py`，按类名、方法签名和 `LambdaMetafactory.metafactory` 出现次数做形状守卫，把 `callServiceLoader` 改写为直接 `ServiceLoader.load` 并保留 `Throwable` 捕获与 `StatusLogger` 报错，由 `probe_client_graph` 在需要 remap 的版本接入（26.1.2 标记 `NOT_REQUIRED`）。`r10` 探针两次 `exitCode` 0，输出 `15:55:58 [main/ERROR] logging-probe: formatted-message`、`context=org.apache.logging.log4j.core.LoggerContext`，`stderr` 只剩 `setrlimit` 提示；同轮 `runtime-control` 为 `CONTROL_PASS`、`sandbox-probe` 为 `B0_SANDBOX_PASS`。证据副本在 `startup-evidence-r2`。

### r2 设备执行

`MC1211-r2-20261122` 带着上述修复装机执行，日志 `startup-evidence-r2/device-r2-first-check/run-1795334711193-MC1211-r2-20261122.log`。镜像与六个原生库的 UUID 全部记录、虚拟地址探针全部正常，`run_main argc=26`，`Main.main` 进入 `GameLoadTimesEvent.<clinit>` → `LogUtils.getLogger`，没有再出现此前的反射初始化错误。这只能说明 r2 越过了反射失败这一段；日志里没有插件或 `StatusLogger` 报错，不足以证明完整格式器在设备上验证通过——r2 根本没走到输出格式化就 abort 了。

失败点换到 `LoggerContext.start`：默认配置启用 JMX，`Server.reregisterMBeansAfterReconfigure` → `ManagementSupport.getPlatformMBeanServer` → `OperatingSystemImpl.<clinit>` 走到 `Java_com_sun_management_internal_OperatingSystemImpl_initialize0`。该符号在链接期只有显式 unsupported stub，运行时打印 `unsupported JVM native entrypoint reached` 并 `abort`，`FATAL signal 6`。

因此 r2 的 `deviceRun` 是启动失败，交付记录写作 `STARTUP_FAILED_SIGABRT`，失败原因为 `Log4j JMX -> OperatingSystemImpl.initialize0 unsupported JVM native`。

源码 `PatchDevice1211` 的 `Main.class` 补丁会在 `CrashReport` 再次初始化前打印原始 `Throwable`；r2 的 `gl41-patch` 已含这 7 条，但本次没有走到 `CrashReport`，日志里也就没有该输出。

启动器侧的失败处理另有独立证据：模拟器 XCTest `testLauncherReportsRuntimeFailure` 在 `LauncherFailure-r2.xcresult` 中 1 通过 / 0 失败，覆盖「启动失败：Java 入口已返回 1」文案、重开按钮禁用、超 256 KiB 日志的头尾保留与 `Done`。它只证明失败能被如实呈现。

### r3 修复：运行时启动参数关闭 JMX

关闭 JMX 的做法是 **iOS 运行时启动参数**，不是构建期静态改写：`ios/C0Harness/main.m` 在构造 `run_main` 的 argv 时无条件加入 `-Dlog4j2.disable.jmx=true`，位置在 LWJGL 属性之后、语言/地区属性之前，两条渲染线共用同一份参数表。日志库字节码、反射配置与 AOT 配置都不因此改变，被去掉的只是 Log4j 默认开启的 JMX 管理面，正常日志输出保留。

配套校验都是第一方夹具或隔离探针，不含游戏代码：

- `tests/fixtures/launcher/run_main_failure.c` 在 argv 中找不到该属性时打印 `MCFAO_JMX_ARGUMENT_MISSING` 并返回 2，找到则打印 `MCFAO_JMX_DISABLED_ARGUMENT_PASS`。
- `ios/C0Harness/WorldUITests.m` 的 `testLauncherReportsRuntimeFailure` 断言日志视图中出现 `MCFAO_JMX_DISABLED_ARGUMENT_PASS`，把「参数确实转发到 `run_main`」变成模拟器上的可见断言，同时仍校验失败文案、重开按钮禁用与超 256 KiB 日志头尾保留。
- `interop/diagnostics/LoggingStartupProbe.java` 读该属性后仍要求真实 core `LoggerContext`、`%d{HH:mm:ss} [%thread/%level] %logger: %msg%n` 格式化输出、以及 `MBeanServerFactory.findMBeanServer(null)` 保持为空（含 `Server.reregisterMBeansAfterReconfigure` 调用之后）。

`startup-evidence-r3/verification.json` 记录本轮结果：官方日志组合在 JVM 上 `loggingJvm: PASS`，macOS Native Image 探针 `loggingNative: PASS`，`runtime-control` 为 `CONTROL_PASS`、`sandbox-probe` 为 `B0_SANDBOX_PASS`，日志相关 Python 回归 6 项 0 失败（`mcfao1211-jmx-logging-regression-r3.log`），启动器 XCTest 1 项 0 失败且 `actualForwardedArgument` 为 `PASS`（`LauncherJmx.xcresult`）。宿主回归 33 项、26.1.2 官方日志组合 JVM 回归 1 项也通过。原生探针首次退出 0 并输出成功标记，但报告脚本误把含 logger/context 后缀的成功行当成完整独立行匹配，导致报告失败；修正断言后重跑通过，原始记录保留在 `startup-evidence-r3`。

这些证据的边界：JVM 与 macOS Native Image 是宿主环境，XCTest 是模拟器，本身都不等于 iOS 设备上 Log4j 不再进入 JMX；设备侧结论来自下一节的实测日志。

### r3 设备执行

`MC1211-r3-20261122` 用 iOS 运行时启动参数关闭 JMX 后装机执行，日志 `startup-evidence-r3/device-r3-first-check/run-1795335629200-MC1211-r3-20261122.log`。argv 共 27 项，其中确实带着 `-Dlog4j2.disable.jmx=true`（r2 是 26 项、不带该参数）。`startup-delivery-verification.json` 已按本次实测更新为 `deviceRun: STARTUP_FAILED_OPENGL_BOOTSTRAP`、`gameButton: USER_TAPPED`，并指向本节日志。镜像与六个原生库 UUID 全部记录，虚拟地址探针 256 MiB–4096 MiB 通过、32768 MiB 按预期失败。

这一步越过了 r2 的阻断：日志栈初始化后没有再触发 `Server.reregisterMBeansAfterReconfigure` → `OperatingSystemImpl.initialize0`，也没有出现 JMX 相关的 abort。日志里同样没有 `StatusLogger` 报错，但 r3 在 GL 初始化处就退出了，仍未走到稳定的日志输出，因此这不构成格式器在设备上的验证。

游戏继续推进到图形层，并且是第一次在设备上拿到真实 GL 上下文（下面的 `first render size` 是初始化时的渲染尺寸，不是渲染成功后的首帧）：

```
[GL41] OSMesa context created; zink over MoltenVK
[GL41] first render size 1627x1097
[GL41] GL_VERSION=4.1 (Compatibility Profile) Mesa 25.0.7
[GL41] GL_RENDERER=zink Vulkan 1.4(Apple A20 Pro GPU (MOLTENVK))
```

zink 日志同时记录了 MoltenVK 1.4.3 / Vulkan 1.4.357、A20 Pro GPU、Metal 4 与 66 个设备扩展，另有一条警告：所选 Vulkan 设备不支持 zink 的基础要求 `feats.features.logicOp` 与 `have_EXT_custom_border_color`。

新的失败点在 LWJGL 的 GL 入口：`com.mojang.blaze3d.platform.Window.<init>` 抛 `ExceptionInInitializerError`，根因是 `java.lang.IllegalStateException at org.lwjgl.opengl.GL.create(GL.java:110)`，由 `GL.<clinit>(GL.java:85)` 触发。`Minecraft.<init>` 因此中断，游戏写出崩溃报告后退出。崩溃报告自身记录 `Backend API: Unknown`、`GL Caps: Using framebuffer using OpenGL 3.2`，说明原生 GL 上下文已建立但 LWJGL 侧的函数绑定/能力探测没有走通。

因此 r3 的结论是：越过反射与 JMX 两段，GL41/Zink 原生上下文成功，卡在 `GL.<clinit>`，仍未到菜单或世界（r4 才进入主菜单）。

`fabric-aot-startup-r3/startup-delivery-verification.json` 现在记的是本次实测结果，不再是装机前的占位：`deviceRun: STARTUP_FAILED_OPENGL_BOOTSTRAP`、`gameButton: USER_TAPPED`、`installation` / `appLaunch` 为 `PASS`、`gateC0: BLOCKED`，并带 `deviceLog` 指向本节日志。r3 已被 r4 取代，其记录保留作失败样本。

## r4 交付（GL 启动修复已进入包）

r3 的设备失败点在 LWJGL 的 GL 入口：`GL.<clinit>` 调 `GL.create()` 时抛 `IllegalStateException`（GL.java:110）。r4 在构建期把这一步绑到包内的 iOS shim，而不是在运行期绕过检查。它已在设备上生效：菜单渲染正常，fps 采样连续。

实现由三部分组成，都是对字节码的定点改写：

- `tools/prepare_opengl_aot.py`：只在输入确实是 `lwjgl-opengl-3.3.3.jar`、且其中 `org/lwjgl/opengl/GL.class` 的 SHA-256 精确等于 `840202709ce57a1f54f9f4a0369dca1434576db430f0e9c1b6d4f940e65bb99c` 时继续；哈希不符直接报错要求重新审计，含签名的 JAR 直接拒绝。输出新的 JAR，其余条目逐字节保留。
- `compiler/gl41/tools/fabricaot/gl41/PatchOpenGLStartup.java`：用 ASM 树 API 定位唯一的 `public static void create()`，清空其指令与异常表后改写为 `create("liblwjgl_opengl.dylib")`。方法签名、平台标记与签名库目录限制都不动，只替换这一步的库选择。
- `tools/probe_client_graph.py`：在需要 remap 的版本且 `--graphics-bridge` + `--renderer gl41` 时调用上述准备步骤，把结果记进 AOT 输入的 `openglAdaptation`。26.1.2 不满足 `profile.requires_remapping`，因此这条路径不触发，仍走原有 `GL.create()`。

`startup-evidence-r4/opengl-startup-result.json` 记录当前证据：`unpatched` 为 `REPRODUCED_DEVICE_FAILURE`（原始类在隔离 JVM 上复现 r3 相同的 GL.java:110 异常），`patched` 为 `PASS`，`sandbox` 为 `B0_SANDBOX_PASS`；`adaptation` 的 `status` 为 `LWJGL_OPENGL_STARTUP_PREPARED`、`changedEntries` 只有 `org/lwjgl/opengl/GL.class`、`scope` 为 static iOS library selection, no library initialization。

r4 的完整构建已结束：`fabric-aot-startup-r4/result.json` 为 `SIGNED_DEVICE_APP`，AOT 结果里 `openglAdaptation` 正是 `LWJGL_OPENGL_STARTUP_PREPARED`、`changedEntries` 只有 `org/lwjgl/opengl/GL.class`；IPA 为 `NCL-game-MC1211-r4-20261122.ipa`（841,102,227 字节，SHA-256 `74442c717b066fae5bc5279f81785dc68795794dd33ed9eafe437dadf8b5669d`），`codesign` / `provisionedDevice` / `testVersionConsistency` 均为 `PASS`，`devicectl` 安装与应用拉起均成功（`startup-evidence-r4/device-r4-install.json`、`device-r4-launch.json`）。

边界同样要说清：隔离 JVM 对照里用的是第一方 GL 函数夹具，它只证明改写后的类能完成初始化、函数查找、JNI 调用、拒绝重复初始化与销毁重建，不代表真实图形上下文、着色器、世界渲染或设备行为。r4 的交付记录为 `deviceRun: MAIN_MENU_REACHED_WORLD_CREATION_FAILED`、`gameButton: USER_TAPPED`、`menu: USER_CONFIRMED`、`world: CREATION_FAILED_JFR`。用户手动点击启动后进入了主菜单，创建世界失败；细节见下节。`GL.create()` 的定点改写在设备上解决了 r3 的 GL 启动失败。

## r4 设备执行（主菜单到达，创建世界失败）

用户在 r4 上手动点击启动游戏，日志 `startup-evidence-r4/device-r4-menu-world-check/run-1795336497817-MC1211-r4-20261122.log`。这一轮首次进入主菜单（用户确认）：GL41/Zink 上下文正常，随后持续输出性能采样，`perf fps=36.3 … 54.8`、`display fps=57.1 / 52.8`，说明菜单确实在渲染而不是只初始化完就退出。这同时说明 r4 的 `GL.create()` 定点改写解决了 r3 卡在 `GL.<clinit>` 的问题。

创建世界没有成功，根因已由日志确认在 JFR。服务器线程在加载世界时抛出 `java.lang.InternalError: Flight Recorder is not supported on this VM`，调用链为 `jdk.jfr.internal.JVMSupport.ensureWithInternalError` → `jdk.jfr.EventType.getEventType` → `net.minecraft.util.profiling.jfr.event.WorldLoadFinishedEvent.<clinit>` → `JfrProfiler.onWorldLoadedStarted` → `MinecraftServer.loadLevel` → `IntegratedServer.initServer`。也就是说，1.21.1 在开始加载世界时会初始化 JFR 事件类型，而这个 Native Image 的 VM 不支持 JFR；这与 r4 的 GL 启动改写无关，菜单能正常渲染已经说明那一步生效。

随后 `MinecraftServer.stopServer` 在 `saveAllChunks` 抛 `NullPointerException`，说明世界加载失败后没有可保存的区块数据，服务器收尾路径没有做好空值处理。

同一份日志末尾还有一条启动器侧的 `NSInvalidArgumentException`：`-[FAOTMetalView showGameMenu]: unrecognized selector sent to instance`，`libc++abi` 随后终止进程，末行是 `[FAOT] FATAL signal 6 (Abort trap: 6)`。它属于 iOS 启动器视图的回调问题，由另一条分支处理，不在本页的 JFR / 世界加载范围内；本页只把它记为同一份日志里的第二条失败，不在此处判断成因。

需要保留的证据边界：进入主菜单是用户实测确认的，日志里的 fps 采样与菜单渲染一致；但世界从未生成，因此没有世界、存档、多人或长期稳定性证据。两份记录都已按本次实测更新为 `deviceRun: MAIN_MENU_REACHED_WORLD_CREATION_FAILED`；`gateC0` 保持 `BLOCKED`，不能因为到达菜单就认为 Gate C 通过。

## r5 设备执行（世界连接失败于 Netty 反射）

r4 的世界创建失败点已定位到 JFR，不是渲染。1.21.1 在 `MinecraftServer.loadLevel` 起始处经 `JfrProfiler.onWorldLoadedStarted` 初始化 `WorldLoadFinishedEvent`，其 `<clinit>` 调 `jdk.jfr.EventType.getEventType`，在 Native Image 上抛 `InternalError: Flight Recorder is not supported on this VM`。

r5 的修复放在构建期，复用游戏自身已有的实现：`compiler/gl41/tools/fabricaot/gl41/PatchDevice1211.java` 的 `withoutFlightRecorder` 先校验 `net/minecraft/util/profiling/jfr/JvmProfiler.class` 的 SHA-256 精确等于 `05bc243d0c863f1c6762f18697366f721d7b3eeeecdb58be9d9f8871b0b72da7`，不符就报错要求重新审计；通过后把 `JvmProfiler.<clinit>` 的指令整体替换为构造并赋值 `JvmProfiler$NoOpProfiler`，即选择 Minecraft 自带的空实现，不新增任何自定义 profiler 代码。该哈希在 mapped client 与 split client 两条输入上一致，因此同一条改写对两者都成立。`tools/patch_gl41_client.py` 把这一步纳入 1.21.1 的 GL41 客户端补丁，期望条目里已含 `net/minecraft/util/profiling/jfr/JvmProfiler.class`，并在结果里记 `flightRecorder: "Minecraft NoOpProfiler; iOS AOT has no JFR recorder"`。

隔离对照已经跑完，正反两面都在 `world-evidence-r5/result.json`：`jvm: PASS`（改写后的真实 `gl41-client/client.jar` 走 `NoOpProfiler`，输出 `WORLD_PROFILER_PASS`），`native: PASS`（同一探针经 macOS Native Image 编译后输出 `WORLD_PROFILER_PASS`），`originalJvm: JFR_SELECTED`（未改写的原始类仍选中 `JfrProfiler`，断言 `Wrong profiler: net.minecraft.util.profiling.jfr.JfrProfiler` 失败退出），`sandbox: B0_SANDBOX_PASS`（外部写入与网络连接均被拒绝）。探针本身是 `interop/diagnostics/WorldProfilerProbe.java`，它只调用真实的 `JvmProfiler.INSTANCE` 钩子：断言是 `NoOpProfiler`、`isAvailable`/`isRunning`/`start` 都为假、`onWorldLoadedStarted` 返回可 finish 的时长、`onChunkGenerate` 返回空、tick 与收发/区域文件钩子不抛异常、对未运行的 recorder 调 `stop` 抛 `IllegalStateException`。证据自述 `scope` 为 actual Minecraft NoOpProfiler hooks, no world execution——它证明的是改写选对了实现、这些钩子在 `NoOpProfiler` 下可安全调用，不是世界真的能创建。

这只解决世界加载阶段的 JFR 初始化；日志里另一条 `-[FAOTMetalView showGameMenu]` 的 `NSInvalidArgumentException` 属于启动器视图回调，由另一条分支处理，本页不覆盖、也不声称已解决。

r5 完整构建、签名、安装和应用拉起均通过，测试标识为 `MC1211-r5-20261122`，产物 `/Volumes/K/AOTC/builds/version-adaptation-1211/fabric-aot-world-r5/NCL-game-MC1211-r5-20261122.ipa`，841,099,987 字节，`client-graph.dylib` UUID `E3253FE4-F73E-3851-805B-62EE32C28DED`，SHA-256 `59d29f76056d85a4fb5c970863e81aae1f4e4bc2d649455e8a9b0754992780ae`。包内标识与 IPA 文件名一致，签名与设备覆盖验证通过。用户实测仍闪退。日志 `world-evidence-r5/device-r5-world-check/run-1795337349403-MC1211-r5-20261122.log` 的退出路径为世界打开 → `ServerConnectionListener.startMemoryChannel` → `NioEventLoopGroup` → shaded JCTools `BaseMpscLinkedArrayQueueProducerFields.<clinit>` → `UnsafeAccess.fieldOffset`，根因是 `NoSuchFieldException: producerIndex`。本轮失败已记为 `WORLD_CONNECTION_FAILED_NETTY_REFLECTION`，世界尚未进入，`gateC0` 保持 `BLOCKED`。这次日志没有复现 r4 的 JFR 异常，但 Netty 在客户端连接阶段先退出，尚不能据此认定整个世界加载路径已通过。

## r6 Netty 本地通道修复

`compiler/reflection-config.json` 按实际 Netty 4.1.97 shaded JCTools 字节码补充 9 个字段的条件反射与 `allowUnsafeAccess`。补齐队列后，原生探针暴露第二处元数据缺失：`ResourceLeakDetector.addExclusions` 查询 `AbstractByteBufAllocator` 的 `toLeakAwareBuffer` 失败。按实际调用方为 `AbstractByteBufAllocator`、`AdvancedLeakAwareByteBuf`、`ReferenceCountUtil` 补充条件 `queryAllDeclaredMethods`，保留 Unsafe 路径与泄漏检测。

证据目录为 `/Volumes/K/AOTC/builds/version-adaptation-1211/world-evidence-r6/`，打包与交付校验记录在 `/Volumes/K/AOTC/builds/version-adaptation-1211/fabric-aot-world-r6/startup-delivery-verification.json`。`world-evidence-r6/netty-verification.json` 记录：原生阴性对照复现设备的 `NoSuchFieldException: producerIndex`；最终修复在隔离 JVM 与 macOS Native Image 上均输出 `NETTY_LOCAL_CHANNEL_PASS`，覆盖实际 MPSC 队列、缓冲分配器、`NioEventLoopGroup`、本地通道 ByteBuf 往返和线程退出。Seatbelt 外部读写与网络拒绝对照通过。`tests/test_netty_reflection.py` 的 3 项测试通过，静态审计真实 JAR 字节码并检查合并后元数据保留；完整 r6 AOT 输入也核对了全部 12 个条目。宿主探针不构成 iOS 世界运行证据。

`MC1211-r6-20261122` 已构建、签名、安装并启动，测试标识与包内 `FAOTTestVersion` 一致。产物 `/Volumes/K/AOTC/builds/version-adaptation-1211/fabric-aot-world-r6/NCL-game-MC1211-r6-20261122.ipa`，840,890,542 字节，SHA-256 `01853622086592d4257adea9e8e915cad8cc5acd4d3076f3d20eee02ac2f9702`，`client-graph.dylib` UUID `0D6F3DC1-0BD1-380B-8857-FCE139A0B354`；`codesign`、`provisionedDevice`、`testVersionConsistency` 为 `PASS`，`dynamicCodeEntitlements` 为 `ABSENT`，profile 2026-11-29 到期。`devicectl` 安装与拉起均成功（`world-evidence-r6/device-r6-install.json`、`device-r6-launch.json`）。

设备上由用户手动进入主菜单并点击进入世界，界面停在 `Loading terrain`，未进入世界，也没有存档证据。日志 `world-evidence-r6/device-r6-terrain-check/run-1795338728131-MC1211-r6-20261122.log` 记录了真实 GL41/Zink 上下文（Mesa 25.0.7 over MoltenVK，`GL_VERSION` 4.1）、`perf fps=47.4`、`display fps=43.1`，并且没有再出现 r5 的 `NoSuchFieldException: producerIndex`。这轮启动日志未记录具体异常；随后现场线程栈定位了下文的聊天公钥等待。卡在加载界面不能算世界加载通过。本轮状态记为 `deviceRun: LOADING_TERRAIN_STALLED_USER_REPORTED`、`world: NOT_ENTERED`、`gateC0: BLOCKED`。

宿主侧证据只覆盖 Netty 反射与本地通道，不等于 iOS 世界运行：阴性对照复现设备异常，修复后在隔离 JVM 与 macOS Native Image 上完成 MPSC 队列、缓冲分配器、`NioEventLoopGroup` 与本地通道 ByteBuf 往返。

后续保留卡住现场，通过 Native Image 已启用的 SIGQUIT 诊断取得完整 Java 线程栈：`world-evidence-r6/r6-terrain-thread-dump.log`。这份新证据定位了上述启动日志未覆盖的阻塞：渲染线程在 `ClientPacketListener.enforcesSecureChat` → `Minecraft.canValidateProfileKeys` → `YggdrasilServicesKeyInfo` → `CompletableFuture.join` 等待聊天公钥，本地服务器线程仍处于正常 tick 等待。1.21.1 原实现先查询公钥，再判断 `serverEnforcesSecureChat`。r7 已在构建期为未启用安全聊天的标志与空会话添加提前判断，保留需要时的原签名校验。r7 已完成签名、安装与世界进入验证，详见下文 r7 记录；存档重进等可玩验收仍为 BLOCKED。

## 本轮范围

本页覆盖 1.21.1 的构建期链路、原版与 Fabric 的签名 IPA 生成、`MC1211-r4-20261122` 至 `MC1211-r6-20261122` 的装机与设备执行、隔离日志探针的根因定位与正向验证、r3 的 JMX 运行时启动参数修复、r4 的 GL 启动修复与其签名 IPA、r5 的 JFR 修复、隔离探针与装机后 Netty 反射失败证据，以及启动器失败界面的模拟器 XCTest。边界各不相同：签名 IPA 只证明构建与签名，`devicectl` 安装只证明系统接受了包，隔离探针只证明日志栈在原生 AOT 下能初始化到 core 上下文并正确格式化（`r10` 两次 exit 0、无 `StatusLogger` 报错），启动器 XCTest 只证明失败能被如实呈现。设备侧从 r2 推进到 r6：r2 到 `Main.main` 后停在 iOS 未实现的 JMX 入口；r3 用 iOS 运行时启动参数关闭 JMX，越过这一段并建立真实的 GL41/Zink 上下文（Mesa 25.0.7 over MoltenVK，`GL_VERSION` 4.1），随后在 `org.lwjgl.opengl.GL.<clinit>` 的 `IllegalStateException` 退出；r4 针对该入口做构建期定点改写，签出、安装、启动后进入主菜单，创建世界失败在 JFR；r5 针对 JFR 选择 Minecraft 自带的 `NoOpProfiler`，隔离 JVM 与 macOS Native Image 探针均已通过；装机后在世界连接阶段因 Netty 的 `producerIndex` 反射缺失退出。r6 按实际 Netty 4.1.97 字节码补齐 9 个 JCTools Unsafe 字段与 3 个 `ResourceLeakDetector` 方法查询，隔离 JVM 与 macOS Native Image 本地通道探针、3 项自动化测试均已通过，并装机启动；设备上不再因 `producerIndex` 闪退，但用户手动测试停在 `Loading terrain`，世界未进入。宿主 JVM、macOS Native Image、6 项日志回归与模拟器 XCTest 另行通过（`startup-evidence-r3`）。最新包 r6 到达主菜单但卡在 `Loading terrain`（`LOADING_TERRAIN_STALLED_USER_REPORTED`），`gateC0` 保持 `BLOCKED`。

## 复现命令

路径按本机实际替换。第 1 步是正式映射入口：`--output` 传的是调用方记录目录，真正的 jar 与结果记录写在按输入哈希确定的缓存目录 `remapped/<key>` 里，两个调用方共用同一份产物。

```sh
JAVA=/Volumes/K/AOTC/cache/toolchains/graalvm-25.0.2/graalvm-community-openjdk-25.0.2+10.1/Contents/Home
OUT=/Volumes/K/AOTC/builds/version-adaptation-1211
CLIENT=/Volumes/K/AOTC/cache/version-adaptation/1.21.1
# 正式映射的确定性缓存根；本轮命中的 key 为 a016fdd0bfd76e5f。
CACHE=/Volumes/K/AOTC/cache/version-adaptation/1.21.1/remapped
# 该 key 下的 client.jar / intermediary-client.jar / mappings.tiny 与 result.json。
REMAPPED="$CACHE/a016fdd0bfd76e5f"
# 已构建的 GL41 运行时产物目录，提供 libOSMesa.8.dylib 与 libMoltenVK.dylib。
GL41_RUNTIME="$OUT/gl41-runtime-current"

# 1) 映射（正式入口；要求真实 PREPARED 清单）。--output 只落调用方记录，jar 在 $REMAPPED。
python3 -B tools/prepare_game_version.py --prepared "$OUT/prepared" \
  --client "$CLIENT/1.21.1.jar" --mappings "$CLIENT/client.txt" --output "$OUT/remap"

# 2) GL41 客户端补丁
python3 -B tools/patch_gl41_client.py --version 1.21.1 \
  --client "$REMAPPED/client.jar" --output "$OUT/gl41-client" --graalvm-home "$JAVA" \
  --asm-jars /Volumes/K/AOTC/cache/owned-client/26.1.2/meta/libraries/engine/asm-9.10.1.jar \
             /Volumes/K/AOTC/cache/owned-client/26.1.2/meta/libraries/engine/asm-tree-9.10.1.jar

# 3) GLFW 原生子集（含宿主状态夹具）
python3 -B tools/build_glfw_lite.py --sdk iphoneos --test-host \
  --client "$CLIENT/1.21.1.jar" --output "$OUT/glfw-r2"

# 4) OpenGL JNI shim
python3 -B tools/build_lwjgl_opengl_ios.py --sdk iphoneos \
  --prepared "$OUT/prepared" --minecraft-version 1.21.1 \
  --lwjgl-opengl /Volumes/K/AOTC/cache/owned-client/1.21.1/meta/libraries/org/lwjgl/lwjgl-opengl/3.3.3/lwjgl-opengl-3.3.3.jar \
  --java-home "$JAVA" --osmesa "$GL41_RUNTIME/libOSMesa.8.dylib" --output "$OUT/lwjgl-opengl"

# 5) LWJGL 静态 JNI 桥（1.21.1 使用 3.3.3 源码树 src-3.3.3）
python3 -B tools/build_lwjgl_lite.py --sdk iphoneos --test-host \
  --prepared "$OUT/prepared" --minecraft-version 1.21.1 \
  --glfw-library "$OUT/glfw-r2/libglfw.dylib" --output "$OUT/lwjgl-r5"
```

## 配置与工作台接入

版本事实由 `tools/game_versions.py` 单点定义。`mcfao.load_config(path, renderer, version)` 只合并所选版本的 `versionProfiles` 覆盖项，非默认版本必须显式声明条目；`native_dependencies.py`、`desktop_workbench.py`、`prepare_environment.py` 的预检、重建、任务记录与生成配置都按版本取路径。这些是代码契约与单元测试范围，不代表上述构建链已跑通。

## 相关文档

- [测试矩阵](TEST_MATRIX.md)
- [语义契约](FABRIC_SEMANTIC_CONTRACT.md)
- [资源闭包](RESOURCE_CLOSURE.md)
- [Java 平台适配](IOS_PLATFORM_ADAPTERS.md)

## r7 聊天公钥等待修复与世界进入

修复位于 PatchDevice1211.avoidUnneededChatKeyWait：仅在安全聊天标志为 false 时提前返回，在会话数据为空时提前清理会话；非空会话原有签名校验和异常处理保留。PlayDiagnostics 改写原始 stderr 文件描述符，避免游戏 Bootstrap 重定向吞掉诊断。4 项聊天回归全部通过，包括原实现等待对照、true/non-null 校验保留、结构变化拒绝，以及真实客户端静态字节码审计；实际客户端没有在宿主测试中执行。

MC1211-r7-20261122 已覆盖安装到同一测试 app。用户确认进入世界，设备日志 world-evidence-r7/device-r7-world-entered/run-1795340290282-MC1211-r7-20261122.log 显示 609 个区块解码，28 秒时 2270 次渲染 tick，并持续处理实体包。世界进入状态记录为 WORLD_ENTERED_USER_REPORTED_LOG_CORROBORATED。短窗口 fps 包含菜单和世界切换，不构成 Gate E 性能验收。

证据根目录 /Volumes/K/AOTC/builds/version-adaptation-1211/world-evidence-r7/，签名 IPA 在 fabric-aot-world-r7/NCL-game-MC1211-r7-20261122.ipa。设备保存重进、触摸和声音因用户暂时无法测试而保持 NOT_RUN_USER_UNAVAILABLE；Gate C 与整合包可玩验收仍 BLOCKED。当前仅 Fabric API 50 模块链路，不能宣称任意整合包已兼容。

桌面版本资源闭包已增加 game_versions.py，复制实际 Tauri 资源到隔离目录的预检测试 3 项通过。原生桌面预检增加 Python -B 参数，避免导入模块时在已签名 app 内写入字节码缓存；Rust 预检回归 2 项通过。

原生桌面包已完成构建，分支快照版本为 0.2.0，路径 artifacts/desktop-builds/desktop/ncl-builder-0.2.0-1795340591666/NCL Builder.app.zip，SHA-256 为 323626bced6f6632b4f3a0c28830b65e3e812ffda3577994e0f1bc62aff43bcd。直接使用最终 app 包内资源核验：1.21.1 / 26.1.2 均识别成功，1.20.1 返回拒绝状态；预检后未生成缓存，严格签名校验 PASS。记录见 /Volumes/K/AOTC/builds/version-adaptation-1211/gui-version-evidence-r7/packaged-desktop-verification.json。原生 GUI 手动点击尚未验证。

