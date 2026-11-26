# 通用桌面实验模式

本模式在 Thor 的 `/home/li/projects/agentic-robotics` 开发，保留旧方块任务。

## 执行职责

- S2：embuddies 单主模型提出目标、动态计划、技能、短代码、取证和恢复。
- Harness：公开事实、证据来源、任务版本、节点依赖、预算和三值验收。
- S1：公开 RGB-D / SAM3 身份与当前图像跟踪、GraspNet 和测量几何候选、ASPIRE/cuRobo 规划、受限动作。
- S0：唯一物理控制线程；感知、模型和规划请求不拥有仿真步进权。
- Monitor：停止、换目标、超时和公开持有证据反驳；不是硬件急停认证。
- 离线经验：proposed → verified → library；验证种子 301/302；默认关闭。

## 接口

`GET /system` 返回公开机器人、相机、坐标约定、对象目录和区域配置。

`POST /episodes` 新模式示例：

```json
{"request_id":"unique-id","scene":"tabletop_v1","seed":201,"goal":{"all":[{"predicate":"on","object":"block_a","support":"platform"},{"predicate":"empty_gripper"}]}}
```

`tabletop_stacked_v1` 是另一种初始布局，目标仍独立提供。限制为 24 decisions / 80 calls / 3000 motion steps / 600 seconds；累计不重置。

`POST /episodes/{id}/plan`：

```json
{"request_id":"plan-id","revision":1,"plan":{"nodes":[{"id":"pick","skill":{"name":"acquire_object","arguments":{"object":"block_a"}},"depends_on":[]},{"id":"put","skill":{"name":"place_object","arguments":{"object":"block_a","support":"platform"}},"depends_on":["pick"]}]}}
```

节点不接受模型提供的前后置条件。其后通过原 `skills` 或绑定节点的 `segments` 接口执行。参考状态与对象依赖必须在派发时重新检查。

`GET /episodes/{id}/system` 获取描述与提供器状态；`GET /episodes/{id}/experiences` 只返回已发布历史技能建议，不提供导入、验证或发布权限。原有停止、目标变更、事件、验收、导出接口保持不变。

支持目标谓词：holding、on、in_region、empty_gripper 的合取。用户不可通过目标声明 visible 或 geometry 已成立。

## 依赖与标定

ASPIRE 固定 fa00f0e，cuRobo 固定 a35a708，详见依赖清单。上游仓库未修改。ASPIRE/cuRobo 均保留原许可证与 NOTICE。MAESTRO 的扫描/感知规划组织方式、EMOS 的描述/适配方式、TAMPAgent 的证据约束和 ENPIRE 的经验工作流用于设计参考；未把它们整个运行框架复制入产品。

现有 Torch 2.13.0+cu132、MuJoCo 3.5.0 和旧 Runtime 环境保持原版本。cuRobo 在 `.venv-curobo`，通过 HTTP 服务隔离。CUDA_PATH / CUDA_HOME 必须指向该 Thor 已安装的 `nvidia/cu13` 头文件根目录，否则内核运行时编译会报 NVRTC 目录缺失。

机器人 mounting、GraspNet gripper-base → TCP 和 TCP → planner hand 的变换均属于设备适配标定，不能当作任意机器人的通用常量。当前桥接器只实现此仿真 Panda。

RGB-D 跟踪只在身份和外观可区分时提供证据；语义分割、当前几何和时间分别记录。几何规则只适用于目前声明的直立刚体与平面支撑；尚不构成通用碰撞安全或任意语言目标的验证器。

## 测试与评测

开发 201/202，候选验证 301/302，冻结测试 701/702/703。`tabletop_campaign` 定义六类目标、三个条件及 54 次清单；必须先通过 A/B/C 和独立技能验证，不能直接启动冻结试验。

协议 fixture 的完成只证明 API 和状态管理，不计入物理成功。物理真值仅写入 `evaluation-private.jsonl`，在线工具不读取；独立评分要求所有相关任务已关闭。

实际结果、失败及尚未完成的阶段见 `research/generic_robotics_20261124/`。没有通过验收的阶段不得写成已完成或自动切换为默认模式。

## 当前规划范围

cuRobo 桥接返回的是观测点云与机器人碰撞规划结果，尚无挂载物体碰撞模型；遮挡未观测区域也不等于自由空间。因此不能将 collision_aware 字段描述为完整搬运安全认证。物理与模型测试仍按阶段门槛验收。

