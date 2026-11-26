# Linux 板子本机工作台

部署位置：`li@100.80.72.6`，源码在 `/home/li/projects/agentic-robotics`。
板子是 Ubuntu 24.04 / ARM64 / NVIDIA Thor / CUDA 13.2。

## 日常使用

在板子的应用菜单打开 **Agentic Robotics**，或在板子浏览器打开
**http://127.0.0.1:8787**。选择场景，输入方块目标，开始任务。
停止、换目标、事件与代码查看、记录导出沿用同一个工作台。

浏览器、任务编排、MuJoCo 仿真、SAM3、Contact GraspNet 与 PyRoKi 均在板子。
主模型沿用配置的模型 API；这次迁移不包含离线部署主语言模型。
SSH 仅用于这次迁移和维护，日常任务没有 Windows 进程或 SSH 隧道依赖。

## 文件与服务

| 内容 | 板子路径或地址 |
|---|---|
| 源码与 Git 历史 | `/home/li/projects/agentic-robotics` |
| Python 环境 | `/home/li/projects/agentic-robotics/.venv` |
| Node | `/home/li/.local/share/agentic-robotics/tools/node/bin/node` |
| 网页 | `127.0.0.1:8787` |
| CaP Runtime | `127.0.0.1:8120` |
| SAM3 / 抓取 / IK | `127.0.0.1:8114` / `8115` / `8116` |
| 工作台配置与任务 | `/home/li/.local/share/agentic-robotics/data` |
| 物理执行记录 | `/home/li/.local/share/agentic-robotics/robot-records` |
| 服务日志 | `/home/li/.local/share/agentic-robotics/logs` |
| 用户服务 | `/home/li/.config/systemd/user/agentic-*.service` |

服务绑定回环地址；无需打开板子的防火墙端口。在其他电脑访问其 Tailscale IP
不会打开工作台。

Windows 源码及旧用户数据保留；当前未提交的架构改动也已复制。
模型密钥在迁移中解密后经已认证 SSH 输入流重新写入板子的加密存储，
没有复用 Windows 密文。密钥与本机加密 key 均不进入 Git。

## 本机维护

```bash
cd /home/li/projects/agentic-robotics
systemctl --user status agentic-robotics-web agentic-cap-runtime
tail -n 60 ~/.local/share/agentic-robotics/logs/runtime.log
```

修改前端后重建；修改 Python 或编排代码后，先停止并结束任务，再重启对应服务：

```bash
export PATH="$HOME/.local/share/agentic-robotics/tools/node/bin:$PATH"
cd /home/li/projects/agentic-robotics/frontend
npm run build
systemctl --user restart agentic-robotics-web
systemctl --user restart agentic-cap-runtime
```

注册服务用 `python3 scripts/linux/install-user-services.py`。此脚本只启用网页；
真实依赖和三个推理服务验收后再启用 Runtime：

```bash
systemctl --user enable --now agentic-cap-runtime.service
```

Runtime 启动会等待三个本地推理服务，最长 180 秒；失败查看各自日志。
服务重启不重放任务或代码。服务在用户登录后启动。

## ARM64 依赖

没有复制 x86 虚拟环境。保留 CaP-X 原锁文件，单独使用
`scripts/linux/install-simulation.py` 和 `requirements-simulation.txt` 安装当前执行路径，
不安装训练与 x86 专属组件。Thor 先安装 CUDA 对应版本：

```bash
.venv/bin/python -m pip install torch==2.13.0+cu132 torchvision==0.28.0+cu132 \
  --index-url https://download.pytorch.org/whl/cu132
.venv/bin/python scripts/linux/install-simulation.py
```

需要已迁入的 CaP-X、SAM3、Contact GraspNet、PyRoKi、jaxls 与 Panda 描述资源。
SAM3 的 SHA256 必须是
`9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e`。

相对原 IAC 环境的主要版本变化为 PyTorch 2.9.1 → 2.13.0+cu132、
torchvision 0.24.1 → 0.28.0+cu132、Open3D 0.19 → 0.20（ARM64 wheel）。
JAX 0.4.29、MuJoCo 3.5.0、NumPy 1.26.4、SciPy 1.15.3 和 Matplotlib 3.10.8 保持原版本。
pyrender 使用与 IAC 相同的 0.1.45 / PyOpenGL 3.1.6 组合，单独忽略其过时的
PyOpenGL 精确依赖元数据；不声称全量上游依赖满足 `pip check`。

