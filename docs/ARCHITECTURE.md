# 技术架构与关键事实

> 2026-11-15 集成决策：主要研发渲染器采用 ADR-009：原生 Blaze3D OpenGL → LWJGL OpenGL JNI → OSMesa/Mesa Zink → MoltenVK → Apple Metal → UIKit CAMetalLayer。原生 Java MetalBackend 为可选独立路径；GL41 的 Metal 呈现层与该后端不同。desktop/ 继续使用 Tauri 2/React/TypeScript/Rust，完整构建须调用与 CLI 相同的本地 AOT 后端。


## 已确认的基本事实

- 两条并行线的版本事实由 `tools/game_versions.py` 单点定义：**26.1.2 / Java 25**（默认，发行即 official 未混淆命名空间，无需 remap）与 **1.21.1 / Java 21**（发行即混淆，构建期必须完成 obfuscated → intermediary → official 映射）。
- Fabric Loader 支持编织/转换类，包括 Mixin；常规的运行时加载机制与 AOT 静态闭包存在实质冲突。
- GraalVM Native Image 的 closed-world 假设要求动态类/资源/反射在构建时尽可能可知。仅增加 reflection-config 并不能解决任意 ASM 动态 defineClass。
- Gluon 项目有 iOS Native Image 技术路线，但 **Java 25 + 本项目真实代码 + 当前 iOS 工具链是否可用需要单独实证**。
- Minecraft 桌面 LWJGL/OpenGL 不能假定直接在 iOS Metal 中执行；必须验证具体 GL 特性并实现语义兼容。优先研究有源码、有许可的绑定/渲染技术，不得假称已兼容。

## 已识别但尚未实现的关键约束

- 26.1 需按 **official/unobfuscated** Minecraft 和对应 Fabric Loader 版本处理，不可以默认复用旧 Intermediary/Yarn 的 JAR。该条只约束 26.1：1.21.1 的运行时 Mod remap classpath 明确需要 Fabric intermediary，其制品在 `compiler/game-versions.lock.json` 按大小与 SHA-256 固定，且绝不作为运行时游戏 JAR。Mod 适配须核验 Fabric API 与 Class Tweaker。
- 两条线的客户端特定输入通过配置的 `versionProfiles` 分开声明。`mcfao.load_config(path, renderer, version)` 只合并所选版本的覆盖项，非默认版本必须显式声明条目；`native_dependencies.config_paths`、`desktop_workbench.py` 的预检/重建/任务记录与 `prepare_environment.py` 的生成配置同样按版本解析。版本差异不得靠改写根键或复制整份配置来绕过。
- 签名 IPA 的生成、安装与设备执行是三个独立结论。1.21.1 的 `MC1211-r4-20261122`（`SIGNED_DEVICE_APP`，50 个模块零排除，841,102,227 字节，SHA-256 `74442c71…`）已签出并由 `devicectl` 安装与拉起；设备上进入主菜单并持续渲染（`perf fps` 36–55），但创建世界失败，没有世界或存档证据。更早的原版探针停在 `com.mojang.logging.LogUtils` 初始化，`run_main` 返回 1。AOT 链接、打包、签名、安装、启动、菜单、世界必须逐级分别取证。
- 反射元数据缺失会在 Native Image 里表现为初始化失败，且可能被后续初始化覆盖原始原因。1.21.1 的 `LogUtils` 失败已用隔离探针复现为 `DefaultFlowMessageFactory.<init>()` 与 `ParameterizedMessageFactory.<init>()` 的 `NoSuchMethodException`：JVM 通过、原生 AOT 失败，说明问题在镜像元数据而非日志库版本。补元数据必须同时保留原始异常输出，否则设备日志仍只能看到表层 `NoClassDefFoundError`。
- 只补反射元数据不够：Log4j 2.22.1 的 `ServiceLoaderUtil.callServiceLoader` 依赖 `LambdaMetafactory` 动态生成工厂，Native Image 下不可用，服务查找会静默回退到 `SimpleLoggerContext`。`compiler/logging-tools` 按类名、方法签名与 `LambdaMetafactory.metafactory` 出现次数做形状守卫，把它改写为直接 `ServiceLoader.load` 并保留 `Throwable` 捕获与 `StatusLogger` 报错；`tools/prepare_logging_aot.py` 由 `probe_client_graph` 在需要 remap 的版本接入。除构造器与服务查找外，日志格式插件（`PatternConverter.newInstance` 与 `Log4j2Plugins.dat`）同样依赖构建期元数据与资源，缺一项就只剩回退格式。1.21.1 的隔离探针在补齐三类构造器、服务查找改写与 7 个 pattern converter 后，原生 AOT 两次 exit 0 并输出带时间戳与级别的格式化日志，不再有 `StatusLogger` 报错。构建期静态改写只能证明日志栈可初始化，不能替代设备运行。
- 日志栈能初始化到 core 也不等于能在 iOS 上启动：Log4j 默认配置会启用 JMX，`LoggerContext.start` 经 `Server.reregisterMBeansAfterReconfigure` 走到 `java.lang.management`，最终命中 `Java_com_sun_management_internal_OperatingSystemImpl_initialize0`。该符号在链接期只有显式 unsupported stub（`LINKED_WITH_UNSUPPORTED_ENTRYPOINTS`），运行时打印 `unsupported JVM native entrypoint reached` 并 abort。`MC1211-r2-20261122` 的设备日志已实证这条路径。关闭方式选在 iOS 运行时：启动器给 `run_main` 传 `-Dlog4j2.disable.jmx=true`，日志库字节码与 AOT 配置保持不变，正常日志输出保留。unsupported entrypoint 的 stub 会把「未实现」变成进程级崩溃，因此这类平台缺口要在进入该路径之前挡住，而不是指望运行期降级。
- 执行 Mixin 前的加载图解析必须覆盖 `depends/breaks/conflicts/provides`、Entrypoint、Language Adapter、Jar-in-Jar、side stripping 和实际版本选择；正确处理 class initialization build-time vs run-time。
- 需要被完整识别的客户端原生依赖不止 OpenGL：LWJGL/GLFW、OpenAL、STB、JNI、线程、资源/字体/着色器/窗口事件、iOS 应用生命周期。
- 预检只是静态读取；远程 Mod 与真正 Minecraft 客户端获取必须单独有安全下载+哈希核验服务，第三方构建代码必须先在隔离环境处理。
- AOT 后含 Minecraft 代码的 IPA 不得公开再分发；无 Apple Developer Program 的签名/安装必须按 7 天有效期实证。
- 参考 `TECHNICAL_GATES.md`、`SECURITY_AND_DISTRIBUTION.md`、`REVIEW_REPORT.md`，以逐步 Gate 判断是否真实可行。