## 命令与阶段门槛

依赖安装使用现有 Thor Python 3.12 环境：

```sh
.venv/bin/python scripts/linux/install-generic-planner.py
```

安装脚本只注册 cuRobo 服务，不自动启动它，也不更改旧 Runtime/工作台的默认模式。现有手工开发服务使用 8118 时，必须先结束它再启动同端口的托管服务。

实际模型开发测试：

```sh
~/.local/share/agentic-robotics/tools/node/bin/node runtime/scripts/tabletop-model-trial.mjs --output research/new-tabletop-development --task support --seed 201
```

开发命令只允许种子 201/202。`--condition direct_cap` 使用无 S2 任务图/经验的短代码路径；其操作仍绑定同一注册条件和效果，不因此开放任意机器人权限。`system_with_memory` 必须显式提供已验证、冻结的技能目录。

独立技能验证：

```sh
cd cap-runtime
../.venv/bin/python -m experiments.tabletop_skill_validation --library /absolute/candidate-library --candidate CANDIDATE_ID --bindings '{"arg1":"block_a","arg2":"platform"}' --records /absolute/fresh-validation-records
```

这一步只运行保留的 301/302，不发布技能。源 Runtime/验证契约不一致或物理、公开检查任一失败，都不能晋升。候选可能只是复用已有技能的建议，不等于学习到了新控制能力；实际收益必须通过对照检验。

`experiments.tabletop_campaign` 只有在 A/B/C 和 D_validation 均带有通过证据、技能已发布且模型/Runtime/技能目录已冻结时，才创建并运行 54 次评测。没有满足门槛时，不允许用开发成功样例冒充冻结报告。

## 通用恢复

`restore_grip_pose(object)` 将原方块 Runtime 的记录路径恢复思想用于通用对象。只有当前目标版本、编码器终点、夹爪命令和相关场景版本仍匹配时，才允许反向执行已完成的下降路径。它不释放物体，也不证明重新获得了持有；执行后仍须检查公开证据。

## 本轮验收状态

完整实现记录、失败原因和阶段门槛见 [实现与验收](../research/generic_robotics_20261124/IMPLEMENTATION_STATUS.md)。该文档记录最初实施阶段；后续已完成六类最低闭环覆盖与 54 次冻结对照。最新结论以以下冻结报告为准，通用模式和经验仍不默认开启。

最新后续进展见 [继续推进与验收](../research/generic_robotics_followup_20261124/PROGRESS.md)。

本轮正式54次对照已完成：[冻结结果与负结果](../research/generic_robotics_followup_20261124/FORMAL_RESULTS.md)。三组公开确认均16/18，未观察到经验收益；经验仍默认关闭。

## 多视角候选（2026-11-26）

开发 CLI 可显式增加 `--perception multiview`，默认仍为 `single_view`。候选合并同次采集、时间与标定一致的主相机/腕部相机公开表面；不一致或不可用时保留 unknown，不放宽验证阈值。该选项进入运行指纹及经验契约；旧经验不能未经重新验证直接沿用。

6 次开发运行的物理与公开验收均通过，但单视角对照同样成功，尚未证明收益。详见[开发结果与未完成项](../research/geometry_evidence_20261125/RESULTS.md)。

## 测量证据契约（开发候选，2026-11-26）

当前图像与异步几何测量有各自的时间。`image_ref` / 顶层 `evidence_ref` 表示本次相机采集；每个对象的 `evidence_source` 表示实际参与测量的相机帧、任务 ID、控制步、坐标系和采集时间。`geometry_robot_state` 对应几何源帧的 TCP/夹爪状态，不得用最新机器人状态替代它。

真实通用桌面快照声明 `geometry_evidence_schema: 1` 后，缺失来源、任务/坐标/相机/位姿配对不一致、陈旧或动作前证据均不能授权当前几何动作。只有同步 fixture 保留旧格式兼容；它的测试结果不算物理成功。

持有与放置时间窗按独立的实际采集包去重。重复发布同一几何不能形成稳定性证据；放置关系还要求支撑物有独立窗口。最少 3 次、跨度 0.6 秒、最大年龄 1 秒的阈值保持不变。

