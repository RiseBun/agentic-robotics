# Persistent CaP Runtime

默认后端为真实 CaP-X，复用现有 Python 环境和感知服务；不在此仓库下载模型或安装仿真器。

```bash
export CAP_RUNTIME_TOKEN='a-long-random-token-at-least-16-characters'
export MUJOCO_GL=egl JAX_PLATFORMS=cpu XLA_PYTHON_CLIENT_PREALLOCATE=false
python -m agentic_cap.server --upstream /absolute/path/to/cap-x --records /absolute/path/to/records
```

先启动 CaP 的 SAM3、Contact GraspNet、Pyroki 服务。与默认 CaP API 地址保持一致：127.0.0.1 的 8114、8115、8116 端口。启用 PyTorch 确定性评测时，服务启动前设置 `CUBLAS_WORKSPACE_CONFIG=:4096:8`。

控制线程是仿真唯一所有者。原阻塞 API 通过传感器 facade 提交目标；每段使用独立的 IK 配置和可取消上下文。策略不接触原始环境、真值、文件或网络。

`goto_pose` 现在区分 IK 解到达与实际 TCP 到达：最多三次求解，解的 FK 满足 1 mm / 0.5° 后才提交关节目标；实际编码器 FK 满足 3 mm / 1° 且关节到位后才返回 `status: reached`，同时返回请求／实际位姿、误差、控制步和时间。不可达或跟踪超时抛出结构化运动失败，结束当前段。`home_pose` 仍按关节目标验收，`last_motion.scope` 用于区分两者。运动到位不是抓持、放置成功或碰撞安全证明；原物体验证阈值不变。详见 `docs/cartesian-motion-plan.md`。

正常结束代码段后先确认关节保持，再等待已发布的动作后观测窗口就绪，最长 3 秒。窗口完整即交给原验证器判断，不等待到成功；遮挡、缺失、超时和检查异常仍为 unknown。等待不阻塞仿真，计入累计墙钟和 idle_steps，可被停止、换目标或预算耗尽打断。内部保持不会把正常执行标记为用户取消。

候选技能显式启用：`CAP_ACTIVE_EVIDENCE=1` 提供持有物体时的 `inspect_support`，`CAP_PLACEMENT_EVIDENCE=1` 提供释放并撤离后的 `inspect_placement`，两项默认关闭。两组共用动作后的新鲜支撑物检查，不能用下降前的旧帧批准释放；公开验证窗口不超过其引用帧。后者仅在当前方块场景用空机械臂侧移解除中心相机遮挡，最多两次，证据仍不足即 unknown，不代表通用相机规划或真机碰撞保证。详见项目 `docs/placement-verification.md`。

`--evaluation` 仅添加本地 private scorer 文件，不向 observe、events 或模型上下文发送真值。当前评测持有使用双指接触与初始高度差，堆叠使用物体接触及相对位置。它不是硬件接触传感器。

fixture 服务只需标准库；完整测试还包含公开 RGB-D 几何与坐标边界检查，需要 NumPy、SciPy：

```bash
python -m pip install numpy scipy
python -m unittest discover -s tests -v
CAP_RUNTIME_TOKEN='test-token-at-least-16-chars' python -m agentic_cap.server --backend fixture
```

fixture 不能用于报告机器人成功率。`tests/real_smoke.py` 测真实控制循环、停止和同环境继续；`tests/real_tasks.py` 测固定短代码；Slurm 文件是本次服务器的工程试验配置，不是产品部署或 SSH 功能。

每个任务保存 episode.json、events.jsonl、公开 PNG/NPZ 证据，可选 evaluation-private.jsonl。服务重启保持同一 records 目录；旧任务标为 interrupted，不重建物理场景或重放代码。默认一次只允许一个活动环境。

导出 ZIP 直接含 JSON/JSONL，图像与深度位于内部 observations.tar.xz。再次解压即可获得标准 PNG/NPZ 文件；像素与数组无损，仅改变压缩编码以共享等待帧的重复数据。界面图像读取限时 15 秒，完整记录下载单独限时 600 秒。

夹持反馈含公开估计的物体中心／TCP 偏移，只适用于保持当前夹爪姿态的平移放置。公开堆叠验证沿用原阈值；对满足平面与先前尺寸条件的方块侧面使用几何中心估计，证据不足仍为 unknown。最终 table 支撑关系尚未校准，工作台需先澄清此类目标。

公开状态与 NPZ 标明 CaP 基座坐标、米／弧度及 WXYZ 四元数顺序；启用私有评测时另存实际世界到基座变换，仅供离线评分。`experiments/` 下三面方块中心估计仍为离线候选，不影响在线夹持偏移或验证阈值。诊断范围与拒绝样本见 `docs/geometry-experiment.md`。