## 建议流水线

```
.mrpack / Prism Instance
   -> manifest verification + secure asset acquisition
   -> dependency resolution + license-aware local assets
   -> verified Fabric dependency graph + isolated deterministic Mixin / Class Tweaker transformation
   -> AOT reachability / resource / reflection metadata
   -> Java25-compatible native image (iOS arm64)
   -> Cocoa / Swift + Java native interop + LWJGL/GLFW/OpenAL compatibility + Metal graphics bridge
   -> Xcode codesign + IPA
   -> physical iPhone smoke / perf / longevity tests
```

## 必须产生的 ADR

- `ADR-001` Java25 iOS AOT 实际工具链版本、补丁、复现过程（包括失败日志）。
- `ADR-002` 构建期 Fabric Loader / Mixin 的设计与可支持范围，单独明确 Mixin 在加载期修改目标类的语义。
- `ADR-003` Minecraft 26.1.2 LWJGL/OpenGL 适配路线、Metal 渲染方案和测试用例。
- `ADR-004` Microsoft / Minecraft 合法授权流程，资产来源与分发隔离。
- `ADR-005` 用户本地 IPA 签名、free provisioning、bundleID 与更新/存档保留行为。
- `ADR-006` 性能调优方法：baseline vs 优化后数据、设备配置与功耗限制。

## 风险登记（重要）

- **R1 工具链不兼容：** GraalVM 25 社区版 Native Image ≠ 完成 iOS cross compile。需先完成 iPhone 真机 AOT smoke test。
- **R2 类转换：** Mixin 的装配时机与 AOT 需要的静态化可能冲突；构建期转换可能影响 Mod 副作用与初始化顺序。
- **R3 图形：** OpenGL context、着色器、驱动相关行为与 iOS Metal 不等价。
- **R4 许可证：** 不得把 Minecraft 专有 JAR 或未经授权资源上传到公开仓库/发布包。
- **R5 运行时内存：** 高分辨率纹理、Java 堆/Native Image 内存行为、iOS Jetsam，均须在真机测试。

## 参考资料

- https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1
- https://www.minecraft.net/en-us/article/removing-obfuscation-in-java-edition
- https://docs.fabricmc.net/develop/loader/
- https://www.graalvm.org/latest/reference-manual/native-image/metadata/
- https://docs.gluonhq.com/
- https://www.minecraft.net/en-us/usage-guidelines
