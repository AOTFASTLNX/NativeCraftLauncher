# 存储管理页面验证

2026-11-17。界面入口为侧边栏「存储管理」，包含容量条、磁盘筛选、分类占用、最大直接子项、Finder 定位、分组清理明细和存储位置。

- `light.png`、`dark.png`、`narrow-dark.png`：浏览器界面截图，使用 IPC 测试夹具数据。
- `live-scan-summary.json`：本机实际后端只读扫描快照；本次未清理用户数据。
- `verification.json`：检查项和验证边界。清理删除仅在临时目录验证；Finder 调用在浏览器中检查 IPC 参数，Rust 验证路径约束，未执行原生 Finder UI 验收。本次未打包新 App。
- `storage-blank-fix.png`（2026-11-22）：修复后使用「仅占用统计」快照的页面截图，含清理计划单独加载时的状态。

2026-11-22 修复：`storage-snapshot` 只返回占用数据（`usage`、`volumes`、`managedBytes` 等），不含 `groups`、`reclaimBytes`、`blocked`；旧页面在渲染时直接读取 `plan.groups`，抛错后整棵 React 树被卸载，存储管理页只剩背景。现在清理计划由单独的 `storage-plan` 请求加载，占用统计先渲染；清理请求失败只在「空间清理」显示错误。新增 `PageBoundary` 兜住页面级渲染异常，避免再次整页白屏。

复现命令（仓库根）：

```sh
python3 -B -m unittest discover -s tests -p 'test_storage_inventory.py'
python3 -B -m unittest discover -s tests -p 'test_cleanup_storage.py'
MCFAO_STORAGE_CONFIG=/private/tmp/ncl-storage-page-tests/storage.json python3 -B -m unittest discover -s tests -p 'test_desktop_workbench.py'
CARGO_NET_OFFLINE=true cargo test --manifest-path desktop/src-tauri/Cargo.toml --lib
```

在 `desktop/` 下执行 `npm run build`、`npm test`、`npm run test:e2e`。浏览器测试需要本机端口；工作台取消进程用例需要 ps 权限。原生编译前已通过外接盘守卫及空间预检。

本机历史目录扫描耗时约三分钟，数据代表扫描完成时的快照。扫描占用按已分配字节统计，APFS 共享块/快照和文件变化会影响与磁盘容量的比较。可清理空间按互不重叠的候选规则计算，不能直接使用目录总占用替代。
