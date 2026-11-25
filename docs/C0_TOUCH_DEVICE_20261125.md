# TouchController 触控：iPhone 真机验证记录

测试标识：`recollection3-touch-input-r16-20261125`
日期：2026-11-25
设备：iPhone（`iPhone19,4`，UDID `00008160-001A78620A600036`），GPU `Apple A20 Pro`
包：`org.fabricaot.research.c0.kexin` 0.1 (build 8)，renderer `gl41`

## 结论

用户在 iPhone 上运行该构建并确认可玩。设备自身日志记录了两条独立运行：

| 运行 | 触控桥 | 进入世界 | 段错误 |
| --- | --- | --- | --- |
| `run-joined-world.log` | `host installed` | `Dev joined the game` | 无 |
| `run-menu.log` | `host installed` | — | 无 |

日志由 App 自身把 stdout/stderr 重定向到 `Documents/c0-logs/` 后产生，经 `devicectl` 从应用容器原样取回，未做编辑。

## 关键日志行

入口点与 Mod 加载：

```
[MCFAO_ENTRYPOINT] mod=touchcontroller-26-1-fabric stage=client class=top.fifthlight.touchcontroller.fabric.v26_1.TouchController
Loading TouchController…
Exporting TouchController config schema
Reading TouchController config file
```

原生桥安装（此前的修复若失效，这一行不会出现）：

```
[TouchBridge] host installed protocol=alpha17 frame-snapshot-v1
```

进入世界：

```
[Server thread/INFO]: Preparing spawn area: 16%
[Server thread/INFO]: Dev joined the game
```

## 归档位置

原始日志与哈希保存在本机 `artifacts/evidence/c0-touch-r16-iphone-20261125/`。该目录按仓库 `.gitignore` 约定不入库（生成证据不提交），此处记录其哈希以便复核：

```
4221b4ada2bc2d52df4bf604dc9d2bc3b03c4682d7dc4cd6c2c86e95d5c0c644  run-joined-world.log
6f1ff1aecd4e818c43ab90671ae1943d2d91e501852368baa039048b278d748f  run-menu.log
```

## 边界

本记录证明：触控桥在真机上完成安装、Mod 配置可读、并进入了世界。它不包含逐帧输入采样，因此对手感的判断来自用户的直接体验反馈，而非自动化测量。性能、长时间稳定性与 Gate E 的帧时间统计仍属未测范围。