SAM3 的图像推理导入链会导入训练数据类，并意外要求 ARM64 没有 wheel 的
视频解码器 decord。`optional-sam3-video.py` 仅把该导入延迟到实际读取视频的分支；
没有伪造模块、修改权重或替换图像推理。视频训练仍需另装真实 decord。

## 测试边界

迁移初始版本在板子通过 Python **234** 项、Node **97** 项。
前端在板子通过 9 项测试及构建（含 TypeScript 检查）。
这些属于接口与逻辑验证，不代表真实物理任务成功。

`scripts/linux/smoke-browser.mjs` 检查本机浏览器入口；加 `--run-task` 会使用
现有主模型和真实 Runtime 提交一次抬起任务、截图并导出，然后关闭场景。
执行前要求没有其他活动场景。结果保存在 `.tmp/linux-browser-smoke`，
任务失败会如实记录，不改验证阈值。

2026-11-21 板子迁移验收：

| 检查 | 结果 |
|---|---|
| 最终依赖下的逻辑测试 | Python 234、Node 97、前端 9 项通过 |
| 真实浏览器 | 页面和设置可用；无 JavaScript 错误 |
| 任务导出按钮 | 浏览器成功下载约 2.24 MB 的 JSON 任务包，含 Runtime ZIP |
| 真实主模型 + 本地 CaP | 抬起任务公开确认成功；3 次决策、7 次调用、122 个运动步，任务完成时 25.38 秒 |
| 固定程序 / 开发种子 12 | 抬起、堆叠独立物理验收通过；重堆叠未完成、公开 `unknown`，无错误完成声明 |
| 持续仿真 | 三个场景等待约 5 秒，仿真各推进约 5 秒，倍率 0.999 左右 |
| 每场景环境重置 | 各 1 次创建，恢复没有重置 |

这是迁移验收，不是新种子对照或成功率结论。保留第一轮绘图库兼容失败；
固定 Matplotlib 后使用新目录运行第二轮，没有覆盖失败结果。
重堆叠的证据/恢复不足仍属系统能力缺口；迁移没有解决它。

板子原始证据：`.tmp/thor-fixed-v1`、`.tmp/thor-fixed-v2`（含独立评测）、
`.tmp/thor-browser-ui-v1`、`.tmp/thor-browser-task-v1`（主模型流程、截图和导出）、
`.tmp/thor-browser-download-v1`（导出按钮验收）。

## 2026-11-21：迁移后的原生改进

后续修改仅在板子。当前 Python 245、Node 97 项通过；真实模型在开发种子 12 的抬起、普通堆叠通过公开与独立物理验收。重堆叠仍未完成，模型已实测执行放置失败后的授权撤回。新增 Runtime 失败路线约束，禁止用新帧或成功撤回重复同一受阻放置；腕部相机对照未解决遮挡，仍关闭。

详细记录、失败保留及未完成能力见 [原生恢复诊断](../research/native_recovery_20261121/结果与边界.md)。不能将安全求助当作任务完成，也不能从开发种子推断成功率。

## 2026-11-22: selected public geometry

The Runtime now uses the same selected public support geometry for task admission, placement compilation and failed-route context. Python 255 and Node 97 checks pass. The Runtime service has been updated; wrist camera remains an experimental opt-in. Four fixed-controller development regressions and one model follow-up did not complete restacking. The model did retreat and request assistance. Details: [regression report](../research/support_geometry_20261122/results.md).

## 2026-11-22: model image sources and persistent services

The model input now supports coherent recorded primary/wrist/front/agent camera images with explicit source indices and rejected-view reasons. Image downloads obey stop/goal cancellation. Node 103 tests pass. Real-model development regression: ordinary stack physically verified; restack retreated then requested help, still incomplete. See [image-evidence report](../research/scene_images_20261122/results.md).

`loginctl enable-linger li` is enabled on the board so the user services continue when SSH exits or the graphical user is logged out. Before this change, services could restart with each SSH login. All inference ports and services were checked again after reconnecting. This setting does not automatically log into the desktop.

## 2026-11-22: opt-in alternate recovery