规划需要动作后的新几何时，S1 最多等待 1.5 秒；等待经过原取消/版本/预算检查，S0 持续推进。超时返回未知，不重新派发旧动作。相关事件为 `geometry_wait_started` / `geometry_wait_finished`。效果为 unknown 时，S1 可在原预算内最多额外等待 2.5 秒获取完整窗口；相关事件为 `public_effect_wait_started` / `public_effect_wait_finished`，超时仍返回 unknown。

原始主相机和腕部 NPZ 均保存采集包时间、相机时间、关节、TCP、夹爪、任务与控制步。几何补全使用的尺寸先验记录其原始证据来源。该变更使旧经验契约失效，须重新验证；经验默认关闭，生产服务未切换。开发实验及失败见 `research/evidence_contract_20261126/`。

## 跨计划的恢复准入（开发候选，2026-11-26）

通用模式将 acquire_object、place_object、place_in_region 的失败按实际技能参数与恢复条件统计。相同条件下最多两次失败，之后节点标记 blocked 并返回 retry_constraint。重新命名计划、换代码、改描述、新时间戳及单纯撤回均不清零；技能、绑定代码及直接 CaP 路径共用此限制。

放置操作的条件包括当前用户目标版本、目标/支撑绑定、支撑物版本、最近一次已验证抓取及相关对象真正执行的有效主动取证。搬运物体自身位置变化和无关物体移动不解除放置限制。被动观察、失败或仍 unknown 的取证不清零。

Runtime 将拒绝重复动作并记录 recovery_dispatch_blocked。模型可以选择相关主动取证、其他受支持策略或请求帮助；强行提交被阻止的操作时，工作台请求保持确认后进入等待用户状态。此机制不保证新视角一定解决失败，也不证明产生了更好的恢复策略；它只阻止已知无变化条件下的重复物理派发。目标变更仍由原用户输入流程管理，模型不得自行换用户目标绕过限制。

## 实际 RGB-D 缺失诊断（2026-11-26）

单相机备用路径在主视角完全丢失时也尝试有效腕部测量；缺失深度仅在有限值 provider 边界编码为无测量零值，原始记录和公开投影不补造深度。释放前复核 holding 与当前 support_geometry / region。实际像素丢失实验仍有最终验收 unknown，不能引用 fixture 恢复成功作为物理能力。见[诊断报告](../research/rgbd_occlusion_20261126/RESULTS.md)。

## 语义重识别与当前几何（2026-11-26）

重识别失败或新分割外观不一致时，保留之前确认的对象身份和原身份时间；不沿用旧 mask/几何。当前腕部或主相机仍需重新提供不歧义的像素深度。身份替换复用当前外观跟踪的 0.10 距离及 0.025 歧义间隔，不放宽关系验收阈值。实际像素故障已恢复物体几何，但支撑物/最终关系仍未知，见[开发记录](../research/identity_retention_20261126/RESULTS.md)。

## 公开图像覆盖候选（实验，默认关闭）

开发 CLI 可增加 `--inspection coverage`，默认 `lateral`。coverage 只在 S2 调用已注册 inspect_object 后选择 S1 候选：以已观测位置/尺寸作为瞄准先验，使用当前腕部标定投影、FOV 和当前深度缺失区域排序有限平移，不直接承诺未来可见性。姿态保持不变；空夹爪要求经公开验证，持有时仍保持 X/Z。每次最多尝试两个候选，全部经过 cuRobo、取消、目标版本及原预算检查。

空夹爪候选相对首次检查锚点 X/Y 各不超过 8cm，Z 为原高度或抬高 6cm；世界范围限制 X/Y±0.35m、Z 0.95–1.25m。访问记录按厘米格去重，锚点不随编码器漂移更新。低位持有会返回需要记录路径撤回的原因，不隐式执行恢复。规划失败也消耗两个候选尝试额度，实际证据仍由原 verifier 判断。

声明固定的支撑物只关联其先前公开测量包围盒及 2cm 外扩内的当前像素，过滤在最大连通区域选择前执行，以免机器人同色部件替代支撑物。该窗口是新增的保守身份关联限制，未放宽关系验收；越界或缺失保持 unknown，历史包围盒不作为当前成功证据。

开发中的固定图像丢失任务仍没有全部恢复完成；候选不会默认部署，见[候选与负结果](../research/inspection_coverage_20261126/RESULTS.md)。没有真机或完整碰撞安全结论。