An experimental high-clearance held-object yaw skill now supports a different placement attempt after a rejected release and authorized retreat. The final real-model seed12 run completed this chain and passed independent physical evaluation (11 decisions, 36 API calls, no reset). Fixed seed22 reached the physical goal but exhausted the API budget during final skill execution, so the feature remains off by default. Python 265 and Node 104 tests pass. Details: [reorientation evidence](../research/reorientation_20261122/results.md).

## 2026-11-22: recovery call cost and release headroom

The acquire skill skips opening only when the current public sensor already verifies an open empty gripper. The registered place release requires two API calls of headroom for measured state and retreat. Python 269 and Node 104 tests pass. In a fixed-controller development seed22 run, alternate recovery completed its full sequence within 40 calls; real-model lifting and stacking passed, while real-model restacking exhausted 12 decisions and remained incomplete. The source update is deployed, but experimental wrist/alternate recovery remain off by default. [Evidence and limitations](../research/recovery_cost_20261122/results.md).


### 动作结果与场景版本同步发布（2026-11-22）

Runtime 在段结束前原子提交最新公开观测的实质场景变化，记录 result_revision/result_evidence_ref，减少自身动作结果延迟发布导致的无效模型请求。后续场景变化仍使旧请求失效。Python 273、Node 104 项通过，已在关闭生产场景后部署。开发 seed22 真实模型复测：抬起与堆叠公开／独立物理通过；重堆叠 unknown／物理失败，12 决策和 40 API 用尽。三项过期丢弃均为零，不构成成功率或因果提升。详见 [同步发布记录](../research/scene_publication_20261122/results.md)。


### 决策前观测与求助复核（2026-11-22）

新增 `decision_evidence`，在模型预算预留前最多等待 3 秒传感窗覆盖；ready 不代表任务成功，完整但遮挡仍 unknown。ask_user 正式进入求助前复核当前状态，仅在同目标从无合法路线变为有合法路线时重新请求模型，不自动执行。Python 277、Node 116 项通过，已重启 Runtime 与工作台并核对源码。两轮真实模型开发回归完整保留：最终抬起 2 次决策、堆叠 3 次决策，均物理 verified；重堆叠仍在 12 决策／40 API 内失败。详见 [观测就绪记录](../research/decision_evidence_20261122/results.md)。


### 阶段资源预检（2026-11-22）

注册技能和实际生成代码共用 AST 的 API 成本上界分析，包含主动取证子调用和最终释放的测量／撤离余量。预算不足时在首个 API 前拒绝；模型可以看到可负担／不可负担注册路线。运动、耗时和全任务成功不由该估计证明。Python 290、Node 116 项通过。开发 seed22 抬起与堆叠物理 verified；重堆叠剩余 4 API、放置需 6 时主动求助，没有第三次下降，任务仍 failed。求助提示词另行修正固定预算约束，历史公开状态的模型复查无动作。已重启工作台和 Runtime，语义契约 v9 纳入 program_resources.py。详见 [资源预检记录](../research/stage_resources_20261122/results.md)。


### 恢复与完成路径的联合预算（2026-11-22）

新增 resources.completion_paths，组合当前恢复及后续注册阶段的条件API成本，不授权或排队未来动作。priority_restore_routes区分已记录下降撤回与可选目标进展；低位取证准入与技能内部撤回检查一致。Python302、Node116项通过。真实模型第二轮：重堆叠seed12失败后撤回／调整方向／再次放置，公开和物理通过；seed22仍失败，先撤回后在5 API不足后续9 API时求助；抬起和堆叠通过。已核对部署，方向恢复默认关闭。见[联合预算记录](../research/continuation_resources_20261122/results.md)。


### 释放视角诊断（2026-11-23，仅研究脚本）

对照现有四台标定相机及两个开发种子的预释放位置，没有找到可直接替换的视角；种子22主＋agent/front公开点云融合仍unknown。支撑物45°转动的两项诊断均在IK残差检查失败，未执行旋转；四个第二轮任务全部关闭，目标红块未尝试释放。相关17项测试通过。生产策略与服务未改动、不宣称完成率提升。下一步设计是只读候选动作的可见性／IK联合评估。详见[释放视角诊断](../research/release_visibility_20261123/results.md)。
